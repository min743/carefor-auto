# -*- coding: utf-8 -*-
"""지점 단위 페이지 수집·판정.

수급자별 스캔과 달리 팝업 없이 페이지 DOM 텍스트/구조를 그대로 파싱한다.
연도 전환 reloadPage({'yy'}), 월 전환 reloadPage({'yy','mm'}).

판정 항목:
  5(보수교육, 8-7-1)  6(직원교육, 8-7)  7(직원인권보호, 1-6탭)  11(재난대응, 8-7)
  13(시설안전, 6-3)  16(감염관리 ①③6-2·②6-3)  19①(노인인권 교육, 부분)
  23②(약품 분기점검, 부분)  24·25·26(프로그램 ①계획·③의견, 부분, 2026~)
"""
from __future__ import annotations

import re
from datetime import date, datetime

from src.carefor_client import build_spa_hash, _navigate_spa

DN_BASE = "https://dn.carefor.co.kr/"

PAGES = {
    "edu":       ("left_sub8", "/share/staff/view.staff_education", "8-7.교육일지"),
    "refresher": ("left_sub8", "/share/staff/view.staff_refresher_training", "8-7-1.요양보호사 보수교육"),
    "checks":    ("left_sub6", "/share/safe/view.regularly_check", "6-3.정기점검"),
    "guide":     ("left_sub1", "/patient/view.patient_guide", "1-6.수급자 안내사항/예방접종"),
    "daily":     ("left_sub6", "/share/safe/view.daily_check", "6-2.일일점검"),
    "plan":      ("left_sub5", "/share/program/view.program_annual_plan_sep", "5-6.프로그램 계획"),
    "opinion":   ("left_sub5", "/share/program/view.program_evaluation", "5-5.프로그램 의견수렴 및 반영"),
    "health":    ("left_sub8", "/share/staff/view.staff_yearly_report", "8-10.건강검진관리"),
}

# 6-2 일일점검: 행(일자)×열(위생점검1/주방소독2/간호비품3/급식4) — class complete/none 로 작성 여부
DAILY_PARSE_JS = """
(() => {
  const b = document.querySelector('#r_padding g-b[data-gt-row-count]');
  if (!b) return [];
  const rows = parseInt(b.getAttribute('data-gt-row-count')) || 0;
  const out = [];
  for (let r = 0; r < rows; r++) {
    const cells = {};
    b.querySelectorAll('[data-gt-row="' + r + '"]').forEach(el => {
      const c = el.getAttribute('data-gt-col');
      if (c === null) return;
      cells[c] = { cls: el.className || '', txt: (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 24) };
    });
    if (cells['0'] && /\\d+일/.test(cells['0'].txt)) out.push(cells);
  }
  return out;
})()
"""

CLOSE_MODAL_JS = """
(() => {
  const btn = Array.from(document.querySelectorAll('div.m_button, .m_button'))
    .find(el => el.textContent.trim() === '창닫기');
  if (btn) btn.click();
  const mask = document.getElementById('mask_div');
  if (mask) mask.style.display = 'none';
})()
"""

GET_TEXT_JS = "(() => { const el = document.querySelector('#r_padding') || document.body; return el.innerText; })()"
GET_YEAR_JS = "(() => { const el = document.querySelector('.datepicker .datearea'); return el ? el.textContent.trim() : ''; })()"


def _goto(page, key: str, g_pammgno: str) -> None:
    type_, view, title = PAGES[key]
    h = build_spa_hash(type_, view, title, g_pammgno)
    _navigate_spa(page, f"{DN_BASE}#{h}")
    page.wait_for_timeout(3500)


def _set_year(page, year: int) -> None:
    cur = page.evaluate(GET_YEAR_JS)
    if str(year) in cur:
        return
    page.evaluate(f"reloadPage({{'yy':'{year}'}})")
    page.wait_for_timeout(3000)


def _set_month(page, year: int, month: int) -> None:
    page.evaluate(f"reloadPage({{'yy':'{year}','mm':'{month:02d}'}})")
    page.wait_for_timeout(2500)


def _month_range(cutoff: str, today) -> list[tuple[int, int]]:
    """기준일이 속한 달부터 이번 달까지 (y, m) 목록."""
    cy, cm = int(cutoff[:4]), int(cutoff[5:7])
    months = []
    y, m = cy, cm
    while (y, m) <= (today.year, today.month):
        months.append((y, m))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return months


def scrape_branch_pages(page, g_pammgno: str, years: list[int], progress_cb=print,
                        cutoff: str | None = None) -> dict:
    """지점 단위 페이지 순회 수집 (연도별/월별)."""
    from datetime import date as _date
    out = {"edu": {}, "checks": {}, "refresher": None, "rights": None,
           "daily": {}, "plan": {}, "opinion": {}}

    _goto(page, "edu", g_pammgno)
    for y in years:
        _set_year(page, y)
        out["edu"][str(y)] = page.evaluate(GET_TEXT_JS)
        progress_cb(f"  8-7 교육일지 {y}년 수집")

    _goto(page, "refresher", g_pammgno)
    out["refresher"] = page.evaluate(GET_TEXT_JS)
    progress_cb("  8-7-1 보수교육 수집")

    _goto(page, "checks", g_pammgno)
    for y in years:
        _set_year(page, y)
        out["checks"][str(y)] = page.evaluate(GET_TEXT_JS)
        progress_cb(f"  6-3 정기점검 {y}년 수집")

    # 연도별 텍스트 페이지들 (한 페이지 실패해도 나머지는 계속)
    def _yearly(key: str, label: str) -> None:
        try:
            _goto(page, key, g_pammgno)
            for y in years:
                _set_year(page, y)
                out[key][str(y)] = page.evaluate(GET_TEXT_JS)
            progress_cb(f"  {label} {len(years)}개년 수집")
        except Exception as e:
            progress_cb(f"  {label} 수집 실패(건너뜀): {e}")

    out["health"] = {}
    _yearly("plan", "5-6 프로그램 계획")
    _yearly("opinion", "5-5 의견수렴")
    _yearly("health", "8-10 건강검진")

    # 6-2 일일점검 (기준일 이후 월별 순회)
    if cutoff:
        try:
            _goto(page, "daily", g_pammgno)
            months = _month_range(cutoff, _date.today())
            for (y, m) in months:
                _set_month(page, y, m)
                out["daily"][f"{y}-{m:02d}"] = page.evaluate(DAILY_PARSE_JS)
            progress_cb(f"  6-2 일일점검 {len(months)}개월 수집")
        except Exception as e:
            progress_cb(f"  6-2 일일점검 수집 실패(건너뜀): {e}")

    # 1-6 직원인권 보호지침 탭 (2026 신설 지표 — 2026년부터만)
    if max(years) >= 2026:
        try:
            out["rights"] = scrape_rights(page, g_pammgno)
            progress_cb("  1-6 직원인권 보호지침 수집"
                        + ("" if out["rights"] else " — 내용 비어있음(실패)"))
        except Exception as e:
            out["rights"] = ""
            progress_cb(f"  1-6 직원인권 보호지침 수집 실패: {e}")

    return out


def scrape_rights(page, g_pammgno: str) -> str:
    """1-6 이동 → 직원인권 탭 클릭 → 내용 로딩 폴링 (탭 로딩이 간헐적으로 느려 재시도)."""
    GET_TAB_JS = ("(() => { const el = document.querySelector('#tab_div_guide_offer_when_join');"
                  " return el ? el.innerText : ''; })()")
    _goto(page, "guide", g_pammgno)
    for attempt in range(3):
        page.evaluate(CLOSE_MODAL_JS)
        page.wait_for_timeout(700)
        try:
            page.click(".tabmenu2 li:has-text('직원인권')", timeout=8000)
        except Exception:
            pass
        for _ in range(12):
            page.wait_for_timeout(1000)
            txt = page.evaluate(GET_TAB_JS)
            if txt and len(txt) > 100:
                return txt
    return ""


# ---------------- 파싱 ----------------

def parse_edu(text: str) -> dict:
    """교육일지: 회차 레코드([N회] 날짜 + 교육명 + 서명 n/m) + 신규직원 알림."""
    lines = [ln.strip() for ln in text.split("\n")]
    records = []
    for i, ln in enumerate(lines):
        m = re.match(r"^\[(\d+)회\]\s*(\d{4}\.\d{2}\.\d{2})", ln)
        if not m:
            continue
        name, sign = "", None
        for j in range(i + 1, min(i + 6, len(lines))):
            s = lines[j]
            if not s:
                continue
            sm = re.search(r"(\d+)\s*/\s*(\d+)", s)
            if "서명" in s and sm:
                sign = (int(sm.group(1)), int(sm.group(2)))
                break
            if s == "직원 서명":
                continue
            if re.match(r"^\[(\d+)회\]", s):
                break
            if not name and not re.match(r"^\d+\s*/\s*\d+$", s):
                name = s
            elif name and sm and re.match(r"^\d+\s*/\s*\d+$", s):
                sign = (int(sm.group(1)), int(sm.group(2)))
                break
        records.append({"round": int(m.group(1)), "date": m.group(2), "name": name, "sign": sign})

    # 신규직원 교육 기한 알림 (직원명/교육명/입사일/기한 4줄 반복)
    newstaff = []
    try:
        k = lines.index("교육 대상 신규직원")
        seq = [s for s in lines[k:k + 40] if s]
        # 헤더(직원명 교육명 입사일 교육 실시 기한) 이후 4개씩
        hdr = seq.index("교육 실시 기한")
        vals = seq[hdr + 1:]
        for a in range(0, len(vals) - 3, 4):
            nm, edu, join, due = vals[a:a + 4]
            dm = re.search(r"(\d{4}\.\d{2}\.\d{2})", due)
            jm = re.search(r"(\d{4}\.\d{2}\.\d{2})", join)
            if not (dm and jm):
                break
            newstaff.append({"name": nm, "edu": edu, "join": jm.group(1), "due": dm.group(1)})
    except ValueError:
        pass
    return {"records": records, "newstaff": newstaff}


def parse_checks(text: str) -> dict:
    """정기점검: 소방 12개월 + 약품 4분기 + 소독 4분기 작성 여부."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    # 마지막 '4분기' 위치 (소독 헤더 끝) 이후 상태 토큰 20개
    idxs = [i for i, s in enumerate(lines) if s == "4분기"]
    fire, med, dis = [], [], []
    if len(idxs) >= 2:
        statuses = []
        for s in lines[idxs[-1] + 1:]:
            if s in ("작성", "미작성", "-"):
                statuses.append(s == "작성")
                if len(statuses) == 20:
                    break
            elif len(statuses) > 0 and s not in ("작성", "미작성", "-"):
                break
        if len(statuses) == 20:
            fire, med, dis = statuses[:12], statuses[12:16], statuses[16:20]
    return {"fire": fire, "med": med, "disinfect": dis}


def parse_refresher(text: str) -> dict:
    """보수교육: 대상/작성 카운트 + 직원별 상태."""
    target = done = None
    m = re.search(r"대상 직원 수\s*\n\s*(\d+)명", text)
    if m:
        target = int(m.group(1))
    m = re.search(r"작성 직원 수\s*\n\s*(\d+)명\s*/\s*(\d+)명", text)
    if m:
        done = int(m.group(1))
    rows = []
    lines = [ln.strip() for ln in text.split("\n")]
    for i, ln in enumerate(lines):
        if ln in ("작성", "미작성", "연중 퇴사", "연중퇴사"):
            # 역방향으로 이름 찾기: [연번, 이름, 성별, 생년, 입사, 퇴사, 직종..., 대상여부, 상태]
            back = [s for s in lines[max(0, i - 12):i] if s]
            name = ""
            for b in range(len(back) - 1, -1, -1):
                if back[b] in ("대상", "비대상"):
                    # 대상여부 앞쪽에서 성별 위치 기준으로 이름 추정
                    for c in range(b - 1, -1, -1):
                        if back[c] in ("남", "여") and c >= 1:
                            name = back[c - 1]
                            break
                    break
            if name:
                rows.append({"name": name, "status": ln})
    return {"target": target, "done": done, "rows": rows}


def parse_rights(text: str) -> dict:
    """1-6 직원인권 보호지침 탭: 수급자별 [현황, 이름, 급여개시일, 제공일|퇴소(날짜)]."""
    lines = [ln.strip() for ln in text.split("\n")]
    date_re = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")

    done = total = None
    for ln in lines[:40]:
        m = re.match(r"^(\d+)\s*/\s*(\d+)$", ln)
        if m:
            done, total = int(m.group(1)), int(m.group(2))
            break

    rows = []
    i = 0
    while i < len(lines):
        if lines[i].isdigit():
            seq = lines[i + 1:i + 8]
            if len(seq) >= 5 and seq[0] in ("수급중", "퇴소", "보류", "대기", "입소대기"):
                status, group, name, grade, start = seq[0], seq[1], seq[2], seq[3], seq[4]
                if date_re.match(start):
                    provided = left_before = None
                    nxt = seq[5] if len(seq) > 5 else ""
                    if date_re.match(nxt):
                        provided = nxt
                    else:
                        mm = re.match(r"퇴소\((\d{4}\.\d{2}\.\d{2})\)", nxt)
                        if mm:
                            left_before = mm.group(1)
                    rows.append({"status": status, "name": name, "grade": grade,
                                 "start": start, "provided": provided, "left_before": left_before})
                    i += 6
                    continue
        i += 1
    return {"done": done, "total": total, "rows": rows}


def parse_health(text: str) -> dict:
    """8-10 건강검진: 직원별 [현황, 이름, 직종, 검진상태] + 상단 작성/항목누락/대상 카운트."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    counts = None
    for ln in lines[:80]:
        m = re.match(r"^(\d+)\s*/\s*(\d+)\s*/\s*(\d+)$", ln)
        if m:
            counts = (int(m.group(1)), int(m.group(2)), int(m.group(3)))  # 작성/항목누락/대상
            break
    rows = []
    i = 0
    while i < len(lines):
        if lines[i].isdigit() and i + 2 < len(lines) and lines[i + 1] in ("재직", "퇴사", "휴직"):
            status, name = lines[i + 1], lines[i + 2]
            # 이후 7줄 내 검진상태 토큰
            hstat = ""
            for j in range(i + 3, min(i + 11, len(lines))):
                if lines[j] in ("작성", "미작성", "퇴사", "항목누락", "연중 퇴사"):
                    hstat = lines[j]
                    break
                if lines[j].isdigit() and j > i + 5:
                    break
            rows.append({"status": status, "name": name, "health": hstat})
            i += 3
            continue
        i += 1
    return {"counts": counts, "rows": rows}


PROG_TYPES = ("신체기능", "인지기능", "사회적응")


def parse_plan(text: str) -> dict:
    """5-6: 유형별 연간계획 작성일. {유형: 'YYYY.MM.DD' | None}"""
    out = {t: None for t in PROG_TYPES}
    lines = [ln.strip() for ln in text.split("\n")]
    for i, ln in enumerate(lines):
        for t in PROG_TYPES:
            if ln == f"{t} 프로그램" and out[t] is None:
                for j in range(i + 1, min(i + 4, len(lines))):
                    if re.match(r"^\d{4}\.\d{2}\.\d{2}$", lines[j]):
                        out[t] = lines[j]
                        break
    return out


def parse_opinion(text: str) -> dict:
    """5-5: 유형별 [수렴 작성일들, 반영 작성 여부]. 유형 블록을 앵커로 분할."""
    lines = [ln.strip() for ln in text.split("\n")]
    # 유형 앵커 위치 (헤더 이후 본문 블록: '신체기능' 단독 줄)
    anchors = []
    for i, ln in enumerate(lines):
        if ln in PROG_TYPES:
            anchors.append((ln, i))
    out = {t: {"collect_dates": [], "reflect": False} for t in PROG_TYPES}
    for k, (t, i) in enumerate(anchors):
        end = anchors[k + 1][1] if k + 1 < len(anchors) else len(lines)
        seg = lines[i:end]
        for j, s in enumerate(seg):
            if s == "의견수렴 내용":
                for b in range(j - 1, max(0, j - 6), -1):
                    if re.match(r"^\d{4}\.\d{2}\.\d{2}$", seg[b]):
                        out[t]["collect_dates"].append(seg[b])
                        break
            if s == "의견반영 내용":
                out[t]["reflect"] = True
    return out


def parse_daily(cells_rows: list[dict], ym: str) -> dict:
    """6-2 한 달 데이터: 일자별 [위생(1), 간호비품(3)] 작성 여부.
    반환: {"hygiene_miss": [일...], "supply_miss": [일...], "days": N}"""
    hygiene_miss, supply_miss = [], []
    for row in cells_rows or []:
        c0 = row.get("0", {}).get("txt", "")
        m = re.match(r"(\d+)일", c0)
        if not m:
            continue
        if "(일)" in c0:  # 일요일(휴무일)은 미작성 대상에서 제외
            continue
        day = int(m.group(1))
        c1 = row.get("1", {})
        c3 = row.get("3", {})
        closed = c3.get("txt", "") == "-"  # 간호비품 '-' = 휴무일(토·공휴일) 추정 → 제외
        if not closed and "none" in c1.get("cls", "") and "미작성" in c1.get("txt", ""):
            hygiene_miss.append(day)
        if "none" in c3.get("cls", "") and "미작성" in c3.get("txt", ""):
            supply_miss.append(day)
    return {"hygiene_miss": hygiene_miss, "supply_miss": supply_miss, "days": len(cells_rows or [])}


# ---------------- 판정 ----------------

def _half_of(d: str) -> str:
    return "상반기" if int(d[5:7]) <= 6 else "하반기"


def analyze_branch_pages(data: dict, cutoff: str, today: date | None = None) -> dict:
    today = today or date.today()
    cut = datetime.strptime(cutoff, "%Y.%m.%d").date()
    years = sorted(int(y) for y in data.get("edu", {}).keys())

    edu_parsed = {y: parse_edu(data["edu"][str(y)]) for y in years}
    chk_parsed = {y: parse_checks(data["checks"].get(str(y), "")) for y in years}
    refresher = parse_refresher(data.get("refresher") or "")

    # ---- 항목 11: 재난대응훈련 반기별 (기준일 5/1, 11/1) ----
    disaster_miss = []
    for y in years:
        recs = [r for r in edu_parsed[y]["records"] if "재난" in r["name"]]
        for half, due, lo, hi, end in (
            ("상반기", date(y, 5, 1), f"{y}.01.01", f"{y}.06.30", date(y, 6, 30)),
            ("하반기", date(y, 11, 1), f"{y}.07.01", f"{y}.12.31", date(y, 12, 31)),
        ):
            if today < due or end < cut:
                continue
            if not any(lo <= r["date"] <= hi for r in recs):
                disaster_miss.append(f"{y} {half}")

    # ---- 항목 19①: 노인인권 교육 반기별 ----
    rights_miss = []
    rights_note = []
    for y in years:
        recs = [r for r in edu_parsed[y]["records"] if "노인인권" in r["name"] or "학대" in r["name"]]
        for half, lo, hi, end in (
            ("상반기", f"{y}.01.01", f"{y}.06.30", date(y, 6, 30)),
            ("하반기", f"{y}.07.01", f"{y}.12.31", date(y, 12, 31)),
        ):
            if end < cut:
                continue
            has = any(lo <= r["date"] <= hi for r in recs)
            if not has:
                if end < today:
                    rights_miss.append(f"{y} {half}")
                elif date(today.year, today.month, 1) > datetime.strptime(lo, "%Y.%m.%d").date():
                    rights_note.append(f"{y} {half} 미작성(진행중)")
        # 서명 미완: n/m 합계 기준
        for r in recs:
            if r["sign"] and r["sign"][1] - r["sign"][0] > 0:
                rights_note.append(f"{r['date']} 서명 {r['sign'][0]}/{r['sign'][1]}")

    # ---- 항목 6: 운영규정 교육(연1회) + 급여제공지침교육(연1회) + 신규직원 7일 ----
    edu6_miss = []
    for y in years:
        if date(y, 12, 31) < cut:
            continue
        recs = edu_parsed[y]["records"]
        if not any("운영규정" in r["name"] for r in recs):
            edu6_miss.append(f"{y} 운영규정 교육 없음" if y < today.year else f"{y} 운영규정 교육 미실시(진행중)")
        if not any("급여제공지침" in r["name"] for r in recs):
            edu6_miss.append(f"{y} 급여제공지침교육 없음" if y < today.year else f"{y} 급여제공지침교육 미실시(진행중)")
    # 신규직원 교육 기한 초과 (당해연도 알림)
    cur_ns = edu_parsed.get(today.year, {}).get("newstaff", [])
    overdue_ns = [
        f"{n['name']}({n['edu']} 기한 {n['due']})"
        for n in cur_ns
        if datetime.strptime(n["due"], "%Y.%m.%d").date() < today
    ]
    edu6_miss += ["신규직원 기한초과: " + s for s in overdue_ns]
    edu6_cur = [s for s in edu6_miss if "진행중" not in s]

    # ---- 항목 5: 보수교육 ----
    ref_miss = [r["name"] for r in refresher["rows"] if r["status"] == "미작성"]
    ref_target, ref_done = refresher.get("target"), refresher.get("done")

    # ---- 항목 13: 소방시설 월 1회 (매월 28일 기준) ----
    fire_miss = []
    for y in years:
        fire = chk_parsed[y]["fire"]
        if not fire:
            fire_miss.append(f"{y}년 데이터 파싱 실패")
            continue
        for mth in range(1, 13):
            m_end = date(y, mth, 28)
            if m_end < cut or m_end > today:
                continue
            if not fire[mth - 1]:
                fire_miss.append(f"{y}.{mth:02d}")

    # ---- 항목 16②: 정기소독 분기별 / 항목 23②: 일반의약품 분기별 ----
    dis_miss, med_miss = [], []
    q_ends = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
    for y in years:
        dis = chk_parsed[y]["disinfect"]
        med = chk_parsed[y]["med"]
        for q, (mm, dd) in q_ends.items():
            q_end = date(y, mm, dd)
            if q_end < cut or q_end > today:
                continue
            if dis and not dis[q - 1]:
                dis_miss.append(f"{y} {q}분기")
            if med and not med[q - 1]:
                med_miss.append(f"{y} {q}분기")

    # ---- 항목 16①③: 6-2 일일점검 (간호비품 매일 · 위생점검 매일, 전일까지) ----
    supply_miss_m, hygiene_miss_m = [], []
    for ym_key in sorted(data.get("daily", {})):
        y, m = int(ym_key[:4]), int(ym_key[5:7])
        d = parse_daily(data["daily"][ym_key], ym_key)
        lo = cut.day if (y, m) == (cut.year, cut.month) else 1
        hi = today.day - 1 if (y, m) == (today.year, today.month) else 31
        sup = [dd for dd in d["supply_miss"] if lo <= dd <= hi]
        hyg = [dd for dd in d["hygiene_miss"] if lo <= dd <= hi]
        if sup:
            supply_miss_m.append(f"{ym_key}({len(sup)}일)")
        if hyg:
            hygiene_miss_m.append(f"{ym_key}({len(hyg)}일)")

    # ---- 항목 24·25·26: 프로그램 (①연간계획 + ③의견 반기수렴·연1회 반영) ----
    plan_parsed = {int(y): parse_plan(t) for y, t in data.get("plan", {}).items()}
    op_parsed = {int(y): parse_opinion(t) for y, t in data.get("opinion", {}).items()}
    prog_miss = {t: [] for t in PROG_TYPES}
    prog_note = {t: [] for t in PROG_TYPES}
    for y in sorted(plan_parsed):
        # 5-5/5-6은 2026년 개편 화면부터 유형별 관리 — 이전 연도는 구버전이라 제외
        if y < 2026 or date(y, 12, 31) < cut:
            continue
        for t in PROG_TYPES:
            if plan_parsed.get(y) is not None and not plan_parsed[y].get(t):
                prog_miss[t].append(f"{y} 연간계획 없음")
            op = op_parsed.get(y, {}).get(t, {"collect_dates": [], "reflect": False})
            for half, lo_d, hi_d, end in (
                ("상반기", f"{y}.01.01", f"{y}.06.30", date(y, 6, 30)),
                ("하반기", f"{y}.07.01", f"{y}.12.31", date(y, 12, 31)),
            ):
                if end < cut or end > today:
                    continue
                if not any(lo_d <= dd <= hi_d for dd in op["collect_dates"]):
                    prog_miss[t].append(f"{y} {half} 의견수렴 없음")
            if y < today.year and not op["reflect"]:
                prog_miss[t].append(f"{y} 의견반영 없음")
            elif y == today.year and not op["reflect"]:
                prog_note[t].append(f"{y} 의견반영 미작성(진행중)")

    # ---- 항목 7: 직원인권 보호지침 (2026 신설 — 2026년부터) ----
    rights = parse_rights(data.get("rights") or "")
    r7_missing, r7_late = [], []
    for r in rights["rows"]:
        if r["left_before"]:
            continue  # 안내일 이전 퇴소자 제외 (사용자 확정)
        if not r["provided"]:
            r7_missing.append(f"{r['name']}({r['status']})")
        elif r["start"] >= "2026" and r["provided"] > r["start"]:
            # 2026년 급여개시 수급자: 개시일까지 안내돼 있어야 (미리 안내는 정상)
            r7_late.append(f"{r['name']} 개시{r['start']}→제공{r['provided']}")

    # ---- 항목 15①: 건강검진 연 1회 (완결 연도는 미작성=미흡, 당해 연도는 진행중 표기) ----
    health_parsed = {int(y): parse_health(t) for y, t in (data.get("health") or {}).items()}
    health_miss, health_note = [], []
    for y in sorted(health_parsed):
        if date(y, 12, 31) < cut:
            continue
        rows = health_parsed[y]["rows"]
        miss_names = [r["name"] for r in rows if r["health"] == "미작성" and r["status"] != "퇴사"]
        if not rows:
            continue
        if y < today.year:
            if miss_names:
                health_miss.append(f"{y}년 미작성 {len(miss_names)}명({', '.join(miss_names[:8])}{'…' if len(miss_names) > 8 else ''})")
        else:
            if miss_names:
                health_note.append(f"{y}년 미작성 {len(miss_names)}명(연내 진행중)")

    def st(miss):
        return "양호" if not miss else "미흡"

    item_results = {}
    if health_parsed:
        item_results["15"] = {
            "status": st(health_miss),
            "detail": "[부분판정: ①연1회 검진] "
                      + ("; ".join(health_miss + health_note) or "완결 연도 전 직원 검진 작성 확인")
                      + " (②입사전 검진 제출 탭은 추후 구현)",
        }
    if data.get("rights"):
        parts = []
        if rights["done"] is not None:
            parts.append(f"완료 {rights['done']}/{rights['total']}명")
        if r7_missing:
            parts.append("미제공: " + ", ".join(r7_missing))
        if r7_late:
            parts.append("개시 후 지연제공: " + ", ".join(r7_late))
        if not r7_missing and not r7_late:
            parts.append("전 수급자 제공 확인 (안내 전 퇴소자 제외)")
        item_results["7"] = {
            "status": st(r7_missing + r7_late),
            "detail": "2026년 기준 — " + " · ".join(parts),
        }

    item_results |= {
        "5": {
            "status": st(ref_miss),
            "detail": (f"대상 {ref_target}명 중 작성 {ref_done}명"
                       + (f", 미작성: {', '.join(ref_miss)}" if ref_miss else " — 전원 이수/작성")),
        },
        "6": {
            "status": st(edu6_cur),
            "detail": ("; ".join(edu6_miss) or "운영규정·급여제공지침 교육 연 1회 충족")
                      + " (①지침 12항목 비치는 수기 확인)",
        },
        "11": {
            "status": st(disaster_miss),
            "detail": ("누락: " + ", ".join(disaster_miss)) if disaster_miss else "반기별 재난대응훈련 실시 확인",
        },
        "13": {
            "status": st(fire_miss),
            "detail": ("소방점검 누락: " + ", ".join(fire_miss)) if fire_miss else "매월 소방시설 점검 입력 확인",
        },
        "16": {
            "status": st(supply_miss_m + hygiene_miss_m + dis_miss),
            "detail": ("① 간호비품 미작성: " + (", ".join(supply_miss_m) or "없음")
                       + " · ② 정기소독 누락: " + (", ".join(dis_miss) or "없음")
                       + " · ③ 위생점검 미작성: " + (", ".join(hygiene_miss_m) or "없음")),
        },
        "23": {
            "status": st(med_miss),
            "detail": "[부분판정: ②분기점검만] 일반의약품 분기 점검 "
                      + (("누락: " + ", ".join(med_miss)) if med_miss else "충족")
                      + " (①보관함 잠금·③적정투약은 현장/수기 확인)",
        },
        "24": {
            "status": st(prog_miss["신체기능"]),
            "detail": "[부분판정: ①계획·③의견, 2026년~] "
                      + ("; ".join(prog_miss["신체기능"] + prog_note["신체기능"]) or "연간계획·의견수렴/반영 충족")
                      + " (②주3회 실시는 다음 단계)",
        },
        "25": {
            "status": st(prog_miss["인지기능"]),
            "detail": "[부분판정: ①계획·③의견, 2026년~] "
                      + ("; ".join(prog_miss["인지기능"] + prog_note["인지기능"]) or "연간계획·의견수렴/반영 충족")
                      + " (②주3회 실시는 다음 단계)",
        },
        "26": {
            "status": st(prog_miss["사회적응"]),
            "detail": "[부분판정: ①계획·③의견, 2026년~] "
                      + ("; ".join(prog_miss["사회적응"] + prog_note["사회적응"]) or "연간계획·의견수렴/반영 충족")
                      + " (②월1회 실시는 다음 단계)",
        },
        "19": {
            "status": st(rights_miss),
            "detail": "[부분판정: ①교육일지만] "
                      + (("누락: " + ", ".join(rights_miss)) if rights_miss else "반기별 노인인권 교육 확인")
                      + (" / " + "; ".join(rights_note) if rights_note else "")
                      + " (②안내사항·③기록지는 3~4차 구현 예정)",
        },
    }

    return {
        "item_results": item_results,
        "detail": {
            "edu_records": {y: edu_parsed[y]["records"] for y in years},
            "newstaff": cur_ns,
            "refresher": refresher,
            "checks": {y: chk_parsed[y] for y in years},
            "rights": rights,
            "daily_miss": {"supply": supply_miss_m, "hygiene": hygiene_miss_m},
            "programs": {"plan": plan_parsed, "opinion": op_parsed},
            "health": health_parsed,
        },
    }
