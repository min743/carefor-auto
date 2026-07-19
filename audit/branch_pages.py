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

import calendar
import re
from datetime import date, datetime, timedelta

from src.carefor_client import build_spa_hash, _navigate_spa

DN_BASE = "https://dn.carefor.co.kr/"

# 기관 점검용 가상 계정 — 직원 판정에서 제외 (사용자 확정 2026-07-06)
EXCLUDE_STAFF = {"관리팀", "평가자"}

PAGES = {
    "edu":       ("left_sub8", "/share/staff/view.staff_education", "8-7.교육일지"),
    "refresher": ("left_sub8", "/share/staff/view.staff_refresher_training", "8-7-1.요양보호사 보수교육"),
    "checks":    ("left_sub6", "/share/safe/view.regularly_check", "6-3.정기점검"),
    "guide":     ("left_sub1", "/patient/view.patient_guide", "1-6.수급자 안내사항/예방접종"),
    "daily":     ("left_sub6", "/share/safe/view.daily_check", "6-2.일일점검"),
    "plan":      ("left_sub5", "/share/program/view.program_annual_plan_sep", "5-6.프로그램 계획"),
    "opinion":   ("left_sub5", "/share/program/view.program_evaluation", "5-5.프로그램 의견수렴 및 반영"),
    "health":    ("left_sub8", "/share/staff/view.staff_yearly_report", "8-10.건강검진관리"),
    "master":    ("left_sub9", "/basic/view.center_master", "9-1.시설정보설정"),
    "welfare":   ("left_sub8", "/share/staff/view.welfare_reward_manage", "8-1-1.복지(포상) 제공대장 관리"),
    "progdaily": ("left_sub5", "/share/program/view.program_service_daily", "5-1.프로그램 제공기록"),
    "consult":   ("left_sub1", "/share/patient/view.patient_consult", "1-4.상담일지"),
    "case":      ("left_sub8", "/share/patient/view.patient_case_meeting_tab", "8-5.사례관리 회의록"),
    "connect":   ("left_sub1", "/share/patient/view.patient_connection_send_report", "1-10.연계기록지 발송 리포트"),
    "status":    ("left_sub3", "/share/care/view.status_change_report", "3-2.상태변화 기록"),
    "casetotal": ("left_sub1", "/patient/view.patient_case_total", "1-2.전체 기초평가 현황"),
    "bigo":      ("left_sub3", "/share/care/view.care_service_bigo_all", "3-1-3.요양급여 특이사항 관리"),
}

# 1-2 전체 기초평가 현황: '급여제공 결과평가' 열 집계(작성건수/대상자수) 추출.
# 2024~25 구버전 = 연 1회 단일 열, 2026~ = 반기 2열 (data-gt-col 기반 매핑)
EVAL12_JS = """
(() => {
  const gt = Array.from(document.querySelectorAll('g-t')).find(t => t.textContent.includes('작성건수'));
  if (!gt) return null;
  const gh = gt.querySelector('g-h');
  if (!gh) return null;
  const ths = Array.from(gh.querySelectorAll('g-th'));
  const hdr = ths.find(t => t.textContent.includes('결과평가'));
  if (!hdr) return null;
  const col = hdr.getAttribute('data-gt-col');
  const span = parseInt(hdr.getAttribute('colspan') || '1');
  const agg = {};
  ths.forEach(t => {
    const txt = t.textContent.replace(/\\s+/g, '');
    if (/^\\d+\\/\\d+$/.test(txt)) agg[t.getAttribute('data-gt-col')] = txt;
  });
  if (span === 1) return {kind: 'year', y: agg[col] || ''};
  const halfCells = ths.filter(t => ['상반기', '하반기'].includes(t.textContent.trim()));
  const i0 = halfCells.findIndex(t => t.getAttribute('data-gt-col') === col);
  const col2 = (i0 >= 0 && halfCells[i0 + 1]) ? halfCells[i0 + 1].getAttribute('data-gt-col') : null;
  return {kind: 'half', h1: agg[col] || '', h2: col2 ? (agg[col2] || '') : ''};
})()
"""

# 1-4 상담일지: 수급자별 분기 셀 complete/none + 행 data-info(연간 상담수·급여반영수)
# 17③ 월간 소식 게시용 지점별 케어링 네이버 블로그 (사용자 제공 2026-07-18).
# 네이버가 외부 검색·fetch를 막아 자동 게시 판정 불가 → 대시보드에 링크만 달아 수기 확인요망.
CARING_BLOG = {
    "둔산점": "https://blog.naver.com/cc_dg02",
    "서구점": "https://blog.naver.com/cc_dg03",
    "천안점": "https://blog.naver.com/cc_gg081",
    "청주 오창점": "https://blog.naver.com/cc_gg14",
}

CONSULT_PARSE_JS = """
(() => {
  const t = document.querySelector('#patient_consult_table');
  if (!t) return null;
  const out = {header: [], banner: '', rows: []};
  t.querySelectorAll('g-h span[data-type=searchedStatus]').forEach(s => out.header.push(s.innerText.trim()));
  const bn = document.querySelector('.m_button.opn .stxt');
  if (bn) out.banner = bn.innerText.trim();
  const b = t.querySelector('g-b');
  if (!b) return out;
  let cur = null;
  Array.from(b.children).forEach(el => {
    const tag = el.tagName;
    if (tag === 'G-TF') {
      cur = null;
      try { cur = JSON.parse(el.getAttribute('data-info')); } catch (e) {}
      if (cur) out.rows.push({name: cur.pamname || '', pas: cur.year_pas_cnt || 0,
                              csh: cur.year_csh_cnt || 0, stat: '', q: []});
    } else if (tag === 'G-TD' && cur && out.rows.length) {
      const col = el.getAttribute('data-gt-col');
      const last = out.rows[out.rows.length - 1];
      if (col === '1') last.stat = el.innerText.trim();
      if (['5', '6', '7', '8'].includes(col) && last.q.length < 4) {
        last.q.push(el.className.includes('complete') ? (el.innerText.trim().split('\\n')[0] || '✓') : '');
      }
    }
  });
  return out;
})()
"""

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


# ── 11번 재난훈련 휴무일 검증: 2-8 월간 일정 현황(일별 수급자 출석) ──────────────
# 재난훈련 날짜에 그날 수급자 출석이 0명이면 휴무일 훈련 의심(실제 대피훈련 불가).
_ATTEND_GRAB_JS = (
    "() => { const c=document.querySelector('#div_monthly_attend_stat_info')||document.body;"
    " const de=c.querySelector('.datearea');"
    " return {mon:(de?de.innerText:'').trim(), n:c.querySelectorAll('g-td').length,"
    " cells:[...c.querySelectorAll('g-td')].map(x=>x.innerText.trim())}; }"
)


def _attend_by_day(cells: list[str], ndays: int) -> dict[int, int]:
    """2-8 월간 일정 현황 셀 → {일: 출석인원}.

    ★ 1행 = 3 + '그달 일수' + 1 셀 = [연번, 이름, 등급, 1일..N일, 일정합계].
      열수를 35로 고정하면 30일월(6월=34셀)에서 어긋나 전 평일 오탐 → 반드시 ndays 기준.
      날짜 셀이 'N시간M분'이면 출석, 빈칸이면 결석.
    """
    cols = 3 + ndays + 1
    att = {dd: 0 for dd in range(1, ndays + 1)}   # 유효 그리드면 미출석일도 0(=휴무)로 남긴다
    valid = 0
    for i in range(0, len(cells), cols):
        r = cells[i:i + cols]
        if len(r) < cols or not re.match(r"^\d+$", r[0]) or "등급" not in r[2]:
            continue
        valid += 1
        for dd in range(1, ndays + 1):
            if r[2 + dd].strip() and "시간" in r[2 + dd]:
                att[dd] += 1
    return att if valid else {}   # 유효행 0 = 수집 실패 → 빈 dict(그날 조회 시 None=판정 제외)


def scrape_disaster_attendance(page, g_pammgno: str, dates: list[str], progress_cb=print) -> dict[str, int | None]:
    """재난훈련 날짜(YYYY.MM.DD 리스트)의 그날 수급자 출석 인원 → {date: count}.

    2-8 '월간 일정 현황' 탭에서 해당 월로 move_month 이동 후 그리드를 읽는다.
    반환값: 0=출석없음(휴무 추정), N=출석 인원, None=월 수집 실패(판정 제외).
    """
    if not dates:
        return {}
    months = sorted({(int(d[:4]), int(d[5:7])) for d in dates})
    h = build_spa_hash("left_sub2", "/transport/view.monthly_attend_stat",
                       "2-8.월간 입소자, 일정, 서비스 현황", g_pammgno)
    _navigate_spa(page, f"{DN_BASE}#{h}")
    page.wait_for_timeout(2500)
    page.evaluate("() => { const e=[...document.querySelectorAll('li')].find(x=>x.textContent.includes('월간 일정 현황')); if(e) e.click(); }")
    page.wait_for_timeout(2500)
    per_month: dict[tuple[int, int], dict[int, int]] = {}
    for (y, m) in months:
        page.evaluate(f"move_month('{y}','{m:02d}')")
        target, prev_n, d = f"{y}년 {m:02d}월", -1, {}
        for _ in range(25):  # 월변경 후 그리드 로딩 안정화: 셀수 2회 연속 동일 + datearea=목표월
            page.wait_for_timeout(500)
            d = page.evaluate(_ATTEND_GRAB_JS)
            if target in d.get("mon", "") and d.get("n") and d["n"] == prev_n:
                break
            prev_n = d.get("n", -1)
        per_month[(y, m)] = _attend_by_day(d.get("cells", []), calendar.monthrange(y, m)[1])
    out: dict[str, int | None] = {}
    for dstr in dates:
        y, m, day = int(dstr[:4]), int(dstr[5:7]), int(dstr[8:10])
        out[dstr] = per_month.get((y, m), {}).get(day)
    progress_cb(f"  2-8 재난훈련일 출석: {out}")
    return out


def scrape_branch_pages(page, g_pammgno: str, years: list[int], progress_cb=print,
                        cutoff: str | None = None) -> dict:
    """지점 단위 페이지 순회 수집 (연도별/월별)."""
    from datetime import date as _date
    out = {"edu": {}, "checks": {}, "refresher": None, "rights": None,
           "daily": {}, "plan": {}, "opinion": {}, "opened": None}

    # 9-1 기관 지정일자 (오픈일 — 이전 기간은 판정 제외)
    try:
        _goto(page, "master", g_pammgno)
        txt = page.evaluate(GET_TEXT_JS)
        m = re.search(r"기관\s*지정일자[\s\S]{0,30}?(\d{4}\.\d{2}\.\d{2})", txt)
        if m:
            out["opened"] = m.group(1)
            progress_cb(f"  9-1 기관 지정일자: {out['opened']}")
        else:
            progress_cb("  9-1 지정일자 파싱 실패 — 기준일만 사용")
    except Exception as e:
        progress_cb(f"  9-1 수집 실패(기준일만 사용): {e}")

    _goto(page, "edu", g_pammgno)
    for y in years:
        _set_year(page, y)
        out["edu"][str(y)] = page.evaluate(GET_TEXT_JS)
        progress_cb(f"  8-7 교육일지 {y}년 수집")

    # 11번 휴무일 검증: 재난훈련 날짜의 그날 수급자 출석(2-8 월간 일정)을 대조 수집.
    # 실패해도 본 점검은 그대로 — 그 경우 disaster_attend 빈 dict → 11번은 반기별 실시만 판정.
    out["disaster_attend"] = {}
    try:
        dis_dates = sorted({r["date"] for ys in out["edu"].values()
                            for r in parse_edu(ys)["records"] if "재난" in r["name"]})
        if dis_dates:
            out["disaster_attend"] = scrape_disaster_attendance(page, g_pammgno, dis_dates, progress_cb)
    except Exception as e:
        progress_cb(f"  2-8 재난 출석 수집 건너뜀: {e}")

    # 보수교육: 평가 채점 대상은 전년도(매뉴얼 적용기간) — 전년도+당해 모두 수집
    _goto(page, "refresher", g_pammgno)
    out["refresher"] = {}
    ref_years = [y for y in (_date.today().year - 1, _date.today().year) if y >= min(years)]
    for y in ref_years:
        _set_year(page, y)
        out["refresher"][str(y)] = page.evaluate(GET_TEXT_JS)
    progress_cb(f"  8-7-1 보수교육 수집 ({', '.join(map(str, ref_years))})")

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
    out["welfare"] = {}
    _yearly("plan", "5-6 프로그램 계획")
    _yearly("opinion", "5-5 의견수렴")
    _yearly("welfare", "8-1-1 복지대장")

    # 8-10 건강검진: 연도별 × 탭 2개(연간관리현황 + 입사전 제출)
    out["health_pre"] = {}
    try:
        _goto(page, "health", g_pammgno)
        page.evaluate(CLOSE_MODAL_JS)
        for y in years:
            _set_year(page, y)
            out["health"][str(y)] = page.evaluate(GET_TEXT_JS)
            try:
                page.click("text=입사전 건강검진 제출", timeout=8000)
                page.wait_for_timeout(2500)
                out["health_pre"][str(y)] = page.evaluate(GET_TEXT_JS)
                page.click("text=연간관리현황", timeout=8000)
                page.wait_for_timeout(2000)
            except Exception:
                pass
        progress_cb(f"  8-10 건강검진 {len(years)}개년 수집 (2개 탭)")
    except Exception as e:
        progress_cb(f"  8-10 건강검진 수집 실패(건너뜀): {e}")

    # 5-1 프로그램 제공기록 (2026년~, 월간 보기 순회 — 항목 24~26② 실시횟수)
    out["progdaily"] = {}
    try:
        _goto(page, "progdaily", g_pammgno)
        page.evaluate(CLOSE_MODAL_JS)
        page.evaluate("change_view('monthly')")
        page.wait_for_timeout(2500)
        pm_start = max(date(2026, 1, 1), date(int(cutoff[:4]), int(cutoff[5:7]), 1) if cutoff else date(2026, 1, 1))
        y, m = pm_start.year, pm_start.month
        n_m = 0
        while (y, m) <= (_date.today().year, _date.today().month):
            # view_flag 없이 reloadPage하면 일간 뷰로 리셋됨 (매월 1일 하루치만 수집되는 버그)
            page.evaluate(f"reloadPage({{'yy':'{y}','mm':'{m:02d}','dd':'01','view_flag':'monthly'}})")
            txt = ""
            for _ in range(10):  # 월 전환 레이스 방지
                page.wait_for_timeout(700)
                txt = page.evaluate(GET_TEXT_JS)
                if f"{y}년 {m:02d}월" in txt:
                    break
            out["progdaily"][f"{y}-{m:02d}"] = txt
            n_m += 1
            m += 1
            if m > 12:
                y, m = y + 1, 1
        progress_cb(f"  5-1 프로그램 제공기록 {n_m}개월 수집")
    except Exception as e:
        progress_cb(f"  5-1 프로그램 제공기록 수집 실패(건너뜀): {e}")

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

    # 1-4 상담일지 (항목 17①② — 분기별 상담 + 급여반영, 2024~)
    out["consult"] = {}
    try:
        _goto(page, "consult", g_pammgno)
        page.evaluate(CLOSE_MODAL_JS)
        for y in [yy for yy in years if yy >= 2024]:
            page.evaluate(f"reloadPage({{'yy':'{y}','visit_type':'','include_serviceSupply':''}})")
            out["consult"][str(y)] = page.evaluate(CONSULT_PARSE_JS) if _wait_year(page, y) else None
        progress_cb(f"  1-4 상담일지 {len(out['consult'])}개년 수집")
    except Exception as e:
        progress_cb(f"  1-4 상담일지 수집 실패(건너뜀): {e}")

    # 8-5 사례관리 회의록 (항목 29 — 반기별 회의·반영·평가, 2024~)
    out["case"] = {}
    try:
        _goto(page, "case", g_pammgno)
        page.evaluate(CLOSE_MODAL_JS)
        for y in [yy for yy in years if yy >= 2024]:
            page.evaluate(f"reloadPage({{'yy':'{y}'}})")
            out["case"][str(y)] = page.evaluate(GET_TEXT_JS) if _wait_year(page, y) else ""
        progress_cb(f"  8-5 사례관리 회의록 {len(out['case'])}개년 수집")
    except Exception as e:
        progress_cb(f"  8-5 사례관리 수집 실패(건너뜀): {e}")

    # 1-2 전체 기초평가 현황 — 급여제공 결과평가 집계 (항목 34①, 2024~)
    out["result_eval"] = {}
    try:
        _goto(page, "casetotal", g_pammgno)
        page.evaluate(CLOSE_MODAL_JS)
        for y in [yy for yy in years if yy >= 2024]:
            d = f"{y}1231" if y < _date.today().year else _date.today().strftime("%Y%m%d")
            page.evaluate(f"reloadPage({{'date':'{d}'}})")
            out["result_eval"][str(y)] = page.evaluate(EVAL12_JS) if _wait_year(page, y) else None
        progress_cb(f"  1-2 결과평가 집계 {len(out['result_eval'])}개년 수집")
    except Exception as e:
        progress_cb(f"  1-2 결과평가 수집 실패(건너뜀): {e}")

    # 3-1-3 특이사항 '안전관리' 검색 (항목 19③ 부분 — 2026~. 2024~25는 전 지점 입력 관행 없어 제외)
    # 지점마다 기재란이 다르다: 청주는 신체(cdssnch), 서구는 인지관리(cdsinji)에 쓴다.
    # (실측 2026: 서구 신체 0명/인지 8명, 청주 신체 1명/인지 0명)
    # → 한쪽만 검색하면 반대편 관행 지점이 통째로 '미기재'로 뒤집힌다. 둘 다 검색해 합집합으로 본다.
    # 두 칸에 동시에 값이 있으면 AND 로 걸려 0건이 되므로 매 검색마다 반대편 칸을 비운다.
    out["bigo_safety"] = {}
    out["bigo_safety_inji"] = {}
    try:
        _goto(page, "bigo", g_pammgno)
        page.evaluate(CLOSE_MODAL_JS)
        for y in [yy for yy in years if yy >= 2026]:
            e_d = f"{y}1231" if y < _date.today().year else _date.today().strftime("%Y%m%d")
            for fld, key in (("cdssnch", "bigo_safety"), ("cdsinji", "bigo_safety_inji")):
                page.evaluate(f"document.querySelector('#id_sdate').value='{y}0101';"
                              f"document.querySelector('#id_edate').value='{e_d}';"
                              "['cdssnch','cdsinji'].forEach(n => {"
                              "  const el = document.querySelector('input[name='+n+']');"
                              "  if (el) el.value = '';"
                              "});"
                              f"document.querySelector('input[name={fld}]').value='안전관리';"
                              "load_contents_form('carebigoInquiry')")
                page.wait_for_timeout(3000)
                out[key][str(y)] = page.evaluate(GET_TEXT_JS)
        progress_cb(f"  3-1-3 안전관리 특이사항 {len(out['bigo_safety'])}개년 수집(신체+인지)")
    except Exception as e:
        progress_cb(f"  3-1-3 수집 실패(건너뜀): {e}")

    # 3-2 상태변화 기록 (항목 34④ — 주 1회 작성, 월별 순회 2024~)
    out["status"] = {}
    try:
        _goto(page, "status", g_pammgno)
        page.evaluate(CLOSE_MODAL_JS)
        sm = max((cutoff or "2024.01").replace(".", "")[:6], "202401")
        y, m = int(sm[:4]), int(sm[4:6])
        n_m = 0
        while (y, m) <= (_date.today().year, _date.today().month):
            page.evaluate(f"reloadPage({{'yyyymm':'{y}{m:02d}'}})")
            txt = ""
            for _ in range(10):  # 월 전환 레이스 방지 — 화면의 'YYYY년 MM월' 확인
                page.wait_for_timeout(700)
                txt = page.evaluate(GET_TEXT_JS)
                if f"{y}년 {m:02d}월" in txt:
                    break
            out["status"][f"{y}-{m:02d}"] = txt
            n_m += 1
            m += 1
            if m > 12:
                y, m = y + 1, 1
        progress_cb(f"  3-2 상태변화 기록 {n_m}개월 수집")
    except Exception as e:
        progress_cb(f"  3-2 상태변화 수집 실패(건너뜀): {e}")

    # 1-10 연계기록지 발송 리포트 (항목 30② — 퇴소자 연계기록지 제공, 2024~)
    try:
        _goto(page, "connect", g_pammgno)
        page.evaluate(CLOSE_MODAL_JS)
        s0 = (cutoff or "2024.01.01").replace(".", "")
        s0 = max(s0, "20240101")
        e0 = _date.today().strftime("%Y%m%d")
        page.evaluate(f"document.querySelector('#id_s_date').value='{s0}';"
                      f"document.querySelector('#id_e_date').value='{e0}';"
                      "load_contents_form('ptcSendReport')")
        page.wait_for_timeout(2500)
        out["connect"] = page.evaluate(GET_TEXT_JS)
        progress_cb(f"  1-10 연계기록지 수집 ({s0}~{e0})")
    except Exception as e:
        out["connect"] = ""
        progress_cb(f"  1-10 연계기록지 수집 실패(건너뜀): {e}")

    # 1-6 수급자 안전관리 설명 탭 (항목 19④ — 연 1회, 2024~. 기본 탭이라 연도만 전환)
    out["safe"] = {}
    try:
        _goto(page, "guide", g_pammgno)
        for y in [yy for yy in years if yy >= 2024]:
            page.evaluate(f"reloadPage({{'yy':'{y}'}})")
            txt = ""
            if _wait_year(page, y):
                for _ in range(4):
                    txt = page.evaluate(SAFE_TAB_JS)
                    if "총인원" in txt:
                        break
                    page.wait_for_timeout(800)
            out["safe"][str(y)] = txt
        progress_cb(f"  1-6 수급자 안전관리 {len(out['safe'])}개년 수집")
    except Exception as e:
        progress_cb(f"  1-6 수급자 안전관리 수집 실패(건너뜀): {e}")

    # 1-6 이동서비스 안전수칙/차량운행표 탭 (항목 28①② — 퇴소자 포함)
    try:
        out["transport"] = scrape_transport(page, g_pammgno)
        progress_cb(f"  1-6 이동서비스 안전수칙 {len(out['transport'])}명 수집(퇴소자 포함)")
    except Exception as e:
        out["transport"] = []
        progress_cb(f"  1-6 이동서비스 안전수칙 수집 실패(건너뜀): {e}")

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


SAFE_TAB_JS = ("(() => { const el = document.querySelector('#div_safe');"
               " return el ? el.innerText : ''; })()")

# 연도 전환(reloadPage) 후 이전 연도 테이블이 남은 채 조기 수집되는 레이스 방지용 — 화면 표시 연도
YEAR_JS = ("(() => { const el = document.querySelector('.datepicker .datearea');"
           " return el ? el.innerText : ''; })()")


def _wait_year(page, y, tries: int = 10) -> bool:
    """datepicker 표시 연도가 y가 될 때까지 폴링. 성공 시 True."""
    for _ in range(tries):
        page.wait_for_timeout(800)
        page.evaluate(CLOSE_MODAL_JS)
        if str(y) in (page.evaluate(YEAR_JS) or ""):
            page.wait_for_timeout(500)
            return True
    return False


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

TRANSPORT_CLICK_JS = r"""
(() => {
  const mask = document.getElementById('mask_div'); if (mask) mask.style.display = 'none';
  const li = Array.from(document.querySelectorAll('li'))
    .find(e => /div_transport/.test(e.getAttribute('page-info') || ''));
  if (!li) return false;
  li.click(); return true;
})()
"""

TRANSPORT_TOE_JS = r"""
(() => {
  const root = document.getElementById('div_transport'); if (!root) return false;
  const lab = Array.from(root.querySelectorAll('label')).find(e => /퇴소자\s*포함/.test(e.textContent || ''));
  if (!lab) return false;
  const inp = lab.querySelector('input') || document.getElementById(lab.getAttribute('for') || '');
  if (inp) { if (!inp.checked) inp.click(); return true; }
  lab.click(); return true;
})()
"""

# 표 셀 배열(빈칸=미제공 보존): [연번, 현황, 수급자명, 케어그룹, 등급, 급여개시일, 안전수칙일, 차량운행표일]
TRANSPORT_GRID_JS = r"""
(() => {
  const root = document.getElementById('div_transport');
  if (!root) return [];
  const rows = [];
  Array.from(root.querySelectorAll('g-b,tbody tr')).forEach(r => {
    const cells = Array.from(r.querySelectorAll('g-td,td')).map(c => c.textContent.trim().replace(/\s+/g, ' '));
    if (cells.length >= 6) rows.push(cells);
  });
  return rows;
})()
"""


def scrape_transport(page, g_pammgno: str) -> list:
    """1-6 → '이동서비스 안전수칙, 차량운행표' 탭 → 퇴소자 포함 검색 → 표 셀 배열.

    주의: 로딩 마스크(#mask_div)가 클릭을 가로채므로 Playwright click 대신 JS click 사용.
    """
    _goto(page, "guide", g_pammgno)
    page.evaluate(CLOSE_MODAL_JS)
    page.wait_for_timeout(600)
    if not page.evaluate(TRANSPORT_CLICK_JS):
        return []
    rows = []
    for _ in range(10):
        page.wait_for_timeout(1000)
        rows = page.evaluate(TRANSPORT_GRID_JS)
        if rows:
            break
    if not rows:
        return []
    page.evaluate(TRANSPORT_TOE_JS)  # 퇴소자 포함 검색
    for _ in range(8):
        page.wait_for_timeout(900)
        r2 = page.evaluate(TRANSPORT_GRID_JS)
        if len(r2) > len(rows):
            return r2
    return page.evaluate(TRANSPORT_GRID_JS) or rows


def judge_transport(rows: list, cutoff: str, out_scope: set | None = None) -> dict | None:
    """항목 28①② 판정 — 이동서비스 안전수칙·차량운행표 제공.

    rows: scrape_transport 결과 [연번, 현황, 수급자명, 케어그룹, 등급, 급여개시일, 안전수칙일, 차량운행표일]
    out_scope: **평가기간 전 퇴소가 확인된** 수급자명 집합(1-1 스캔 enroll 기준) — 이들만 제외한다.
               스캔에 없는 이름(스캔 이후 신규 입소 등)은 제외하지 않고 포함 = 실제 미흡을 숨기지 않기 위함.
               동명이인은 전원이 기간외일 때만 out_scope 에 넣을 것.
    """
    if not rows:
        return None
    cut = datetime.strptime(cutoff, "%Y.%m.%d").date()

    def _pdate(s):
        try:
            return datetime.strptime((s or "").strip(), "%Y.%m.%d").date()
        except ValueError:
            return None

    miss_rule, miss_sheet, stale, n_target, n_skip = [], [], [], 0, 0
    for c in rows:
        if len(c) < 8:
            continue
        status, name, rule, sheet = c[1], c[2], c[6], c[7]
        if out_scope and name in out_scope:  # 평가기간 전 퇴소 확인된 사람만 제외
            n_skip += 1
            continue
        n_target += 1
        if not _pdate(rule):
            miss_rule.append(f"{name}({status})")
        elif _pdate(rule) < cut:
            stale.append(name)
        if not _pdate(sheet):
            miss_sheet.append(f"{name}({status})")
    n_bad = len(miss_rule) + len(miss_sheet)
    det = (f"[부분판정: ①안전수칙·②차량운행표 제공 / 평가기간 내 재적 {n_target}명"
           f"{f'(기간외 {n_skip}명 제외)' if n_skip else ''}] "
           f"안전수칙 미제공 {len(miss_rule)}명 · 차량운행표 미제공 {len(miss_sheet)}명")
    if miss_rule:
        det += " — 수칙: " + ", ".join(miss_rule[:5])
    if miss_sheet:
        det += " — 운행표: " + ", ".join(miss_sheet[:5])
    if stale:
        det += f" · 제공일이 평가기간({cut:%Y.%m.%d}) 이전 {len(stale)}명(재제공 검토)"
    det += " (③자동차종합보험 유효기간·④직원 수칙 준수 면담은 수기 확인)"
    return {
        "status": "양호" if n_bad == 0 else "미흡",
        "sub_status": {"①": "양호" if not miss_rule else "미흡",
                       "②": "양호" if not miss_sheet else "미흡"},
        "detail": det,
    }


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


def parse_safe(text: str) -> dict:
    """1-6 수급자 안전관리 설명 탭: 요약(총인원/설명/미설명) + [{date, names, staff}]."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    total = done = undone = None
    for i, ln in enumerate(lines):
        if ln == "총인원":
            nums = []
            for s in lines[i:i + 8]:
                m = re.match(r"^(\d+)명$", s)
                if m:
                    nums.append(int(m.group(1)))
            if len(nums) >= 3:
                total, done, undone = nums[0], nums[1], nums[2]
            break
    date_re = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
    person_re = re.compile(r"^(.+?)\((.+?)\)$")
    rows = []
    i = 0
    while i < len(lines):
        if lines[i].isdigit() and i + 1 < len(lines) and date_re.match(lines[i + 1]):
            d = lines[i + 1]
            names = []
            j = i + 2
            while j < len(lines) and person_re.match(lines[j]):
                names.append(person_re.match(lines[j]).group(1))
                j += 1
            if names:
                staff = lines[j] if j < len(lines) and not lines[j].isdigit() else ""
                rows.append({"date": d, "names": names, "staff": staff})
                i = j + 1
                continue
        i += 1
    return {"total": total, "done": done, "undone": undone, "rows": rows}


def parse_case(text: str) -> dict:
    """8-5 사례관리 회의록. 2026 신형: 실시주기 요약(회의/반영/평가 × 상·하반기) + 반기 열 목록.
    2024~25 구형: 분기 보드(1~4분기 작성/미작성) + 목록(연번·일시·수급자·참가자수·작성자·반영 n/m)
    → 반기로 환산. 반환 키는 동일: meeting/reflect/evaluate([상,하]) + rows."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    # 선택 행의 상세 패널(내용보기)은 파싱 제외
    for stop in ("사례관리 회의록 내용보기", "알림사항"):
        if stop in lines:
            lines = lines[:lines.index(stop)]
    out = {"meeting": [None, None], "reflect": [None, None], "evaluate": [None, None], "rows": []}
    ratio_re = re.compile(r"^(\d+)\s*/\s*(\d+)$")
    date_re = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")

    if "실시 주기" in lines:  # ---- 2026 신형 ----
        KEYS = {"사례관리 회의": "meeting", "급여제공 반영": "reflect", "사례관리 회의 평가": "evaluate"}
        for i, ln in enumerate(lines):
            k = KEYS.get(ln)
            if k and out[k] == [None, None] and i + 2 < len(lines):
                out[k] = [lines[i + 1], lines[i + 2]]
        i = 0
        while i < len(lines):
            if lines[i] in ("상반기", "하반기") and i + 1 < len(lines) and date_re.match(lines[i + 1]):
                row = {"half": lines[i], "date": lines[i + 1], "names": "", "sign": "", "reflect": ""}
                for s in lines[i + 2:i + 7]:
                    m = ratio_re.match(s)
                    if m and not row["sign"]:
                        row["sign"] = s.replace(" ", "")
                    elif not row["names"] and not m:
                        row["names"] = s
                out["rows"].append(row)
                i += 2
                continue
            i += 1
        return out

    if "1분기" in lines:  # ---- 2024~25 구형: 분기 보드 → 반기 환산 ----
        try:
            base = lines.index("4분기") + 1
            qs = lines[base:base + 4]
        except ValueError:
            qs = []
        def _half(a, b):
            vals = qs[a:b + 1]
            if len(vals) < 2:
                return None
            n = sum(1 for s in vals if s.startswith("작성"))
            return f"작성({n}건)" if n else "미작성"
        out["meeting"] = [_half(0, 1), _half(2, 3)]
        i = 0
        while i < len(lines):
            if lines[i].isdigit() and i + 1 < len(lines) and date_re.match(lines[i + 1]):
                d = lines[i + 1]
                row = {"half": "상반기" if int(d[5:7]) <= 6 else "하반기",
                       "date": d, "names": "", "sign": "", "reflect": ""}
                for s in lines[i + 2:i + 8]:
                    if date_re.match(s):
                        break
                    m = ratio_re.match(s)
                    if m:
                        row["reflect"] = s.replace(" ", "")
                        break
                    if not row["names"] and not s.isdigit() and ":" not in s and "~" not in s:
                        row["names"] = s
                out["rows"].append(row)
                i += 2
                continue
            i += 1
        # 반영 요약: 행 단위 집계 (반영수>=대상자수 = 완료)
        for hi, half in enumerate(("상반기", "하반기")):
            hr = [r for r in out["rows"] if r["half"] == half]
            if hr:
                ok = 0
                for r in hr:
                    m = ratio_re.match(r["reflect"])
                    if m and int(m.group(1)) >= int(m.group(2)) and int(m.group(2)) > 0:
                        ok += 1
                out["reflect"][hi] = f"{ok} / {len(hr)}"
    return out


def parse_status(text: str, view_ym: str) -> list:
    """3-2 상태변화(월 뷰): 주별 [{start(date), end(date), done, total}].
    주 시작 월이 뷰 월과 같은 주만 반환 (월 경계 중복 제거)."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    vy, vm = int(view_ym[:4]), int(view_ym[5:7])
    week_re = re.compile(r"^(\d{2})\.(\d{2})\s*~\s*(\d{2})\.(\d{2})$")
    ratio_re = re.compile(r"^(\d+)\s*/\s*(\d+)$")
    weeks = []
    for i, ln in enumerate(lines):
        m = week_re.match(ln)
        if m:
            weeks.append((i, m))
        if "작성건수" in ln:
            counts = []
            for s in lines[i + 1:i + 1 + len(weeks) + 2]:
                r = ratio_re.match(s)
                if r:
                    counts.append((int(r.group(1)), int(r.group(2))))
                if len(counts) == len(weeks):
                    break
            out = []
            for (_, wm), (done, total) in zip(weeks, counts):
                sm_, sd_, em_, ed_ = (int(wm.group(k)) for k in range(1, 5))
                sy = vy - 1 if sm_ > vm else vy
                ey = vy + 1 if em_ < vm else vy
                if sm_ != vm:
                    continue  # 주 시작이 뷰 월 밖 → 이전 달 뷰에서 처리 (중복 제거)
                try:
                    out.append({"start": date(sy, sm_, sd_), "end": date(ey, em_, ed_),
                                "done": done, "total": total})
                except ValueError:
                    pass
            return out
    return []


def parse_bigo(text: str) -> list:
    """3-1-3 특이사항 검색 결과: [{name, date}] (검색어 포함 기록만 서버가 반환)."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    date_re = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
    rows = []
    i = 0
    while i < len(lines):
        if (lines[i].isdigit() and i + 3 < len(lines)
                and not date_re.match(lines[i + 1]) and date_re.match(lines[i + 3])):
            rows.append({"name": lines[i + 1], "date": lines[i + 3]})
            i += 4
            continue
        i += 1
    return rows


def parse_connect(text: str) -> dict:
    """1-10 연계기록지: 행(수급자·퇴소일·사유·작성일·제공일·방법) + 발송완료/미발송 집계."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    date_re = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
    total = sent = unsent = None
    flat = re.sub(r"\s+", " ", text)
    m = re.search(r"연계기록지\s*:\s*(\d+)\s*건", flat)
    if m:
        total = int(m.group(1))
    m = re.search(r"발송완료\s*:\s*(\d+)", flat)
    if m:
        sent = int(m.group(1))
    m = re.search(r"미발송\s*:\s*(\d+)", flat)
    if m:
        unsent = int(m.group(1))
    rows, seen = [], set()
    i = 0
    while i < len(lines):
        if (lines[i].isdigit() and i + 2 < len(lines)
                and not date_re.match(lines[i + 1]) and lines[i + 2] in ("남", "여")):
            name = lines[i + 1]
            seg = lines[i + 3:i + 14]
            dates, reason, method = [], "", ""
            for j, s in enumerate(seg):
                if s.isdigit() and j + 2 < len(seg) and seg[j + 2] in ("남", "여"):
                    break  # 다음 행 시작
                if date_re.match(s):
                    dates.append(s)
                elif len(dates) == 2 and not reason:
                    reason = s  # 퇴소일 다음 = 연계사유
                elif len(dates) == 4 and not method:
                    method = s  # 제공일 다음 = 제공방법... (제공자명 앞)
            row = {"name": name, "leave": dates[1] if len(dates) > 1 else "",
                   "reason": reason, "written": dates[2] if len(dates) > 2 else "",
                   "provided": dates[3] if len(dates) > 3 else "", "method": method}
            key = (row["name"], row["written"], row["provided"])
            if key not in seen:
                seen.add(key)
                rows.append(row)
            i += 3
            continue
        i += 1
    return {"total": total, "sent": sent, "unsent": unsent, "rows": rows}


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


# 대장 행 안에 섞여 들어오는 UI 조작 텍스트 — 2~4글자 한글이라 이름 정규식에 그대로 걸린다.
# 실측(둔산점 2026-07-17): 수령인 46명 중 20건이 '조회' 였다(실제 26명). 생일쿠폰 판정은
# '이름이 목록에 있나'만 보므로 가짜 이름이 하나 끼어도 뒤집히진 않지만, 수령인 수가 부풀려진다.
# ★ 관측된 건 '조회' 뿐이고 나머지는 같은 계열이라 예방적으로 넣었다. 실제 수급자·직원 이름은
#   이 목록과 겹치지 않는다(한국 성명은 성+2자 3글자가 대부분이고, 관측된 수령인도 전부 3글자).
_UI_WORDS = {"조회", "수정", "삭제", "등록", "신규", "저장", "확인", "취소",
             "인쇄", "첨부", "목록", "닫기", "선택", "검색", "전체"}


def parse_welfare(text: str) -> dict:
    """8-1-1 복지(포상) 제공대장: 분기별 제공 기록 [{date, title, recipients}]."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    date_re = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
    out = {"1분기": [], "2분기": [], "3분기": [], "4분기": []}
    cur_q = None
    i = 0
    while i < len(lines):
        ln = lines[i]
        if "신규작성" in ln or ln == "알림사항":
            break  # 본문 끝 (하단 버튼·알림 모달)
        if ln in out:
            cur_q = ln
        elif cur_q and date_re.match(ln):
            title = lines[i + 1] if i + 1 < len(lines) else ""
            # 수령인: 제목 다음부터 'n / n' 서명 카운트 전까지의 이름들
            recipients = []
            j = i + 2
            while j < len(lines) and not re.match(r"^\d+\s*/\s*\d+$", lines[j]):
                s = lines[j]
                if date_re.match(s) or s in out or len(s) > 20:
                    break
                if re.match(r"^[가-힣]{2,4}$", s) and s not in _UI_WORDS:
                    recipients.append(s)
                j += 1
            out[cur_q].append({"date": ln, "title": title, "recipients": recipients})
        i += 1
    return out


def parse_prejoin(text: str) -> list:
    """8-10 입사전 건강검진 제출 탭: [{name, join, left, status}]."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    date_re = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
    rows = []
    i = 0
    while i < len(lines):
        # 행: 연번, 이름, 성별, 생년, 직종, 입사일, (퇴사일), 상태
        if (lines[i].isdigit() and i + 2 < len(lines)
                and re.match(r"^[가-힣]{2,4}$", lines[i + 1]) and lines[i + 2] in ("남", "여")):
            name = lines[i + 1]
            join = left = ""
            status = ""
            dates = []
            for j in range(i + 3, min(i + 10, len(lines))):
                s = lines[j]
                if date_re.match(s):
                    dates.append(s)
                elif s in ("작성", "미작성", "항목누락", "퇴사"):
                    status = s
                    break
                elif s.isdigit():
                    break
            # dates: [생년, 입사일, (퇴사일)]
            if len(dates) >= 2:
                join = dates[1]
                if len(dates) >= 3:
                    left = dates[2]
            rows.append({"name": name, "join": join, "left": left, "status": status})
            i += 3
            continue
        i += 1
    return rows


def parse_progdaily(text: str) -> list:
    """5-1 프로그램 제공기록(월간 보기): [{date, type(신체/인지/사회…), journal(✓)}]."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    date_re = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})\(")
    out = []
    i = 0
    while i < len(lines):
        if lines[i] == "알림사항":
            break
        m = date_re.match(lines[i])
        if m:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            ptype, journal = "", False
            for j in range(i + 1, min(i + 12, len(lines))):
                s = lines[j]
                if date_re.match(s):
                    break
                if not ptype and s in ("신체", "인지", "사회", "사회적응", "정서", "가족", "기타"):
                    ptype = s
                if s == "✓":
                    journal = True
                    break
            out.append({"date": d, "type": ptype, "journal": journal})
        i += 1
    return out


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


def analyze_branch_pages(data: dict, cutoff: str, today: date | None = None,
                         branch_name: str = "") -> dict:
    today = today or date.today()
    cut = datetime.strptime(cutoff, "%Y.%m.%d").date()
    # 기관 지정일자(오픈일) 반영 — 개소 전 기간은 판정 제외
    opened = None
    if data.get("opened"):
        try:
            opened = datetime.strptime(data["opened"], "%Y.%m.%d").date()
        except ValueError:
            pass
    eff = max(cut, opened) if opened else cut

    def _period_ok(start_d: date, end_d: date, min_days: int = 30) -> bool:
        """개소일·기준일 이후 해당 기간의 실제 운영일이 min_days 이상일 때만 판정 대상."""
        lo = max(start_d, eff)
        hi = min(end_d, today)
        if lo > hi:
            return False
        return (hi - lo).days >= min_days

    years = sorted(int(y) for y in data.get("edu", {}).keys())

    edu_parsed = {y: parse_edu(data["edu"][str(y)]) for y in years}
    chk_parsed = {y: parse_checks(data["checks"].get(str(y), "")) for y in years}
    # 보수교육: 구버전 raw(str) 호환 — dict{연도: text}로 정규화
    ref_raw = data.get("refresher") or {}
    if isinstance(ref_raw, str):
        ref_raw = {str(today.year): ref_raw}
    refresher_by_year = {int(y): parse_refresher(t) for y, t in ref_raw.items()}
    prev_year = today.year - 1
    # 채점 대상: 전년도(매뉴얼 적용기간). 전년도 데이터 없으면(개소 전 등) 당해로 폴백
    ref_score_year = prev_year if prev_year in refresher_by_year and refresher_by_year[prev_year]["rows"] else today.year
    refresher = refresher_by_year.get(ref_score_year, {"target": None, "done": None, "rows": []})
    refresher_cur = refresher_by_year.get(today.year, {"target": None, "done": None, "rows": []})

    # ---- 항목 11: 재난대응훈련 반기별 (기준일 5/1, 11/1) ----
    disaster_miss = []
    for y in years:
        recs = [r for r in edu_parsed[y]["records"] if "재난" in r["name"]]
        for half, due, lo, hi, h_start, end in (
            ("상반기", date(y, 5, 1), f"{y}.01.01", f"{y}.06.30", date(y, 1, 1), date(y, 6, 30)),
            ("하반기", date(y, 11, 1), f"{y}.07.01", f"{y}.12.31", date(y, 7, 1), date(y, 12, 31)),
        ):
            if today < due or not _period_ok(h_start, end):
                continue
            if not any(lo <= r["date"] <= hi for r in recs):
                disaster_miss.append(f"{y} {half}")

    # 11번 휴무일 검증: 재난훈련일에 그날 수급자 출석이 0명이면 휴무일 훈련 의심(확인요망).
    #   출석>0이면 사람이 있었으니 유효. None(월 미수집)은 판정 제외. 자동 미흡 아닌 '주의'.
    disaster_warn = []
    da = data.get("disaster_attend") or {}
    for y in years:
        for r in edu_parsed[y]["records"]:
            if "재난" in r["name"] and da.get(r["date"]) == 0:
                disaster_warn.append(f"{r['date']}(출석0)")

    # ---- 항목 19①: 노인인권 교육 (2026~ 반기별 / 2024~25 연1회) ----
    rights_miss = []
    rights_note = []
    for y in years:
        recs = [r for r in edu_parsed[y]["records"] if "노인인권" in r["name"] or "학대" in r["name"]]
        if y >= 2026:
            # 매뉴얼상 '반기별 1회' 기준은 2026.1월부터 적용
            for half, lo, hi, h_start, end in (
                ("상반기", f"{y}.01.01", f"{y}.06.30", date(y, 1, 1), date(y, 6, 30)),
                ("하반기", f"{y}.07.01", f"{y}.12.31", date(y, 7, 1), date(y, 12, 31)),
            ):
                if not _period_ok(h_start, end):
                    continue
                has = any(lo <= r["date"] <= hi for r in recs)
                if not has:
                    if end < today:
                        rights_miss.append(f"{y} {half}")
                    elif date(today.year, today.month, 1) > datetime.strptime(lo, "%Y.%m.%d").date():
                        rights_note.append(f"{y} {half} 미작성(진행중)")
        else:
            # 2024~2025는 연 1회 기준 — 반기로 판정하면 허위 미흡(예: 하반기만 실시)
            if _period_ok(date(y, 1, 1), date(y, 12, 31)):
                if not any(f"{y}.01.01" <= r["date"] <= f"{y}.12.31" for r in recs):
                    if y < today.year:
                        rights_miss.append(f"{y}년 노인인권교육 없음(연1회 기준)")
                    else:
                        rights_note.append(f"{y}년 노인인권교육 미실시(진행중)")
        # 서명 미완: n/m 합계 기준
        for r in recs:
            if r["sign"] and r["sign"][1] - r["sign"][0] > 0:
                rights_note.append(f"{r['date']} 서명 {r['sign'][0]}/{r['sign'][1]}")

    # ---- 항목 6: 운영규정 교육(연1회) + 급여제공지침교육(연1회) + 신규직원 7일 ----
    edu6_miss = []
    for y in years:
        if not _period_ok(date(y, 1, 1), date(y, 12, 31)):
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
    ref_miss = [r["name"] for r in refresher["rows"]
                if r["status"] == "미작성" and r["name"] not in EXCLUDE_STAFF]
    ref_target, ref_done = refresher.get("target"), refresher.get("done")
    ref_cur_miss = [r["name"] for r in refresher_cur["rows"]
                    if r["status"] == "미작성" and r["name"] not in EXCLUDE_STAFF]

    # ---- 항목 13: 소방시설 월 1회 (매월 28일 기준) ----
    fire_miss = []
    for y in years:
        fire = chk_parsed[y]["fire"]
        if not fire:
            fire_miss.append(f"{y}년 데이터 파싱 실패")
            continue
        for mth in range(1, 13):
            m_end = date(y, mth, 28)
            if m_end < eff or m_end > today:
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
            q_start = date(y, mm - 2, 1)
            if q_end > today or not _period_ok(q_start, q_end):
                continue
            if dis and not dis[q - 1]:
                dis_miss.append(f"{y} {q}분기")
            if med and not med[q - 1]:
                med_miss.append(f"{y} {q}분기")

    # ---- 항목 16①③: 6-2 일일점검 (간호비품 매일 · 위생점검 매일, 전일까지) ----
    supply_miss_m, hygiene_miss_m = [], []
    for ym_key in sorted(data.get("daily", {})):
        y, m = int(ym_key[:4]), int(ym_key[5:7])
        if (y, m) < (eff.year, eff.month):
            continue  # 개소·기준일 이전 달 제외
        d = parse_daily(data["daily"][ym_key], ym_key)
        lo = eff.day if (y, m) == (eff.year, eff.month) else 1
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
    prog_plan_miss = {t: [] for t in PROG_TYPES}   # ① 연간계획
    prog_op_miss = {t: [] for t in PROG_TYPES}     # ③ 의견수렴·반영
    prog_note = {t: [] for t in PROG_TYPES}
    for y in sorted(plan_parsed):
        # 5-5/5-6은 2026년 개편 화면부터 유형별 관리 — 이전 연도는 구버전이라 제외
        if y < 2026 or not _period_ok(date(y, 1, 1), date(y, 12, 31)):
            continue
        for t in PROG_TYPES:
            if plan_parsed.get(y) is not None and not plan_parsed[y].get(t):
                # 프로그램 연간계획서는 수기(종이) 파일로 수립·보관 → 케어포 미등록이어도
                # 예외 인정하고 ①은 충족 처리 (사용자 확정 2026-07-10).
                # 미흡(prog_plan_miss)이 아니라 노트로 남겨야 ② '①미수립 연동 불인정'도 걸리지 않음.
                prog_note[t].append(f"{y} 연간계획 케어포 미등록 — 수기 파일 보관(예외 인정)")
            op = op_parsed.get(y, {}).get(t, {"collect_dates": [], "reflect": False})
            for half, lo_d, hi_d, h_start, end in (
                ("상반기", f"{y}.01.01", f"{y}.06.30", date(y, 1, 1), date(y, 6, 30)),
                ("하반기", f"{y}.07.01", f"{y}.12.31", date(y, 7, 1), date(y, 12, 31)),
            ):
                if end > today or not _period_ok(h_start, end):
                    continue
                if not any(lo_d <= dd <= hi_d for dd in op["collect_dates"]):
                    prog_op_miss[t].append(f"{y} {half} 의견수렴 없음")
            if y < today.year and not op["reflect"]:
                prog_op_miss[t].append(f"{y} 의견반영 없음")
            elif y == today.year and not op["reflect"]:
                prog_note[t].append(f"{y} 의견반영 미작성(진행중)")
    # ② 실시횟수 (5-1 제공기록, 2026년~): 신체·인지 주3회, 사회적응 월1회 — 일지(✓) 작성분만 인정
    prog_exec_miss = {t: [] for t in PROG_TYPES}
    prog_records = []
    for _ym, txt in (data.get("progdaily") or {}).items():
        prog_records += parse_progdaily(txt)
    if prog_records or data.get("progdaily"):
        TYPE_KEY = {"신체기능": "신체", "인지기능": "인지", "사회적응": "사회"}
        exec_start = max(date(2026, 1, 1), eff)
        for t in PROG_TYPES:
            tk = TYPE_KEY[t]
            recs = [r for r in prog_records if r["type"].startswith(tk) and r["journal"]]
            if t in ("신체기능", "인지기능"):
                wk = exec_start - timedelta(days=exec_start.weekday())  # 시작 주 월요일
                if wk < exec_start:
                    wk += timedelta(days=7)  # 부분 주 제외 — 전년 12월 미수집이라 12/29주 등 허위 미달 방지
                while wk + timedelta(days=6) <= today:
                    cnt = sum(1 for r in recs if wk <= r["date"] <= wk + timedelta(days=6))
                    if cnt < 3:
                        prog_exec_miss[t].append(f"{wk.month}/{wk.day}주 {cnt}회")
                    wk += timedelta(days=7)
            else:
                mc = date(exec_start.year, exec_start.month, 1)
                while (mc.year, mc.month) < (today.year, today.month):
                    cnt = sum(1 for r in recs if (r["date"].year, r["date"].month) == (mc.year, mc.month))
                    if cnt < 1:
                        prog_exec_miss[t].append(f"{mc.year}-{mc.month:02d} 0회")
                    mc = date(mc.year + (1 if mc.month == 12 else 0), (mc.month % 12) + 1, 1)
            # ① 연간계획 미수립 시 ② 연동 불인정 (매뉴얼)
            if prog_plan_miss[t] and not prog_exec_miss[t]:
                prog_exec_miss[t].append("①미수립 → ② 연동 불인정")

    def _cap(lst, n=6):
        return lst[:n] + ([f"외 {len(lst) - n}건"] if len(lst) > n else [])

    prog_miss = {t: prog_plan_miss[t] + _cap(prog_exec_miss[t]) + prog_op_miss[t] for t in PROG_TYPES}

    # ---- 항목 7: 직원인권 보호지침 (2026 신설 — 2026년부터) ----
    rights = parse_rights(data.get("rights") or "")
    r7_missing, r7_late = [], []
    for r in rights["rows"]:
        if r["left_before"]:
            continue  # 안내일 이전 퇴소자 제외 (사용자 확정)
        if r["status"] == "보류":
            continue  # 보류자 제외 — 수급 보류 중이라 연1회 안내 대상 아님(사용자 확정 2026-07-18, 전지점, 일단 보류)
        if not r["provided"]:
            r7_missing.append(f"{r['name']}({r['status']})")
        elif r["start"] >= "2026" and r["provided"] > r["start"]:
            # 2026년 급여개시 수급자: 개시일까지 안내돼 있어야 (미리 안내는 정상)
            r7_late.append(f"{r['name']} 개시{r['start']}→제공{r['provided']}")

    # ---- 항목 19④: 수급자 안전관리 설명 연 1회 (1-6 안전관리 탭, 2024~) ----
    safe_parsed = {int(y): parse_safe(t) for y, t in (data.get("safe") or {}).items() if t}
    safe_miss, safe_note = [], []
    for y in sorted(safe_parsed):
        if not _period_ok(date(y, 1, 1), date(y, 12, 31)):
            continue
        s = safe_parsed[y]
        if s["total"] is None:
            safe_note.append(f"{y}년 안전관리 요약 파싱 실패")
            continue
        if s["undone"]:
            # 1-6 '안전관리 설명' 탭 기준 — 3-1-3 기록지 문구(③)와 혼동 방지 위해 출처 명시
            msg = f"{y}년 [1-6 설명탭] 미설명 {s['undone']}명(설명 {s['done']}/총 {s['total']})"
            if y < today.year:
                safe_miss.append(msg)
            else:
                safe_note.append(msg + "(진행중)")
    # 신규수급자(2026~) 급여개시 14일 이내 설명 대조 — 개시일은 직원인권 탭 rows 재사용
    if safe_parsed and rights["rows"]:
        # 설명탭 이름은 구분자 없음('김경자'), 로스터는 동명이인 구분자 포함('김경자(각)') → 괄호 제거 후 대조
        def _base(n: str) -> str:
            return re.sub(r"\(.*?\)", "", n or "").strip()

        expl = {}
        for s in safe_parsed.values():
            for r in s["rows"]:
                for nm in r["names"]:
                    expl.setdefault(_base(nm), []).append(r["date"])
        # 케어포 설명탭이 '미설명 0명'이면 기관 집계상 전원 설명 완료 →
        # 이름대조 실패는 표기·집계 차이일 수 있으므로 미흡이 아니라 확인용 노트로만 남긴다.
        cur = safe_parsed.get(today.year)
        tab_all_done = bool(cur and cur.get("total") is not None and not cur.get("undone"))
        for rr in rights["rows"]:
            if not rr["start"] or rr["start"][:4] < "2026" or rr["left_before"]:
                continue
            try:
                sd = datetime.strptime(rr["start"], "%Y.%m.%d").date()
            except ValueError:
                continue
            due = sd + timedelta(days=14)
            dates = sorted(dd for dd in expl.get(_base(rr["name"]), []) if dd >= rr["start"])
            if not dates:
                if today > due:
                    if tab_all_done:
                        safe_note.append(f"{rr['name']} 신규(개시 {rr['start']}) 설명기록 미확인 — 설명탭 미설명 0명(확인용)")
                    else:
                        safe_miss.append(f"{rr['name']} 신규(개시 {rr['start']}) 설명 없음")
            elif datetime.strptime(dates[0], "%Y.%m.%d").date() > due:
                safe_note.append(f"{rr['name']} 신규 14일 초과(개시 {rr['start']}→설명 {dates[0]})")
    if len(safe_miss) > 8:
        safe_miss = safe_miss[:8] + [f"외 {len(safe_miss) - 8}건"]
    safe_note = safe_note[:8]

    # ---- 항목 17①②: 분기별 상담 + 급여반영 연1회 (1-4 상담일지, 2024~) ----
    consult_raw = data.get("consult") or {}
    consult_miss, consult2_miss, consult_note = [], [], []
    consult_detail = {}
    Q_END_DAY = (31, 30, 30, 31)
    for ys in sorted(consult_raw):
        c = consult_raw[ys]
        if not c or not c.get("rows"):
            continue
        y = int(ys)
        consult_detail[y] = {"header": c.get("header", []), "banner": c.get("banner", "")}
        # ① 분기별 상담: 완료된 분기만, 페이지 자체 집계(상담수급자수/대상자수) 기준
        for qi in range(4):
            q_start, q_end = date(y, qi * 3 + 1, 1), date(y, qi * 3 + 3, Q_END_DAY[qi])
            if q_end >= today or not _period_ok(q_start, q_end):
                continue
            hdr = c["header"][qi] if qi < len(c.get("header", [])) else ""
            m = re.match(r"(\d+)\s*/\s*(\d+)", hdr)
            if not m:
                continue
            done_n, tot_n = int(m.group(1)), int(m.group(2))
            if done_n < tot_n:
                names = [r["name"] for r in c["rows"]
                         if len(r.get("q", [])) > qi and not r["q"][qi]][:5]
                consult_miss.append(f"{y} {qi + 1}분기 {done_n}/{tot_n}명"
                                    + (f" (미상담 후보: {', '.join(names)})" if names else ""))
        # ② 급여반영: 연 1회 기준 (내부 목표는 분기 1건 — 당해연도는 현황 노트)
        csh_total = sum(r.get("csh") or 0 for r in c["rows"])
        if y < today.year:
            if csh_total < 1 and _period_ok(date(y, 1, 1), date(y, 12, 31)):
                consult2_miss.append(f"{y}년 급여반영 0건")
        else:
            consult_note.append(f"{y}년 급여반영 {csh_total}건 (내부 목표: 분기 1건)")
    if len(consult_miss) > 8:
        consult_miss = consult_miss[:8] + [f"외 {len(consult_miss) - 8}건"]

    # ---- 항목 29: 사례관리 회의 반기별 + 급여반영·평가 (8-5, 2024~) ----
    case_parsed = {int(y): parse_case(t) for y, t in (data.get("case") or {}).items() if t}
    case_miss, case_note = [], []
    for y in sorted(case_parsed):
        cp = case_parsed[y]
        for hi, (half, h_start, h_end) in enumerate((("상반기", date(y, 1, 1), date(y, 6, 30)),
                                                     ("하반기", date(y, 7, 1), date(y, 12, 31)))):
            if not _period_ok(h_start, h_end):
                continue
            meet = cp["meeting"][hi] or ""
            done = meet.startswith("작성")
            if h_end >= today:
                if not done and today > h_start + timedelta(days=60):
                    case_note.append(f"{y} {half} 회의 미작성(진행중)")
                continue
            if not done:
                case_miss.append(f"{y} {half} 회의 미작성")
                continue
            in_grace = (today - h_end).days <= 30  # 반기말 회의의 반영·평가 30일 기한 미도래 가능
            m = re.match(r"(\d+)\s*/\s*(\d+)", cp["reflect"][hi] or "")
            if m and int(m.group(1)) < int(m.group(2)):
                (case_note if in_grace else case_miss).append(
                    f"{y} {half} 급여반영 {m.group(1)}/{m.group(2)}" + ("(기한 진행중)" if in_grace else ""))
            if y >= 2026:
                ev = cp["evaluate"][hi] or ""
                m2 = re.match(r"(\d+)\s*/\s*(\d+)", ev)
                if ev == "미작성" or (m2 and int(m2.group(1)) < int(m2.group(2))):
                    (case_note if in_grace else case_miss).append(
                        f"{y} {half} 회의평가 미완({ev})" + ("(기한 진행중)" if in_grace else ""))
        for r in cp["rows"]:
            ms = re.match(r"(\d+)/(\d+)", r.get("sign") or "")
            if ms and int(ms.group(1)) < int(ms.group(2)):
                case_note.append(f"{r['date']} 회의 서명 {r['sign']}")
    case_note = case_note[:8]

    # ---- 항목 34④: 상태변화 주1회 기록 (3-2, 2024~) ----
    status_weeks = []
    for ym in sorted(data.get("status") or {}):
        status_weeks += parse_status((data["status"] or {})[ym], ym)
    status_miss = []
    for w in status_weeks:
        if w["end"] >= today or w["start"] < eff:
            continue  # 진행중 주·개소 전 주 제외
        if w["total"] and w["done"] < w["total"]:
            status_miss.append(f"{w['start'].strftime('%y.%m.%d')}주 {w['done']}/{w['total']}")
    n_status_miss = len(status_miss)
    if n_status_miss > 10:
        status_miss = status_miss[:10] + [f"외 {n_status_miss - 10}주"]

    # ---- 항목 34①: 급여제공 결과평가 (1-2 집계 — 2024~25 연1회, 2026~ 반기 1회) ----
    def _ratio12(s):
        m = re.match(r"(\d+)\s*/\s*(\d+)", s or "")
        return (int(m.group(1)), int(m.group(2))) if m else None

    eval_miss, eval_note = [], []
    for ys in sorted(data.get("result_eval") or {}):
        ev = (data["result_eval"] or {}).get(ys)
        if not ev:
            continue
        y = int(ys)
        if ev.get("kind") == "year":
            r = _ratio12(ev.get("y"))
            if r and _period_ok(date(y, 1, 1), date(y, 12, 31)) and r[0] < r[1]:
                if y < today.year:
                    eval_miss.append(f"{y}년 결과평가 {r[0]}/{r[1]}")
                else:
                    eval_note.append(f"{y}년 결과평가 {r[0]}/{r[1]}(진행중)")
        else:
            for hi, (half, hs, he) in enumerate((("상반기", date(y, 1, 1), date(y, 6, 30)),
                                                 ("하반기", date(y, 7, 1), date(y, 12, 31)))):
                r = _ratio12(ev.get("h1") if hi == 0 else ev.get("h2"))
                if not r or not _period_ok(hs, he):
                    continue
                if he < today:
                    if r[0] < r[1]:
                        eval_miss.append(f"{y} {half} 결과평가 {r[0]}/{r[1]}")
                else:
                    eval_note.append(f"{y} {half} 결과평가 {r[0]}/{r[1]}(진행중)")

    # ---- 항목 19③(부분): 특이사항 '안전관리' 교육 입력 (3-1-3, 연 1회 가정) ----
    # 기재란은 지점 관행에 따라 신체(cdssnch) 또는 인지관리(cdsinji)다 — 둘 중 어디든 있으면 기재로 본다.
    safety_edu_miss, safety_edu_note = [], []
    for ys in sorted(data.get("bigo_safety") or {}):
        y = int(ys)
        if y < 2026 or not _period_ok(date(y, 1, 1), date(y, 12, 31)):
            continue  # 안전관리 문구 입력은 2026년 시작 관행 — 이전 연도 판정 시 허위 미흡
        have = {r["name"] for r in parse_bigo((data["bigo_safety"] or {}).get(ys) or "")}
        # 인지란은 신 수집분에만 있다(구 raw 는 키 없음) → 있을 때만 합집합
        have |= {r["name"] for r in parse_bigo((data.get("bigo_safety_inji") or {}).get(ys) or "")}
        c = (data.get("consult") or {}).get(ys) or {}
        roster = {r["name"] for r in (c.get("rows") or []) if r.get("stat") in ("수급중", "보류")}
        if not roster:
            safety_edu_note.append(f"{y}년 [3-1-3 기록지] '안전관리' 문구 기재 {len(have)}명(명단 대조 불가)")
            continue
        missing = sorted(roster - have)
        if missing:
            # 1-6 '설명탭'(④)과 다른 지표임을 문구로 분리 — 기재/대상 인원도 함께 표기
            msg = (f"{y}년 [3-1-3 기록지] 특이사항(신체·인지란) '안전관리' 문구 미기재 {len(missing)}명"
                   f"(기재 {len(roster & have)}/대상 {len(roster)}명)"
                   f"({', '.join(missing[:5])}{'…' if len(missing) > 5 else ''})")
            if y < today.year:
                safety_edu_miss.append(msg)
            else:
                safety_edu_note.append(msg + "(진행중)")

    # ---- 항목 30②: 퇴소자 연계기록지 작성·제공 (1-10, 2024~) ----
    connect = parse_connect(data.get("connect") or "")
    conn_miss = []
    if data.get("connect"):
        eff_s = eff.strftime("%Y.%m.%d")
        # 인수 시점 일괄 퇴소(기준일 이전) 건은 제외 — 천안 2024.05.30 일괄 퇴소 등
        judge_rows = [r for r in connect["rows"] if r["leave"] and r["leave"] > eff_s]
        un_names = [r["name"] for r in judge_rows if not r["provided"]]
        if un_names:
            conn_miss.append(f"미발송 {len(un_names)}건 ({', '.join(un_names[:5])}{'…' if len(un_names) > 5 else ''})")
        for r in judge_rows:
            # 계약 종료일(퇴소일)까지 제공해야 인정 — 기한 초과 = 미흡
            if r["provided"] and r["provided"] > r["leave"]:
                conn_miss.append(f"{r['name']} 퇴소 {r['leave']}→제공 {r['provided']}(기한초과)")
        if len(conn_miss) > 8:
            conn_miss = conn_miss[:8] + [f"외 {len(conn_miss) - 8}건"]

    # ---- 항목 15: 건강검진 ①연간(연1회, 항목누락 포함) + ②입사전 제출 ----
    health_parsed = {int(y): parse_health(t) for y, t in (data.get("health") or {}).items()}
    prejoin_parsed = {int(y): parse_prejoin(t) for y, t in (data.get("health_pre") or {}).items()}
    health_miss, health_note = [], []
    for y in sorted(health_parsed):
        if not _period_ok(date(y, 1, 1), date(y, 12, 31)):
            continue
        rows = health_parsed[y]["rows"]
        if not rows:
            continue
        # ① 연간: 미작성 + 항목누락(작성했지만 세부자료 미입력 — "눌렀을 때 자료가 있어야") — 재직·휴직 대상
        # 점검용 계정(관리팀·평가자)은 제외
        miss_names = [r["name"] for r in rows
                      if r["health"] == "미작성" and r["status"] != "퇴사" and r["name"] not in EXCLUDE_STAFF]
        incomplete = [r["name"] for r in rows
                      if r["health"] == "항목누락" and r["status"] != "퇴사" and r["name"] not in EXCLUDE_STAFF]
        # 교차검증: 페이지 상단 '작성/항목누락/대상' 집계와 행 파싱 결과 대조 (제외계정 무관 원본끼리)
        counts = health_parsed[y].get("counts")
        raw_incomplete = len([r for r in rows if r["health"] == "항목누락"])
        if counts and counts[1] != raw_incomplete:
            health_note.append(f"{y}년 항목누락 집계 불일치(페이지 {counts[1]} vs 파싱 {raw_incomplete}) — 확인 필요")
        if y < today.year:
            if miss_names:
                health_miss.append(f"{y}년 미작성 {len(miss_names)}명({', '.join(miss_names[:8])}{'…' if len(miss_names) > 8 else ''})")
            if incomplete:
                health_miss.append(f"{y}년 항목누락 {len(incomplete)}명({', '.join(incomplete[:5])})")
        else:
            if miss_names:
                health_note.append(f"{y}년 미작성 {len(miss_names)}명(연내 진행중)")
            if incomplete:
                health_miss.append(f"{y}년 항목누락 {len(incomplete)}명({', '.join(incomplete[:5])})")
        # ② 입사전 제출: 재직 신규입사자가 입사일 지나도록 미작성 → 미흡 (점검용 계정 제외)
        for r in prejoin_parsed.get(y, []):
            if r["left"] or r["status"] == "작성" or r["name"] in EXCLUDE_STAFF:
                continue
            if r["join"] and datetime.strptime(r["join"], "%Y.%m.%d").date() <= today:
                health_miss.append(f"입사전 미제출: {r['name']}(입사 {r['join']})")

    # ---- 항목 8③: 복지(포상) 분기별 1회 이상 (8-1-1 대장) ----
    welfare_parsed = {int(y): parse_welfare(t) for y, t in (data.get("welfare") or {}).items()}
    welfare_miss, welfare_note = [], []
    birthday_log = {}  # {"YYYY-MM": [수령인]} — 노션 생일쿠폰 대조용
    q_defs = {1: ("1분기", 1, (3, 31)), 2: ("2분기", 4, (6, 30)), 3: ("3분기", 7, (9, 30)), 4: ("4분기", 10, (12, 31))}
    for y in sorted(welfare_parsed):
        wq = welfare_parsed[y]
        for q, (qname, sm, (em, ed)) in q_defs.items():
            q_start, q_end = date(y, sm, 1), date(y, em, ed)
            if not _period_ok(q_start, q_end):
                continue
            recs = wq.get(qname, [])
            if q_end <= today:
                if not recs:
                    welfare_miss.append(f"{y} {qname}")
            elif q_start <= today and not recs:
                welfare_note.append(f"{y} {qname} 미제공(진행중)")
        for qname, recs in wq.items():
            for r in recs:
                # 제목에 '생일' 이 있으면 생일쿠폰 기록으로 본다. ★월은 제목이 아니라 '날짜'에서 얻는다.
                #
                # 제목 문구가 지점 자율이라 여기에 기대면 계속 터진다 — 실제로 두 번 터졌다:
                #   · 2026-07-16: '쿠폰'을 강제 → '4월 생일'만 쓰는 지점의 대장이 통째로 비어
                #     노션 생일자 전원이 '미지급 의심'으로 뒤집힘.
                #   · 그래서 '(\d+)월\s*생일' 로 바꿨더니 이번엔 둔산점('생일쿠폰' — 월 표기 없음)의
                #     대장이 통째로 비어 39건 전건이 오탐이 됐다(2026-07-17 실측. 대장엔 13개월분
                #     지급기록이 멀쩡히 있었다). 고친 게 반대 방향으로 재발한 것이다.
                # 관측된 제목: '4월 생일'(청주·천안) / '생일쿠폰'(둔산) — 공통분모는 '생일'뿐이고,
                # 월은 어느 지점이든 기록 날짜에 항상 있다.
                if "생일" not in (r["title"] or "") or not r["recipients"]:
                    continue
                m = re.search(r"(\d+)월\s*생일", r["title"])
                if m:
                    ym = f"{y}-{int(m.group(1)):02d}"   # 제목에 월이 있으면 그게 지점이 밝힌 대상월
                else:
                    dm = re.match(r"(\d{4})\.(\d{2})\.", r.get("date") or "")
                    if not dm:
                        continue                        # 날짜도 제목도 월을 못 주면 건너뛴다
                    ym = f"{dm.group(1)}-{dm.group(2)}"  # 없으면 지급 날짜의 월
                birthday_log.setdefault(ym, []).extend(r["recipients"])

    def st(miss):
        return "양호" if not miss else "미흡"

    # 항목 6 기준별 분리: ②운영규정 교육(신규직원 기한 포함) / ③급여제공지침 교육
    e6_op = [e for e in edu6_miss if "운영규정" in e]
    e6_guide = [e for e in edu6_miss if "급여제공지침" in e]
    e6_op_cur = [e for e in e6_op if "진행중" not in e]
    e6_guide_cur = [e for e in e6_guide if "진행중" not in e]

    item_results = {}
    # 항목 28은 평가기간 내 재적(in_scope) 판단에 1-1 스캔 enroll 데이터가 필요 →
    # collector.py 에서 judge_transport() 로 계산해 병합한다.

    if welfare_parsed:
        # ②가산금 80% 사용: '2026 평가종료월 다음 해부터' 적용이라 현 정기평가엔 미적용 +
        # 가산금 미지급 기관은 충족(Y) → 두 갈래 모두 충족이라 자동 충족 처리(32번 백신 특례와 같은 구조).
        # 80% 기준이 실제 적용되는 시점(2027~)부터는 가산금 지급 기관의 80% 사용 증빙을 수기 확인해야 한다.
        item_results["8"] = {
            "status": st(welfare_miss),
            "detail": "[부분판정: ③분기별 복지] "
                      + (("누락: " + ", ".join(welfare_miss)) if welfare_miss else "분기별 복지(포상) 제공 충족")
                      + (" / " + "; ".join(welfare_note) if welfare_note else "")
                      + " · [②가산금] 80% 사용 기준은 2026 평가종료월 다음 해부터 적용 → 현 정기평가"
                        "(청주·천안·둔산 2026·서구 2027) 미적용 + 가산금 미지급 기관은 충족 → 충족(Y) 자동 처리"
                        "(적용 시점부터 지급기관 80% 사용 증빙 수기 확인 필요)"
                      + " (①5대보험·④고충면담·⑤퇴직금은 수기 · 생일쿠폰 노션 대조는"
                        " 클라우드 실행 시 아래에 추가됨 — 로컬은 토큰 없어 생략)",
            "sub_status": {"③": st(welfare_miss), "②": "양호"},
        }
    if health_parsed:
        item_results["15"] = {
            "status": st(health_miss),
            "sub_status": {"①": st(health_miss)},
            "detail": "①연간(항목누락 포함)+②입사전 제출: "
                      + ("; ".join(health_miss + health_note) or "전 직원 검진 작성·입사전 제출 확인")
                      + " (75% 이상 부분점수 2.25점은 채점 시 판단)",
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
            "sub_status": {"②": st(r7_missing + r7_late)},
            "detail": "2026년 기준 — " + " · ".join(parts),
        }
    if data.get("status") or data.get("result_eval"):
        sub34 = {}
        if data.get("result_eval"):
            sub34["①"] = st(eval_miss)
        if data.get("status"):
            sub34["④"] = st(status_miss)
        item_results["34"] = {
            "status": st(eval_miss + status_miss),
            "sub_status": sub34,
            "detail": f"[①결과평가·④주1회 상태변화({len(status_weeks)}주 검사)] "
                      + ("; ".join(eval_miss + status_miss) or "결과평가·주간 기록 충족")
                      + (" / " + "; ".join(eval_note) if eval_note else "")
                      + " (기록 충실성·②30일 재작성·③기록지 제공은 수기)",
        }
    if data.get("connect"):
        item_results["30"] = {
            "status": st(conn_miss),
            "sub_status": {"②": st(conn_miss)},
            "detail": f"[부분판정: ②연계기록지] 작성 {connect['total'] or 0}건 · 발송완료 {connect['sent'] or 0}건 — "
                      + ("; ".join(conn_miss) or "전건 기한 내 발송 완료")
                      + " (미작성 퇴소자 존재 여부·①동행진료 기록은 수기 확인)",
        }
    if case_parsed:
        item_results["29"] = {
            "status": st(case_miss),
            "sub_status": {"①": st([m for m in case_miss if "회의 미작성" in m]),
                           "②": st([m for m in case_miss if "급여반영" in m or "회의평가" in m])},
            "detail": "[①반기 회의·②급여반영/평가] "
                      + ("; ".join(case_miss) or "반기별 회의·급여반영·평가 충족")
                      + (" / " + "; ".join(case_note) if case_note else "")
                      + " (3인 참여·직원별 의견·30일 기한 준수는 회의록 팝업 수기 확인)",
        }
    if consult_detail:
        # 17③ 월간 계획표·식단표·소식 월1회 제공: 지점별 네이버 블로그에 게시(사용자 확정 2026-07-18).
        #   네이버가 외부 검색·fetch를 막아 자동 게시 판정은 불가 → 블로그 링크를 달아 '주의(확인요망)'로만.
        blog = CARING_BLOG.get(branch_name)
        blog_txt = (f" · [③월간소식] 지점 블로그 게시 — 확인요망: {blog}" if blog
                    else " · [③월간소식] 블로그 미등록 — 수기 확인")
        item_results["17"] = {
            "status": st(consult_miss + consult2_miss),
            "sub_status": {"①": st(consult_miss), "②": st(consult2_miss), "③": "주의"},
            "detail": "[①분기별 상담·②급여반영 연1회] "
                      + ("; ".join(consult_miss + consult2_miss) or "완료 분기 전 수급자 상담·급여반영 충족")
                      + (" / " + "; ".join(consult_note) if consult_note else "")
                      + blog_txt,
        }

    item_results |= {
        "5": {
            "status": st(ref_miss),
            "sub_status": {"①": st(ref_miss)},
            "detail": (f"[채점연도 {ref_score_year}] 대상 {ref_target}명 중 작성 {ref_done}명"
                       + (f", 미작성: {', '.join(ref_miss)}" if ref_miss else " — 전원 이수/작성")
                       + (f" / {today.year}년 진행: 미작성 {len(ref_cur_miss)}명"
                          + (f"({', '.join(ref_cur_miss)})" if ref_cur_miss else "")
                          if ref_score_year != today.year else "")),
        },
        "6": {
            "status": st(edu6_cur),
            "sub_status": {"②": st(e6_op_cur), "③": st(e6_guide_cur)},
            "detail": ("; ".join(edu6_miss) or "운영규정·급여제공지침 교육 연 1회 충족")
                      + " (①지침 12항목 비치는 수기 확인)",
        },
        "11": {
            "status": "미흡" if disaster_miss else ("주의" if disaster_warn else "양호"),
            "sub_status": {"①": st(disaster_miss)},
            "detail": (("누락: " + ", ".join(disaster_miss)) if disaster_miss else "반기별 재난대응훈련 실시 확인")
                      + (f" · ★휴무일 훈련 의심(그날 수급자 출석 0명) {len(disaster_warn)}건: "
                         + ", ".join(disaster_warn) + " — 확인요망" if disaster_warn else ""),
        },
        # 12① 응급 매뉴얼·비상연락체계: 기본 비치항목 → 전지점 자동 충족(사용자 확정 2026-07-18).
        #    ②알림장치·③④면담은 수기.
        "12": {
            "status": "양호",
            "sub_status": {"①": "양호"},
            "detail": "[①응급 매뉴얼·비상연락체계] 기본 비치항목 → 전지점 충족(Y) 자동 처리 "
                      "(②알림장치 설치·③④면담은 수기)",
        },
        # 13② 피난안내도: 본사에서 지점별로 부착 → 전지점 자동 충족(사용자 확정 2026-07-18).
        "13": {
            "status": st(fire_miss),
            "sub_status": {"①": st(fire_miss), "②": "양호"},
            "detail": (("소방점검 누락: " + ", ".join(fire_miss)) if fire_miss else "매월 소방시설 점검 입력 확인")
                      + " · [②피난안내도] 본사 부착 → 전지점 충족(Y) 자동 처리",
        },
        "16": {
            "status": st(supply_miss_m + hygiene_miss_m + dis_miss),
            "sub_status": {"①": st(supply_miss_m), "②": st(dis_miss), "④": st(hygiene_miss_m)},
            "detail": ("① 간호비품 미작성: " + (", ".join(supply_miss_m) or "없음")
                       + " · ② 정기소독 누락: " + (", ".join(dis_miss) or "없음")
                       + " · ④ 위생점검일지 미작성: " + (", ".join(hygiene_miss_m) or "없음")
                       + " (③감염 대응체계는 수기)"),
        },
        "23": {
            "status": st(med_miss),
            "sub_status": {"②": st(med_miss)},
            "detail": "[부분판정: ②분기점검만] 일반의약품 분기 점검 "
                      + (("누락: " + ", ".join(med_miss)) if med_miss else "충족")
                      + " (①보관함 잠금·③적정투약은 현장/수기 확인)",
        },
        "19": {
            "status": st(rights_miss + safe_miss + safety_edu_miss),
            "sub_status": {"①": st(rights_miss)}
                          | ({"④": st(safe_miss)} if safe_parsed else {})
                          | ({"③": st(safety_edu_miss)} if data.get("bigo_safety") else {}),
            "detail": "[부분판정: ①교육일지" + ("·④안전관리 설명" if safe_parsed else "")
                      + ("·③기록지 안전관리 문구" if data.get("bigo_safety") else "") + "] "
                      + (("누락: " + ", ".join(rights_miss + safe_miss + safety_edu_miss))
                         if (rights_miss or safe_miss or safety_edu_miss)
                         else "반기별 노인인권 교육" + ("·수급자 안전관리 설명(연1회)" if safe_parsed else "") + " 확인")
                      + (" / " + "; ".join(rights_note + safe_note + safety_edu_note)
                         if (rights_note or safe_note or safety_edu_note) else "")
                      + " (②숙지·⑤존중은 면담 · ③은 연1회 입력 가정 — 자동점수 제외, 확인용)",
        },
    }
    has_exec = bool(data.get("progdaily"))
    for no, t, freq in (("24", "신체기능", "주3회"), ("25", "인지기능", "주3회"), ("26", "사회적응", "월1회")):
        sub = {"①": st(prog_plan_miss[t]), "③": st(prog_op_miss[t])}
        if has_exec:
            sub["②"] = st(prog_exec_miss[t])
        item_results[no] = {
            "status": st(prog_miss[t]),
            "sub_status": sub,
            "detail": (f"[①계획·②{freq}·③의견, 2026년~] " if has_exec else "[부분판정: ①계획·③의견, 2026년~] ")
                      + ("; ".join(prog_miss[t] + prog_note[t]) or f"연간계획·{freq} 실시·의견수렴/반영 충족")
                      + ("" if has_exec else f" (②{freq} 실시는 다음 단계)"),
        }

    return {
        "item_results": item_results,
        "opened": data.get("opened"),
        "detail": {
            "edu_records": {y: edu_parsed[y]["records"] for y in years},
            "newstaff": cur_ns,
            "refresher": refresher,
            "checks": {y: chk_parsed[y] for y in years},
            "rights": rights,
            "safe": safe_parsed,
            "consult": consult_detail,
            "case": case_parsed,
            "connect": connect,
            "status": {"weeks": len(status_weeks), "miss_weeks": n_status_miss},
            "result_eval": data.get("result_eval"),
            "welfare": welfare_parsed,
            "birthday_log": birthday_log,
            "daily_miss": {"supply": supply_miss_m, "hygiene": hygiene_miss_m},
            "programs": {"plan": plan_parsed, "opinion": op_parsed},
            "health": health_parsed,
        },
    }
