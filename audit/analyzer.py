"""스캔 원본 데이터 → 항목 20/21/22 판정 + 상세 리스트 생성."""
from __future__ import annotations

from datetime import date, datetime, timedelta

# 항목 20① 판정(욕구사정 상세). 최상단에서 import 해 모듈이 없으면 스캔 시작 '전' 에 터지게 한다
# — 45분짜리 지점 스캔이 끝난 뒤 분석 단계에서 죽으면 원인 파악도, 재실행 비용도 커진다.
from .item20 import judge as judge20


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y.%m.%d").date()


def _fmt(d: date) -> str:
    return d.strftime("%Y.%m.%d")


def enroll_periods(evts: list[dict], today: str) -> list[tuple[str, str]]:
    periods, open_ = [], None
    # 같은 날짜면 개시(급여개시일/수급중)를 퇴소보다 먼저 처리 — 안 그러면 개시==퇴소 동일일
    # 재입소 케이스에서 옛 퇴소가 무시돼 기간이 통째로 이어붙는 phantom 발생
    for e in sorted(evts or [], key=lambda x: (_d(x["d"]), 0 if x["k"] in ("급여개시일", "수급중") else 1)):
        if e["k"] in ("급여개시일", "수급중"):
            if open_ is None:
                open_ = e["d"]
        elif e["k"] == "퇴소" and open_ is not None:
            periods.append((open_, e["d"]))
            open_ = None
    if open_ is not None:
        periods.append((open_, today))
    return periods


def find_gaps(cover: list[tuple[str, str]], s: str, e: str) -> list[str]:
    ivs = sorted([(_d(a), _d(b)) for a, b in cover])
    merged: list[list[date]] = []
    one = timedelta(days=1)
    for a, b in ivs:
        if merged and a <= merged[-1][1] + one:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    S, E = _d(s), _d(e)
    gaps, cur = [], S
    for a, b in merged:
        if b < S or a > E:
            continue
        if a > cur:
            gaps.append((cur, min(a - one, E)))
        cur = max(cur, b + one)
    if cur <= E:
        gaps.append((cur, E))
    return [f"{_fmt(a)}~{_fmt(b)}" for a, b in gaps if a <= b]


# 연1회 정기 욕구사정: 재적기간 내 사정 공백이 이 일수를 넘으면 '미실시' 후보.
# 365(연1회) + 30(grace) = 395. rolling 13개월 기준(사용자 확정 2026-07-20: 경계선도 노출·주의).
YEARLY_GAP_DAYS = 395


def yearly_needs_miss(results: list[dict], cutoff: str, today: str) -> dict:
    """욕구사정 연1회 실시 판정 — raw 1-1 스캔만으로 계산.

    재적기간 = enroll_periods(급여개시~퇴소, '수급중'=재개 포함) ∩ [평가기간, 오늘].
    그 구간 안에서 사정(needs 일자) 간격이 YEARLY_GAP_DAYS 를 넘으면 그 구간을 미실시로 본다
    (재적시작→첫 사정, 사정 사이, 마지막 사정→재적종료 모두 검사, grace 30일).
    ★raw 사람 객체 단위라 needs_full 조인이 없어 동명이인 문제도 없다.
    반환: {'judged': N, 'miss_cur': [(name, [gap..])], 'miss_toe': [...]}  (수급중/퇴소 분리)
    """
    cut_d, today_d = _d(cutoff), _d(today)
    grace = timedelta(days=30)
    judged = 0
    miss_cur: list[tuple[str, list[str]]] = []
    miss_toe: list[tuple[str, list[str]]] = []
    for p in results:
        if p.get("err"):
            continue
        spans = []
        for ps, pe in enroll_periods(p.get("enroll"), today):
            s, e = max(_d(ps), cut_d), min(_d(pe), today_d)
            if s <= e:
                spans.append((s, e))
        if not spans:
            continue  # 평가기간 내 재적 없음 → 대상 아님(평가기간 전 퇴소자 등)
        judged += 1
        asm = sorted(_d(n["date"]) for n in (p.get("needs") or []) if n.get("date"))
        gaps: list[str] = []
        for s, e in spans:
            inside = [x for x in asm if s - grace <= x <= e + grace]
            pts = [s] + inside + [e]
            for i in range(len(pts) - 1):
                dd = (pts[i + 1] - pts[i]).days
                if dd > YEARLY_GAP_DAYS:
                    gaps.append(f"{_fmt(pts[i])}~{_fmt(pts[i + 1])}({dd}일)")
        if gaps:
            (miss_toe if p.get("status") == "퇴소" else miss_cur).append((p["name"], gaps))
    return {"judged": judged, "miss_cur": miss_cur, "miss_toe": miss_toe}


def analyze(results: list[dict], cutoff: str, branch_name: str | None = None) -> dict:
    """스캔 결과 전체 분석. 반환: dict(대조리스트/연간점검/계획문제/항목판정)

    branch_name: 항목 20① 판정에 필요(audit_results/needs_full_<지점>.json 을 읽는다).
                 없으면 20① 은 '주의(미수집)' 로 남는다 — 조용히 양호가 되지 않는다.
    """
    today = _fmt(date.today())
    rows_match = []      # 낙상↔욕구 대조
    rows_check = []      # 수급기간/연간작성/계약/계획
    plan_issues = []
    halfyear_miss = []   # 항목 21: 반기별 누락
    order_issues = []    # 항목 22①: 계획 작성일이 기초평가(위험도·욕구사정)보다 앞선 위반
    rehab_miss = []      # 항목 27①: 2026~ 계획서 기능회복훈련 미기재(확정)
    rehab_warn = []      # 항목 27①: 기본동작 세부 누락 등 확인요망
    rehab_checked = 0    # 판정 가능한 2026~ 계획 수
    rehab_total = 0      # 2026~ 계획 전체 수 (커버리지 분모)
    rehab_nodata = 0     # 팝업실패·캡처잘림·구버전raw 등 판정 불가 건

    cut_d = _d(cutoff)
    cur_year = date.today().year

    for p in results:
        if p.get("err"):
            rows_check.append([p["name"], p.get("status", ""), "", "", "", "", "오류: " + p["err"], "", ""])
            continue
        periods = enroll_periods(p.get("enroll"), today)
        in_scope = any(_d(e) >= cut_d for _, e in periods)

        # ---- 낙상↔욕구 대조 ----
        falls = sorted([f for f in p.get("falls", []) if _d(f["date"]) >= cut_d], key=lambda x: _d(x["date"]))
        needs = sorted(p.get("needs", []), key=lambda x: _d(x["date"]))
        for f in falls:
            eff = None
            for nn in needs:
                if _d(nn["date"]) <= _d(f["date"]):
                    eff = nn
            if eff is None:
                eff = next((nn for nn in needs if _d(nn["date"]) > _d(f["date"])), None)
            a, g = f.get("a", -9), f.get("g", -9)
            # 낙상평가 '활동' 점수는 최저가 1점(보조기구 없음 = 자립)이며 0점은 존재하지 않는다.
            # (실측: 활동 1/3/4점만 관측) → 기존 a<1 조건은 절대 성립하지 않아 자립 케이스가
            # 통째로 '불일치'로 뒤집혔음. a<=1 을 '낙상평가상 자립'으로 판정한다.
            if a < 0:
                verdict = "확인필요(낙상평가 수집실패)"
            elif eff is None:
                verdict = "욕구사정없음"
            elif eff["sit"] == "?" or eff["tr"] == "?":
                verdict = "확인필요(미체크)"
            elif a <= 1:
                if eff["sit"] != "완전자립" and eff["tr"] != "완전자립":
                    verdict = "확인(낙상 자립인데 욕구는 도움)"
                else:
                    verdict = "일치"
            elif eff["sit"] == "완전자립" or eff["tr"] == "완전자립":
                verdict = "불일치"
            else:
                verdict = "일치"
            rows_match.append([
                p["name"], p.get("status", ""), f["date"], a, g,
                eff["date"] if eff else "",
                (eff["sit"] if eff["sit"] != "?" else "미체크") if eff else "",
                (eff["tr"] if eff["tr"] != "?" else "미체크") if eff else "",
                verdict,
            ])

        period_txt = " / ".join(f"{s}~{'' if e == today else e}" for s, e in periods)
        if not in_scope:
            rows_check.append([p["name"], p.get("status", ""), period_txt, "기간외", "기간외", "기간외", "기간외", "기간외", "기간외"])
            continue

        # ---- 항목 21: 낙상/욕창/인지 (2026~ 반기별 1회 / 2024~25 연 1회) ----
        # 매뉴얼: "'반기별 1회'는 2026.1월부터 적용" — 이전 연도를 반기로 보면 허위 누락 급증
        evals = p.get("evals", {})
        # 퇴소자는 케어포가 각 기간을 '퇴소/해당없음'으로 표시(미작성 대상 아님) → 21번 누락 제외
        periods_21 = periods if p.get("status") != "퇴소" else []
        for s, e in periods_21:
            if _d(e) < cut_d:
                continue
            y0 = max(_d(s).year, cut_d.year)
            for y in range(y0, cur_year + 1):
                if y >= 2026:
                    spans = {"상반기": (date(y, 1, 1), date(y, 6, 30)), "하반기": (date(y, 7, 1), date(y, 12, 31))}
                else:
                    spans = {"연간": (date(y, 1, 1), date(y, 12, 31))}
                cyc = 183 if y >= 2026 else 365  # 재평가 주기(반기/연). 직전 평가 후 이 기간 내면 유효
                for half, (h_s, h_e) in spans.items():
                    h_s2 = max(h_s, max(_d(s), cut_d))
                    h_e2 = min(h_e, min(_d(e), date.today()))
                    if h_s2 > h_e2 or (h_e2 - h_s2).days < 30:  # 30일 미만 재적 기간은 제외
                        continue
                    lo = h_e2 - timedelta(days=cyc)  # 재적 종료 시점 기준 직전 주기 시작
                    for kind, key in (("낙상", "fall"), ("욕창", "sore"), ("인지", "cog")):
                        dds = [_d(dd) for dd in evals.get(key, [])]
                        has = any(h_s <= dd <= h_e for dd in dds)   # 해당 기간 내 평가
                        prior = any(lo < dd <= h_e2 for dd in dds)  # 직전 주기 내 평가(퇴소자 과탐 방지)
                        if not has and not prior:
                            halfyear_miss.append([p["name"], p.get("status", ""), f"{y} {half}", kind, f"{_fmt(h_s2)}~{_fmt(h_e2)} 재적"])

        # ---- 항목 22①: 계획일이 기초평가일(위험도평가·욕구사정)보다 앞서는지 ----
        # 매뉴얼 22①: 위험도 → 욕구사정 → 급여계획 순서. 급여계획 작성일이 이 평가들보다
        # 앞서면 그 평가를 반영했을 수 없다 → 위반. (PDF 사례집: 20 "욕구사정 일자가 급여계획
        # 작성 이후면 감점" + 22 "위험도→욕구사정→급여계획 순서 준수" — 욕구사정 작성일도 대상)
        # ★ 예전엔 위험도평가(fall/sore/cog)만 봤다. 욕구사정(needs)도 급여계획보다 앞서야 하므로
        #   둘을 합쳐 본다. ±40일 창으로 '이 계획에 딸린' 평가만 짝지어 무관한 주기 오탐을 막는다.
        eval_dates = [("위험도", dd) for key in ("fall", "sore", "cog") for dd in evals.get(key, [])]
        eval_dates += [("욕구사정", nn["date"]) for nn in needs if nn.get("date")]
        # 기초평가가 2회차 이상이면 각 회차의 평가는 '그 회차의 계획'이 반영한다. 평가일 직후(같은
        # 사이클, 40일 내)에 그 평가를 반영할 계획이 따로 있으면 앞 회차 계획의 순서위반으로 세지
        # 않는다(예: 계획04.20 vs 회차2 평가04.27 — 04.27은 계획04.27 소관이라 오탐). ★덮는 계획을
        # 평가일~+40일로 한정해야 한다 — 무제한이면 다음 연차 계획(356일 뒤)이 앞 해 진짜 위반을
        # 은폐한다(서구 이순남: 평가03.04·계획03.03 하루전 위반이 26.02.23 계획에 가려짐). 사용자 확정 2026-07-21.
        plan_dates = [_d(pl.get("wd")) for pl in p.get("plans", []) if pl.get("wd")]
        for pl in p.get("plans", []):
            wd = pl.get("wd") or ""
            if not wd or _d(wd) < cut_d:
                continue
            later = [(kind, dd) for kind, dd in eval_dates
                     if abs((_d(dd) - _d(wd)).days) <= 40 and _d(dd) > _d(wd)
                     and not any(_d(dd) <= pd <= _d(dd) + timedelta(days=40) for pd in plan_dates)]
            if later:
                order_issues.append([p["name"], wd, "계획 작성일보다 늦은 "
                                     + ", ".join(f"{k}({d})" for k, d in later)])

        # ---- 연간작성/계약/계획 ----
        miss_cols = []
        for y in range(cut_d.year, cur_year + 1):
            enrolled = any(_d(e) >= cut_d and _d(s).year <= y <= _d(e).year for s, e in periods)
            if not enrolled:
                miss_cols.append("-")
                continue
            miss = []
            for kind, key in (("낙상", "fall"), ("욕창", "sore"), ("인지", "cog")):
                if not any(dd.startswith(str(y)) for dd in evals.get(key, [])):
                    miss.append(kind)
            if not any((pl.get("wd") or "").startswith(str(y)) for pl in p.get("plans", [])):
                miss.append("계획")
            miss_cols.append(",".join(miss) if miss else "OK")
        while len(miss_cols) < 3:
            miss_cols.append("-")

        cts = p.get("contracts", [])
        sig_issues = [
            f"{c['cdate']} 미서명(수급자:{c['sSig']}/보호자:{c['gSig']})"
            for c in cts if not (c["sSig"] == "서명" and c["gSig"] == "서명")
        ]
        cont_issues = []
        if not cts:
            cont_issues.append("계약없음")
        else:
            cover = []
            for c in cts:
                parts = [x.strip() for x in c["period"].split("~")]
                if parts and parts[0][:4].isdigit():
                    cover.append((parts[0], parts[1] if len(parts) > 1 and parts[1] else parts[0]))
            for s, e in periods:
                if _d(e) < cut_d:
                    continue
                s2 = cutoff if _d(s) < cut_d else s
                if _d(s2) > _d(e):
                    continue
                cont_issues += ["계약공백 " + g for g in find_gaps(cover, s2, e)]

        plan_prob = []
        for pl in p.get("plans", []):
            st = pl.get("st") or ""
            is_gongdan = (pl.get("key") or "").strip().startswith("공단")
            sent = "발송완료" in st
            signed = ("서명완료" in st) or pl.get("agreeSigned")
            same_day = pl.get("agreeDate") and pl.get("wd") and pl["agreeDate"] == pl["wd"]
            issue = None
            if st == "팝업실패":
                issue = "확인실패"
            elif not sent and not signed:
                issue = "공단연동·발송기록없음" if is_gongdan else "미발송·미서명"
            elif not signed:
                issue = "서명없음"
            else:
                # 발송·서명 완료: 매뉴얼 22② '급여제공 시작일까지 설명·서명·통보'
                # → 동의일이 적용기간 개시일 이전(이하)이면 정상. (작성일과 다른 날이어도 무방)
                ap_start = (pl.get("ap") or "").split("~")[0].strip()
                agree = (pl.get("agreeDate") or "").strip()
                if ap_start and agree:
                    if agree > ap_start:
                        issue = f"동의일 적용개시 이후({agree}>{ap_start})"
                elif not agree:
                    issue = "동의일 없음"
                elif not same_day:  # 적용기간 파싱 불가 시 기존 기준으로 폴백
                    issue = "작성일-동의일 불일치"
            if issue:
                plan_prob.append(f"{pl.get('wd') or '?'} {issue}")
                plan_issues.append([p["name"], pl.get("wd", ""), pl.get("ap", ""), st, pl.get("agreeDate", ""), issue])

        # ---- 항목 27①: 2026~ 계획서 기능회복훈련 세부내용 ----
        # 캡처 신뢰성 주의: 구 스캐너는 '기능회복' 첫 등장부터 300자만 담았는데, 그 첫 등장이
        # 특이사항·종합의견 같은 서술형 문단인 계획서가 많아 표 본문을 통째로 놓쳤다.
        # (실측: 4개 지점 2026~ 계획 387건 전건이 300자 상한에 걸림. 청주 31건 '미기재' 중
        #  live 재캡처로 대조한 건은 전부 표가 실재 → 전건 오탐.)
        # → 판정은 rehabHits/rehabCut 을 남기는 신 스캐너 데이터에서만 하고,
        #   캡처가 불완전하면 '미흡' 대신 판정 불가로 뺀다.
        for pl in p.get("plans", []):
            wd = pl.get("wd") or ""
            if not wd.startswith(("2026", "2025.12")):  # 적용 2026.1~ (전년 12월 작성 예외 인정)
                continue
            rehab_total += 1
            new_scan = "rehabHits" in pl          # 신 스캐너 데이터 여부
            rt = (pl.get("rehabTxt") or "").strip()
            nsp = rt.replace(" ", "")             # '기본동작 훈련' 같은 공백 표기 흡수
            # 판정 불가: 팝업실패 / 구버전raw(300자 절단) / 신 스캐너라도 상한 도달
            if (pl.get("st") == "팝업실패") or (not new_scan) or pl.get("rehabCut"):
                rehab_nodata += 1
                continue
            rehab_checked += 1
            if not rt or pl.get("rehabHits", 0) == 0:
                # 계획서 전체에 '기능회복' 언급 자체가 없음 → 확정 미기재
                rehab_miss.append([p["name"], wd, "기능회복훈련 항목 없음"])
            elif not any(k in nsp for k in ("신체기능", "기본동작", "일상생활동작")):
                rehab_miss.append([p["name"], wd, "기능회복훈련 세부내용(신체기능·기본동작·일상생활동작) 없음"])
            elif "기본동작" not in nsp:
                # 매뉴얼상 기본동작훈련 세부내용은 필수이나, 표기 다양성 여지가 있어
                # 단정하지 않고 '주의(확인요망)'로 둔다.
                rehab_warn.append([p["name"], wd, "기본동작훈련 세부내용 확인요망(다른 훈련은 기재됨)"])

        rows_check.append([
            p["name"], p.get("status", ""), period_txt,
            miss_cols[0], miss_cols[1], miss_cols[2],
            "; ".join(sig_issues) or "서명OK",
            "; ".join(cont_issues) or "연속OK",
            f"{len(p.get('plans', []))}건" + (" / " + "; ".join(plan_prob) if plan_prob else " 모두정상"),
        ])

    # ---- 항목 판정 ----
    n_disc = sum(1 for r in rows_match if r[8] == "불일치")
    n_order = len(order_issues)
    n_half = len(halfyear_miss)
    n_plan = len(plan_issues)
    # 22②: 서명 관련(전자서명 미완료 등)은 '수기 서명'이면 서류함(수급자 관리기록)에 서명된
    #   급여제공계획서가 있어 정상 — 자동으론 못 봐서 미흡 대신 '주의(서류함 수기서명 확인)'로 완화.
    #   (사용자 확정 2026-07-20) 동의일 지연·불일치 등 '진짜 문제'는 미흡 유지.
    _SIG_SOFT = ("서명없음", "미서명", "발송기록없음", "동의일 없음", "확인실패")
    plan_hard = [r for r in plan_issues if not any(k in (r[5] or "") for k in _SIG_SOFT)]
    plan_soft = [r for r in plan_issues if any(k in (r[5] or "") for k in _SIG_SOFT)]
    n_plan_hard, n_plan_soft = len(plan_hard), len(plan_soft)

    def status_of(n_bad, warn=1):
        return "양호" if n_bad == 0 else ("미흡" if n_bad >= warn else "주의")

    def plan_status():
        return "미흡" if n_plan_hard else ("주의" if n_plan_soft else "양호")

    # 항목 21 기준별(낙상①/욕창②/인지③) 누락 분리
    half_by_kind = {"낙상": 0, "욕창": 0, "인지": 0}
    for r in halfyear_miss:
        if r[3] in half_by_kind:
            half_by_kind[r[3]] += 1

    def st(n):
        return "양호" if n == 0 else "미흡"

    # ---- 항목 20①: 종합 욕구사정 (매뉴얼 5요건) ----
    # 소스는 욕구사정 상세 폼(needs_full_<지점>.json) — 1-1 스캔만으로는 작성자·총평·판단근거를
    # 알 수 없다. 옛 판정은 '낙상↔욕구 불일치 + 순서위반' 두 가지로 20 을 매겼는데 둘 다
    # 매뉴얼 20① 요건이 아니다(순서위반은 22① 지표라 아래에서 22 로 이관, 불일치는 참고 병기).
    # 수집물이 없으면 None(조용한 스킵)이 아니라 '주의' 를 낸다 — 안 본 것이 양호로 보이면 안 된다.
    item_results = {
        "20": judge20(branch_name or "", cutoff, n_disc,
                      yearly=yearly_needs_miss(results, cutoff, today)),
        "21": {
            "status": status_of(n_half),
            "sub_status": {"①": st(half_by_kind["낙상"]), "②": st(half_by_kind["욕창"]), "③": st(half_by_kind["인지"])},
            "detail": (f"반기별 누락 {n_half}건 — 낙상 {half_by_kind['낙상']}, "
                       f"욕창 {half_by_kind['욕창']}, 인지 {half_by_kind['인지']}"),
        },
        # 항목 22①: '욕구사정·낙상·욕창·인지 평가를 반영한' 급여제공계획 (매뉴얼 22① 지표)
        #   계획 작성일이 기초평가일보다 앞서면 그 평가를 반영했을 수 없다 → ① 위반.
        #   (옛 코드는 이걸 20 에 넣었으나 20① 요건이 아니다 — 여기로 이관. 사용자 확정 2026-07-17)
        #   ★부분판정: ① 은 작성자명·세부목표·종합의견 등도 요구하는데 여기선 '반영 순서' 만 본다.
        #     그래서 items.py 의 22 auto_subs 에 '①' 을 넣지 않는다 — 양호가 점수로 자동기입되면
        #     안 본 요건까지 충족으로 둔갑한다(27번이 auto_subs=None 으로 같은 처리).
        "22": {
            "status": "미흡" if (n_order or n_plan_hard) else plan_status(),
            "sub_status": {"①": "양호" if n_order == 0 else "미흡", "②": plan_status()},
            "detail": (f"[②발송·서명] 발송·서명 문제 {n_plan}건"
                       + (f" (진짜문제 {n_plan_hard}건" if n_plan_hard else " (")
                       + (f", 서명미완료 {n_plan_soft}건→★수기서명이면 서류함(수급자 관리기록)에 서명계획서 확인요망)"
                          if n_plan_soft else ")")
                       + f" / [부분판정: ①평가 반영] 계획 작성일이 기초평가일보다 앞선 건 {n_order}건"
                       + ((" — " + "; ".join(f"{r[0]}({r[1]})" for r in order_issues[:5])
                           + (f" 외 {len(order_issues)-5}건" if len(order_issues) > 5 else ""))
                          if order_issues else "")
                       + " (①의 작성자명·세부목표·종합의견 등 나머지 요건은 수기 확인)"),
        },
    }
    if rehab_total:
        # 커버리지(판정 N / 전체 M)를 반드시 노출한다 — 판정분만 깨끗하다고 '양호'로 읽히면
        # 실제로는 미판정인데 양호로 보이는 위험이 있다. 커버리지 미달이면 양호를 주지 않는다.
        cov = rehab_checked / rehab_total
        if rehab_miss:
            r_st = "미흡"
        elif rehab_warn or rehab_nodata:
            r_st = "주의"
        elif rehab_checked:
            r_st = "양호"
        else:
            r_st = "주의"
        cov_txt = f"판정 {rehab_checked}건/전체 {rehab_total}건 (커버리지 {cov:.0%})"
        if rehab_nodata:
            cov_txt += f", 캡처불가 {rehab_nodata}건"
        item_results["27"] = {
            "status": r_st,
            "sub_status": {"①": r_st},
            "detail": f"[부분판정: ①계획서 기능회복훈련] 2026~ {cov_txt} — 미기재 {len(rehab_miss)}건"
                      + (f", 확인요망 {len(rehab_warn)}건" if rehab_warn else "")
                      + ((" — " + "; ".join(f"{r[0]}({r[1]})" for r in (rehab_miss + rehab_warn)[:5])) if (rehab_miss or rehab_warn) else "")
                      + (" ※구 스캐너 데이터는 캡처 절단으로 판정 제외(재스캔 필요)" if rehab_nodata else "")
                      + " (②숙지 면담은 수기 확인)",
        }

    return {
        "rows_match": rows_match,
        "rows_check": rows_check,
        "plan_issues": plan_issues,
        "rehab_miss": rehab_miss + rehab_warn,
        "halfyear_miss": halfyear_miss,
        "order_issues": order_issues,
        "item_results": item_results,
        "stats": {
            "total_rounds": len(rows_match),
            "disc": n_disc,
            "match": sum(1 for r in rows_match if r[8] == "일치"),
        },
    }
