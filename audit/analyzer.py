"""스캔 원본 데이터 → 항목 20/21/22 판정 + 상세 리스트 생성."""
from __future__ import annotations

from datetime import date, datetime, timedelta


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y.%m.%d").date()


def _fmt(d: date) -> str:
    return d.strftime("%Y.%m.%d")


def enroll_periods(evts: list[dict], today: str) -> list[tuple[str, str]]:
    periods, open_ = [], None
    for e in sorted(evts or [], key=lambda x: _d(x["d"])):
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


def analyze(results: list[dict], cutoff: str) -> dict:
    """스캔 결과 전체 분석. 반환: dict(대조리스트/연간점검/계획문제/항목판정)"""
    today = _fmt(date.today())
    rows_match = []      # 낙상↔욕구 대조
    rows_check = []      # 수급기간/연간작성/계약/계획
    plan_issues = []
    halfyear_miss = []   # 항목 21: 반기별 누락
    order_issues = []    # 항목 20: 계획일 < 기초평가일 순서 위반
    rehab_miss = []      # 항목 27①: 2026~ 계획서 기능회복훈련 미기재
    rehab_checked = 0    # rehabTxt 캡처된 계획 수 (구버전 raw는 0 → 27 미판정)

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
            if a < 1 and g < 1:
                if eff and eff["sit"] not in ("완전자립", "?") and eff["tr"] not in ("완전자립", "?"):
                    verdict = "확인(0점인데 도움기재)"
                else:
                    verdict = "정상(0점·완전자립)"
            elif eff is None:
                verdict = "욕구사정없음"
            elif eff["sit"] == "?" or eff["tr"] == "?":
                verdict = "확인필요(미체크)"
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

        # ---- 항목 21: 반기별 낙상/욕창/인지 ----
        evals = p.get("evals", {})
        for s, e in periods:
            if _d(e) < cut_d:
                continue
            y0 = max(_d(s).year, cut_d.year)
            for y in range(y0, cur_year + 1):
                for half, (h_s, h_e) in {"상반기": (date(y, 1, 1), date(y, 6, 30)), "하반기": (date(y, 7, 1), date(y, 12, 31))}.items():
                    h_s2 = max(h_s, max(_d(s), cut_d))
                    h_e2 = min(h_e, min(_d(e), date.today()))
                    if h_s2 > h_e2 or (h_e2 - h_s2).days < 30:  # 30일 미만 재적 반기는 제외
                        continue
                    for kind, key in (("낙상", "fall"), ("욕창", "sore"), ("인지", "cog")):
                        has = any(h_s <= _d(dd) <= h_e for dd in evals.get(key, []))
                        if not has:
                            halfyear_miss.append([p["name"], p.get("status", ""), f"{y} {half}", kind, f"{_fmt(h_s2)}~{_fmt(h_e2)} 재적"])

        # ---- 항목 20: 계획일이 기초평가일보다 앞서는지 ----
        all_eval_dates = sorted({dd for key in ("fall", "sore", "cog") for dd in evals.get(key, [])}, key=_d)
        for pl in p.get("plans", []):
            wd = pl.get("wd") or ""
            if not wd or _d(wd) < cut_d:
                continue
            base = [dd for dd in all_eval_dates if abs((_d(dd) - _d(wd)).days) <= 40]
            if base and any(_d(dd) > _d(wd) for dd in base):
                later = [dd for dd in base if _d(dd) > _d(wd)]
                order_issues.append([p["name"], wd, "계획 작성일보다 늦은 기초평가: " + ", ".join(later)])

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
            elif not same_day:
                issue = "작성일-동의일 불일치"
            if issue:
                plan_prob.append(f"{pl.get('wd') or '?'} {issue}")
                plan_issues.append([p["name"], pl.get("wd", ""), pl.get("ap", ""), st, pl.get("agreeDate", ""), issue])

        # ---- 항목 27①: 2026~ 계획서 기능회복훈련 세부내용 (rehabTxt 캡처분만) ----
        for pl in p.get("plans", []):
            if "rehabTxt" not in pl:
                continue
            wd = pl.get("wd") or ""
            if not wd.startswith(("2026", "2025.12")):  # 적용 2026.1~ (전년 12월 작성 예외 인정)
                continue
            rehab_checked += 1
            rt = (pl.get("rehabTxt") or "").strip()
            has_kind = any(k in rt for k in ("신체기능", "기본동작", "일상생활동작"))
            if not rt or not has_kind:
                rehab_miss.append([p["name"], wd, "기능회복훈련 세부내용 없음/미기재"])

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

    def status_of(n_bad, warn=1):
        return "양호" if n_bad == 0 else ("미흡" if n_bad >= warn else "주의")

    # 항목 21 기준별(낙상①/욕창②/인지③) 누락 분리
    half_by_kind = {"낙상": 0, "욕창": 0, "인지": 0}
    for r in halfyear_miss:
        if r[3] in half_by_kind:
            half_by_kind[r[3]] += 1

    def st(n):
        return "양호" if n == 0 else "미흡"

    item_results = {
        "20": {
            "status": "양호" if (n_disc == 0 and n_order == 0) else "미흡",
            "sub_status": {"①": "양호" if (n_disc == 0 and n_order == 0) else "미흡"},
            "detail": f"낙상↔욕구사정 불일치 {n_disc}건, 계획-평가 순서 위반 {n_order}건",
        },
        "21": {
            "status": status_of(n_half),
            "sub_status": {"①": st(half_by_kind["낙상"]), "②": st(half_by_kind["욕창"]), "③": st(half_by_kind["인지"])},
            "detail": (f"반기별 누락 {n_half}건 — 낙상 {half_by_kind['낙상']}, "
                       f"욕창 {half_by_kind['욕창']}, 인지 {half_by_kind['인지']}"),
        },
        "22": {
            "status": status_of(n_plan),
            "sub_status": {"②": status_of(n_plan)},
            "detail": f"급여제공계획 발송·서명 문제 {n_plan}건",
        },
    }
    if rehab_checked:
        item_results["27"] = {
            "status": st(len(rehab_miss)),
            "sub_status": {"①": st(len(rehab_miss))},
            "detail": f"[부분판정: ①계획서 기능회복훈련] 2026~ 계획 {rehab_checked}건 중 미기재 {len(rehab_miss)}건"
                      + ((" — " + "; ".join(f"{r[0]}({r[1]})" for r in rehab_miss[:5])) if rehab_miss else "")
                      + " (기본동작훈련 필수 기재 여부·②숙지 면담은 수기 확인)",
        }

    return {
        "rows_match": rows_match,
        "rows_check": rows_check,
        "plan_issues": plan_issues,
        "rehab_miss": rehab_miss,
        "halfyear_miss": halfyear_miss,
        "order_issues": order_issues,
        "item_results": item_results,
        "stats": {
            "total_rounds": len(rows_match),
            "disc": n_disc,
            "match": sum(1 for r in rows_match if r[8] == "일치"),
        },
    }
