# -*- coding: utf-8 -*-
"""이번달 매출 극대화 점검 — 8시간 미만 급여일·근소차·보류자 마지막 일정 (지점별 로컬 HTML).

무엇을 잡나:
  1) 현재 수급자(수급중+보류) 전수 대상.
  2) 급여일(급여수가>0, 비급여·한도초과·미이용 제외) 중 실제 급여시간이 8시간 미만인 날의 이용일수.
     - 특히 7:40~7:59(8시간에 20분 이내로 아깝게 못 넘긴) '근소차' 날을 강조.
     - 8시간 구간으로 연장 시 늘어나는 급여수가 차액(잠재 매출)을 등급별 관측수가로 추정.
  3) 보류자는 마지막으로 일정이 있었던 날(언제부터 일정이 비었는지).
  4) 전월과 비교(8h미만 일수·근소차·총급여일 증감).

데이터 소스(케어포 2-8 '월간 입소자, 일정, 서비스 현황'):
  - '월간 이동서비스 현황' 탭: 일자별·수급자별 [실제 급여시간 + 급여수가 + 송영차량] → 급여 원장.
  - '월간 일정 현황' 탭: [수급자 × 일자] 시간 매트릭스 → 등급 매핑 + 보류자 마지막 일정 역순 스캔.
  - 1-1 수급자 정보관리: 상태(수급중/보류) 로스터.

산출물: 평가준비/<지점>/매출점검_<지점>_<YYYYMM>.html  (공유 없음, 실명 포함 로컬 전용)
스냅샷: %LOCALAPPDATA%/carefor-auto/revenue_history/<지점>_<YYYYMM>.json (개인정보 로컬 보관)

사용:
  py -X utf8 revenue_check.py                # 4지점 전체, 이번달
  py -X utf8 revenue_check.py 둔산            # 둔산점만
  py -X utf8 revenue_check.py 전체 2026-07    # 대상월 지정
"""
from __future__ import annotations

import html
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# 이 스크립트는 매출정보라 OneDrive 밖 저장 폴더에 둘 수 있다(온디맨드 유실 방지).
# 케어포 접속 모듈은 공개 저장소 carefor-auto 에서 가져오므로 sys.path 에 그 경로를 추가한다.
# (저장 폴더 위치와 무관하게 carefor-auto 절대경로를 우선 사용, 없으면 상대경로 폴백)
_REPO = Path(r"C:\Users\alsgm\OneDrive\Desktop\클로드코드\carefor-auto")
if not (_REPO / "src").exists():
    _REPO = Path(__file__).resolve().parent.parent / "carefor-auto"
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from playwright.sync_api import sync_playwright

from src.config import Config, config_path, app_data_dir
from src.carefor_client import build_spa_hash, _navigate_spa, extract_g_pammgno
from audit.explore_pages import login

DN_BASE = "https://dn.carefor.co.kr/"

# 산출물 루트 = 이 스크립트가 있는 '매출점검' 폴더 (지점 하위폴더에 저장)
OUT_ROOT = Path(__file__).resolve().parent


def out_dir(key: str) -> Path:
    """매출점검/<지점>/ 폴더(없으면 생성) 반환. 매출정보는 여기 로컬 전용으로만 보관."""
    d = OUT_ROOT / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def manual_exclude(key: str) -> set:
    """계약상 8시간 미만 이용자(연장 대상 아님) 수동 제외 명단.

    파일: 매출점검/<지점>/_8시간미만계약제외.txt (한 줄 한 명, # 뒤는 주석).
    스캔으로 '계약 시간'을 못 잡아 자동 구분 불가 → 사용자가 직접 넣는다.
    """
    return _read_namelist(out_dir(key) / "_8시간미만계약제외.txt")


def manual_hold_exclude(key: str) -> set:
    """보류 현황에서 뺄 보류자 명단(예: 복귀 예정·재개 확정). 파일: 매출점검/<지점>/_보류제외.txt.
    보류자 표에서만 숨김(매출·급여일과 무관)."""
    return _read_namelist(out_dir(key) / "_보류제외.txt")


def _read_namelist(f) -> set:
    if not f.exists():
        return set()
    names = set()
    for ln in f.read_text(encoding="utf-8").splitlines():
        ln = ln.split("#")[0].strip()
        if ln:
            names.add(ln)
    return names


def patient_manage_url(g_pammgno: str) -> str:
    """1-1 수급자 정보관리 SPA URL. (audit.collector 의존 없이 자립 — scan_inpage.js 미필요)"""
    h = build_spa_hash("left_sub1", "/share/patient/view.patient_manage",
                       "1-1.수급자 정보관리", g_pammgno)
    return f"{DN_BASE}#{h}"

# ── 시간/구간 유틸 ─────────────────────────────────────────────────────────
NEAR_MISS_LO = 460   # 7시간 40분 (8시간에 20분 이내 = 근소차)
FULL_8H = 480        # 8시간


def parse_minutes(t: str) -> int | None:
    """'8시간 11분'/'7시간'/'6시간 27분' → 분. '미이용' 포함/빈칸이면 None."""
    if not t or "미이용" in t:
        return None
    h = re.search(r"(\d+)\s*시간", t)
    m = re.search(r"(\d+)\s*분", t)
    if not h and not m:
        return None
    return (int(h.group(1)) if h else 0) * 60 + (int(m.group(1)) if m else 0)


def tier_of(mins: int) -> str:
    """급여제공시간 구간(주야간보호 수가 구간)."""
    if mins < 180:
        return "3h미만"
    if mins < 360:
        return "3~6h"
    if mins < 480:
        return "6~8h"
    if mins < 600:
        return "8~10h"
    if mins < 720:
        return "10~12h"
    return "12h+"


def parse_amt(s: str) -> int | None:
    s = s.replace(",", "").strip()
    return int(s) if s.isdigit() else None


# ── 케어포 네비게이션 ──────────────────────────────────────────────────────
def _open_2_8(page, g_pammgno: str) -> None:
    h = build_spa_hash("left_sub2", "/transport/view.monthly_attend_stat",
                       "2-8.월간 입소자, 일정, 서비스 현황", g_pammgno)
    _navigate_spa(page, f"{DN_BASE}#{h}")
    page.wait_for_timeout(2500)


def _click_tab(page, name: str) -> None:
    page.evaluate(
        "(name)=>{const e=[...document.querySelectorAll('li')].find(x=>x.textContent.trim()===name);"
        "if(e)e.click();}", name)
    page.wait_for_timeout(2500)


# 7-1 합계 행의 칸 순서 (실측 2026-07-22, 천안점).
#  헤더 21칸: [chk,연번,수급자명,등급/본인부담률,일수, 공단부담금,본인부담금,
#              식사재료비,간식비,이·미용비,진료약제비,기타비용,등급외/한도초과,
#              부담금합계,선납적용액,당월총액,이전미납액,청구액,청구입금액,처리,(빈)]
#  합계행 17칸: 앞 5칸(chk~일수)이 '총 N 건' 하나로 병합 → 합계[i] = 헤더[i+4]
#  검산: 부담금합계 = 본인부담금+식사+간식+이미용+진료약제+기타+등급외한도초과 (천안 18,721,610 일치)
NONPAY_COLS = [(3, "식사재료비"), (4, "간식비"), (5, "이미용비"),
               (6, "진료약제비"), (7, "기타비용"), (8, "등급외한도초과")]


def scrape_7_1_nonpay(page, progress=print) -> dict:
    """7-1 합계 행에서 비급여(공단급여·본인부담금 제외) 항목별 금액을 뽑는다.
    ⚠️ 7-1은 **청구월 = 전월** 기준이다(당월은 아직 청구 전) — 표기할 때 밝힐 것."""
    try:
        rows = page.evaluate(
            "()=>[...document.querySelectorAll('table.frame_list_tbl tr')]"
            ".map(tr=>[...tr.querySelectorAll('td,th')].map(c=>c.innerText.trim()))")
        tot = next((r for r in rows if r and r[0].startswith("총 ") and len(r) >= 15), None)
        if not tot:
            progress("  7-1 비급여: 합계 행을 못 찾음")
            return {}
        def _n(i):
            return int(re.sub(r"[^\d]", "", tot[i]) or 0)
        out = {label: _n(i) for i, label in NONPAY_COLS}
        out["본인부담금"] = _n(2)
        out["공단부담금"] = _n(1)
        out["부담금합계"] = _n(9)
        out["비급여계"] = sum(out[l] for _, l in NONPAY_COLS)
        # 검산 — 합이 안 맞으면 열 위치가 바뀐 것이므로 조용히 넘기지 말고 알린다
        if out["본인부담금"] + out["비급여계"] != out["부담금합계"]:
            progress(f"  ⚠️ 7-1 비급여 검산 불일치 "
                     f"(본인 {out['본인부담금']:,} + 비급여 {out['비급여계']:,} "
                     f"≠ 부담금합계 {out['부담금합계']:,}) — 열 위치 확인 필요")
        progress(f"  7-1 비급여(청구월): {out['비급여계']:,}원 "
                 f"(식사 {out['식사재료비']:,} · 간식 {out['간식비']:,} · "
                 f"진료약제 {out['진료약제비']:,} · 등급외한도초과 {out['등급외한도초과']:,})")
        return out
    except Exception as e:
        progress(f"  7-1 비급여 수집 실패: {e}")
        return {}


def scrape_7_1_billed(page, g_pammgno: str, progress=print) -> int:
    """7-1 본인부담금 청구관리(청구월=전월 자동)에서 급여비용(공단부담+본인부담) 총액 = 공단 청구기준 매출.
    열: [chk,연번,수급자명,등급/본인부담률,일수,급여비용공단,급여비용본인,...,등급외/한도초과].
    급여비용 공단+본인 = 한도초과·등급외가 자동으로 빠진 순수 급여비용(등급외자는 공단/본인=0)."""
    try:
        h = build_spa_hash("left_sub7", "/share/cost/view.cost_master", "7-1.본인부담금 청구관리", g_pammgno)
        _navigate_spa(page, f"{DN_BASE}#{h}")
        page.wait_for_timeout(4500)
        rows = page.evaluate("()=>[...document.querySelectorAll('table.frame_list_tbl tr.cr, g-tr')]"
                             ".map(tr=>[...tr.querySelectorAll('td,g-td')].map(td=>td.innerText.trim()))")
        billed = 0
        for r in rows:
            if len(r) < 13 or not re.fullmatch(r"[가-힣]{2,4}", (r[2] or "").strip()):
                continue
            billed += int(re.sub(r"[^\d]", "", r[5]) or 0) + int(re.sub(r"[^\d]", "", r[6]) or 0)
        progress(f"  7-1 급여비용(공단+본인, 청구월): {billed:,}원")
        return billed
    except Exception as e:
        progress(f"  7-1 급여비용 수집 실패: {e}")
        return 0


_GRAB_CELLS = (
    "() => { const root=[...document.querySelectorAll('[id^=\"div_monthly\"]')]"
    ".find(e=>e.offsetParent!==null && e.querySelectorAll('g-td').length)||document.body;"
    " const de=document.querySelector('.datearea');"
    " return {mon:(de?de.innerText:'').trim(),"
    " cells:[...root.querySelectorAll('g-td')].map(x=>x.innerText.trim())}; }"
)


def _grab_month(page, y: int, m: int) -> list[str]:
    """현재 활성 탭에서 대상월로 이동 후 그리드 셀(innerText) 배열을 안정화하여 반환.

    move_month 는 탭(하위 뷰)이 로드된 뒤 정의되므로, 호출 전 정의를 기다린다.
    """
    page.wait_for_function("typeof move_month === 'function'", timeout=20000)
    target, d = f"{y}년 {m:02d}월", {}
    # ★move_month 를 한 번만 부르면 화면이 아직 준비 전이라 조용히 무시될 때가 있다
    #   (실측: 초기 월에서 안 움직여 직전 달 값이 그 달로 저장됐다). 안 바뀌면 다시 부른다.
    for attempt in range(3):
        page.evaluate(f"move_month('{y}','{m:02d}')")
        prev = -1
        for _ in range(140):        # 최대 ~70초 (실측 청주 2024-10 = 50초)
            page.wait_for_timeout(500)
            d = page.evaluate(_GRAB_CELLS)
            n = len(d.get("cells", []))
            if target in d.get("mon", "") and n and n == prev:
                break
            prev = n
        if target in d.get("mon", ""):
            break
        page.wait_for_timeout(1500)
    # ★대상월로 실제로 바뀌었는지 확인하고, 아니면 예외를 낸다.
    #   확인 없이 반환하면 **직전 달 데이터를 그 달 것처럼 저장**한다 —
    #   실측(2026-07-22) 89개월 중 4개월이 이렇게 옆 달과 완전히 같은 값으로 들어갔다.
    #   조용히 틀린 값보다 시끄럽게 실패하는 편이 낫다.
    if target not in d.get("mon", ""):
        raise RuntimeError(f"{target} 로 월 이동 실패(화면: {d.get('mon', '?')}) — 값을 쓰지 않음")
    # ★★월 이름표만 믿으면 안 된다 — 라벨이 먼저 바뀌고 표는 이전 달인 채로 남는 경우가 있다
    #   (실측: 라벨 2026년 01월인데 내용은 02월치였다). 표의 '일(요일)' 을 달력과 대조한다.
    cells = d.get("cells", [])
    seen = {}
    for c in cells:
        mt = _DATE.match(c)
        if mt:
            seen.setdefault(int(c[:2]), c[3])
    if seen:
        import calendar
        wk = "월화수목금토일"
        bad = [f"{dd}일={w}(달력:{wk[calendar.weekday(y, m, dd)]})"
               for dd, w in sorted(seen.items())
               if dd <= calendar.monthrange(y, m)[1]
               and w != wk[calendar.weekday(y, m, dd)]]
        if bad:
            raise RuntimeError(f"{target} 표 내용이 다른 달이다 ({', '.join(bad[:3])}) — 값을 쓰지 않음")
    return cells


# ── 파싱: 월간 이동서비스 현황 → 일자별 급여 레코드 ─────────────────────────
_DATE = re.compile(r"^\d{2}\([월화수목금토일]\)$")


def parse_transport(cells: list[str]) -> list[dict]:
    """'월간 이동서비스 현황' 셀 스트림 → 급여 레코드 리스트.

    레코드 경계 = 급여수가(AMT) 셀. 실제 급여시간 = AMT 바로 앞의 마지막 TIME 셀.
    (송영 0/1/2대 등 가변 열이어도 이 규칙은 일관됨 — 실측 1578건 검증.)
    """
    recs: list[dict] = []
    buf: list[str] = []
    cur_day: int | None = None
    plate = re.compile(r"^\d{2,3}[가-힣]\d{4}$")
    for c in cells:
        if _DATE.match(c):
            cur_day = int(c[:2])
            buf = []
            continue
        buf.append(c)
        amt = parse_amt(c)
        if amt is None:
            continue
        # 레코드 종료(AMT). 버퍼에서 필드 추출.
        name = buf[0] if buf and re.fullmatch(r"[가-힣]{2,4}", buf[0]) else None
        times = [x for x in buf[:-1] if ("시간" in x or "분" in x)]
        billed = parse_minutes(times[-1]) if times else None
        used_transport = any(plate.match(x) for x in buf)
        raw_time = times[-1] if times else ""
        buf = []
        if name is None or billed is None or cur_day is None or amt <= 0:
            continue  # 요약행·미이용·비정상 레코드 제외
        recs.append({"day": cur_day, "name": name, "min": billed,
                     "amt": amt, "raw": raw_time, "transport": used_transport})
    return recs


# ── 파싱: 월간 일정 현황 → {이름: (등급, 이용일 집합)} ──────────────────────
def parse_schedule(cells: list[str], ndays: int) -> dict[str, dict]:
    cols = 3 + ndays + 1
    out: dict[str, dict] = {}
    for i in range(0, len(cells), cols):
        r = cells[i:i + cols]
        if len(r) < cols or not re.match(r"^\d+$", r[0]) or "등급" not in r[2]:
            continue
        name, grade = r[1].strip(), r[2].strip()
        days = {dd for dd in range(1, ndays + 1)
                if parse_minutes(r[2 + dd]) is not None}
        rec = out.setdefault(name, {"grade": grade, "days": set()})
        rec["days"] |= days
        rec["last_day"] = max(days) if days else rec.get("last_day")
    return out


# ── 로스터: 1-1 수급자 상태 ────────────────────────────────────────────────
def scrape_roster(page, g_pammgno: str):
    """(상태맵, 등급맵, 정보맵). 1-1 목록 열: [연번,현황,수급자명,케어그룹,성별,나이,등급,인정만료].
    현황=td[1], 수급자명=td[2], 등급=td[6]. data-info: pshdate=보류전환일, pamdiss=주요질환, pamindt=급여개시일.
    등급·정보는 출석과 무관하게 전원 존재(보류자 포함)."""
    _navigate_spa(page, patient_manage_url(g_pammgno))
    page.wait_for_timeout(2500)
    rows = page.evaluate(r"""
    (() => {
      const out=[];
      document.querySelectorAll('table.frame_list_tbl tr.cr').forEach(tr=>{
        const tds=[...tr.querySelectorAll('td')].map(td=>td.textContent.trim());
        let name=null, psh=null, dis=null, start=null;
        const di=tr.getAttribute('data-info');
        if(di){ try{const d=JSON.parse(di); name=d.pamname; psh=d.pshdate; dis=d.pamdiss; start=d.pamindt;}catch(e){} }
        const stat = tds.length>=3 ? tds[1] : '';
        if(!name && tds.length>=3) name=tds[2];
        const grade = tds.length>=7 ? tds[6] : '';
        if(name) out.push({name, stat, grade, psh, dis, start});
      });
      return out;
    })()
    """)

    def _ymd(s):
        s = (s or "").strip()
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if re.fullmatch(r"\d{8}", s) else ""

    status_map, grade_map, info_map = {}, {}, {}
    for r in rows:
        nm = (r.get("name") or "").strip()
        if not nm:
            continue
        status_map[nm] = (r.get("stat") or "").strip()
        gr = (r.get("grade") or "").strip()
        if gr and "등급" in gr:
            grade_map[nm] = gr
        info_map[nm] = {"since": _ymd(r.get("psh")), "start": _ymd(r.get("start")),
                        "disease": (r.get("dis") or "").strip()}
    return status_map, grade_map, info_map


# ── 지점 수집 ──────────────────────────────────────────────────────────────
MONTHS_BACK = 13  # 보류자 마지막 일정 역순 스캔 범위(13개월 = 1년 초과 판별)

# 이력수정 팝업에서 보류(pshstat=E) 행의 변경사유(pshcont) = 보류 사유
_READ_REASON_JS = r"""()=>{
  const inputs=[...document.querySelectorAll('input')];
  let reason='';
  for(const el of inputs){ const m=(el.name||'').match(/^pshstat_(\d+)$/);
    if(m && el.value==='E'){ const c=document.querySelector('input[name=pshcont_'+m[1]+']'); if(c && c.value) reason=c.value; } }
  if(!reason){ const c=inputs.find(el=>/^pshcont_/.test(el.name||'')&&el.value); if(c)reason=c.value; }
  return reason;
}"""

_CHECK_DISCHARGED_JS = r"""(()=>{const l=[...document.querySelectorAll('label')].find(e=>e.textContent.includes('퇴소자 포함'));
  if(l){const i=l.querySelector('input'); if(i&&!i.checked) i.click();}})()"""


def collect_hold_reasons(page, g_pammgno: str, hold_names: list, progress=print) -> dict:
    """보류자별 보류 사유 수집. 1-1 목록→수급자 클릭→'이력수정' 팝업→pshcont(변경사유) 읽기."""
    reasons = {}

    def goto_list():
        _navigate_spa(page, patient_manage_url(g_pammgno))
        page.wait_for_timeout(2200)
        page.evaluate(_CHECK_DISCHARGED_JS)
        page.wait_for_timeout(2200)

    goto_list()  # 워밍업(첫 대상 누락 방지)
    for nm in hold_names:
        try:
            goto_list()
            clicked = page.evaluate(
                "(name)=>{const tr=[...document.querySelectorAll('table.frame_list_tbl tr.cr')]"
                ".find(t=>[...t.querySelectorAll('td')].some(td=>td.textContent.trim()===name));"
                "if(tr){const c=[...tr.querySelectorAll('td')].find(td=>td.textContent.trim()===name);"
                "(c||tr).click();return true;}return false;}", nm)
            if not clicked:
                reasons[nm] = ""
                continue
            page.wait_for_timeout(2500)
            page.evaluate("()=>{const b=[...document.querySelectorAll('button,a,span')].find(e=>e.textContent.trim()==='이력수정');if(b)b.click();}")
            page.wait_for_timeout(1800)
            r = page.evaluate(_READ_REASON_JS)
            if not r:  # 1회 재시도
                page.wait_for_timeout(1500)
                r = page.evaluate(_READ_REASON_JS)
            reasons[nm] = r or ""
        except Exception:
            reasons[nm] = ""
    progress(f"  보류 사유 {sum(1 for v in reasons.values() if v)}/{len(hold_names)}건 수집")
    return reasons


def collect_branch(page, g_pammgno: str, y: int, m: int, py: int, pm: int,
                   progress=print) -> dict:
    """대상월/전월 급여 레코드 + 등급·보류자 마지막 일정 수집.

    순서 주의: 로그인 직후 2-8을 먼저 연다(2-8 뷰 스크립트 move_month 로드 보장).
    1-1(로스터)로 이동하면 2-8 재진입 시 스크립트 미로드가 생기므로, 일정 매트릭스를
    미리 역순으로 캐시해두고 로스터는 맨 마지막에 조회한다.
    """
    import calendar

    # 1) 이동서비스 현황(대상월·전월) — 로그인 직후 2-8
    _open_2_8(page, g_pammgno)
    _click_tab(page, "월간 이동서비스 현황")
    cur_recs = parse_transport(_grab_month(page, y, m))
    prev_recs = parse_transport(_grab_month(page, py, pm))
    # 등록된 일정 전체 기준(오늘·미래 예정 포함). 사용자 확정 2026-07-20: 오늘 제외하지 않음.
    progress(f"  이동서비스 급여건 — 이번달 {len(cur_recs)} / 전월 {len(prev_recs)} (등록일정 전체)")

    # 2) 월간 일정 현황 — 등급 매핑 + 마지막 일정 역순 캐시(대상월부터 13개월)
    _click_tab(page, "월간 일정 현황")
    sched: dict[tuple[int, int], dict] = {}
    yy, mm = y, m
    for _step in range(MONTHS_BACK):
        sched[(yy, mm)] = parse_schedule(_grab_month(page, yy, mm),
                                         calendar.monthrange(yy, mm)[1])
        mm -= 1
        if mm == 0:
            yy, mm = yy - 1, 12
    grade_of = {nm: v["grade"] for nm, v in sched[(y, m)].items()}
    progress(f"  월간 일정 {MONTHS_BACK}개월 캐시 (등급 {len(grade_of)}명)")

    # 3) 로스터(1-1) — 맨 마지막 이동(2-8 재진입 불필요). 상태+등급+보류정보(전원, 출석무관).
    roster, roster_grade, roster_info = scrape_roster(page, g_pammgno)
    progress(f"  로스터 {len(roster)}명 (등급 {len(roster_grade)}명)")

    # 4) 보류자 마지막 일정 + 등급 = 캐시된 일정에서 최근월부터 탐색
    hold_names = [nm for nm, st in roster.items() if "보류" in st]
    last_sched: dict[str, str | None] = {}
    hold_grade: dict[str, str] = {}
    order = sorted(sched.keys(), reverse=True)  # 최근월 → 과거월
    for nm in hold_names:
        found, grade = None, None
        for (sy, sm2) in order:
            d = sched[(sy, sm2)].get(nm)
            if not d:
                continue
            if grade is None and d.get("grade"):
                grade = d["grade"]                       # 마지막 다닌 달의 등급
            if found is None and d.get("last_day"):
                found = f"{sy}-{sm2:02d}-{d['last_day']:02d}"
            if found and grade:
                break
        last_sched[nm] = found
        # 등급 우선순위: 1-1 로스터(출석무관·전원) > 마지막 다닌 달 스케줄 > 대상월 스케줄
        hold_grade[nm] = roster_grade.get(nm) or grade or grade_of.get(nm, "?")
    progress(f"  보류자 {len(hold_names)}명 마지막 일정·등급 확인")

    # 로스터 등급으로 grade_of 보강(스케줄에 없던 사람도 등급 확보)
    grade_of = {**roster_grade, **grade_of}

    # 등급외자 매출 제외(공단 급여 아님, 사용자 지정 2026-07-20)
    oob = {nm for nm, gr in grade_of.items() if "등급외" in (gr or "")}
    if oob:
        n0c, n0p = len(cur_recs), len(prev_recs)
        cur_recs = [r for r in cur_recs if r["name"] not in oob]
        prev_recs = [r for r in prev_recs if r["name"] not in oob]
        progress(f"  등급외 {len(oob)}명 매출 제외: 이번달 {n0c}→{len(cur_recs)}, 전월 {n0p}→{len(prev_recs)}")

    # 5) 보류 사유(이력수정 팝업의 변경사유) 수집
    hold_reasons = collect_hold_reasons(page, g_pammgno, hold_names, progress)
    hold_info = {nm: {**roster_info.get(nm, {}), "reason": hold_reasons.get(nm, "")}
                 for nm in hold_names}

    # 6) 7-1 급여비용(공단+본인) — 청구월(=전월) 실측 = 청구기준 매출. 당월은 비율로 추정.
    billed_prev = scrape_7_1_billed(page, g_pammgno, progress)
    # 6-1) 같은 화면의 합계 행에서 비급여(식사재료비·간식비·진료약제비·기타·등급외한도초과) 수집.
    #      scrape_7_1_billed 가 이미 7-1 로 이동해 놨으므로 재이동 없이 바로 읽는다.
    nonpay = scrape_7_1_nonpay(page, progress)

    return {"roster": roster, "cur": cur_recs, "prev": prev_recs,
            "grade_of": grade_of, "hold_last": last_sched, "hold_grade": hold_grade,
            "hold_info": hold_info, "billed_prev": billed_prev, "nonpay": nonpay}


# ── 집계·판정 ──────────────────────────────────────────────────────────────
def _median(xs: list[int]) -> int | None:
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) // 2


def aggregate(data: dict, excl: set | frozenset = frozenset()) -> dict:
    """지점 원자료 → 수급자별 집계 + 근소차 수가차액 추정 + 전월비교.

    excl: 계약상 8시간 미만 이용자 명단 → 근소차·잠재매출(기회)에서 제외(급여일·매출·8h미만 건수는 사실대로 유지, 플래그만).
    """
    cur, prev = data["cur"], data["prev"]
    grade_of = data["grade_of"]

    # 8h구간 관측수가 테이블 (사람별 / 등급별) — 연장 시 목표수가 추정용
    person_8h: dict[str, list[int]] = {}
    grade_8h: dict[str, list[int]] = {}
    for r in cur:
        if 480 <= r["min"] < 600:  # 8~10h 구간에서 실제 청구된 수가
            person_8h.setdefault(r["name"], []).append(r["amt"])
            grade_8h.setdefault(grade_of.get(r["name"], "?"), []).append(r["amt"])

    def est_8h_amt(name: str) -> int | None:
        own = person_8h.get(name)
        if own:
            return _median(own)
        return _median(grade_8h.get(grade_of.get(name, "?"), []))

    # 수급자별 집계
    def summarize(recs: list[dict]) -> dict[str, dict]:
        agg: dict[str, dict] = {}
        for r in recs:
            a = agg.setdefault(r["name"], {"pay_days": 0, "u8_days": 0, "o8_days": 0,
                                           "near_days": 0,
                                           "t68": 0, "near_dates": [], "transport": False,
                                           "gain": 0, "gain_est": False})
            a["pay_days"] += 1
            a["transport"] = a["transport"] or r["transport"]
            if r["min"] >= FULL_8H:
                # 8h이상 — 총급여일 = 8h이상 + 8h미만 이 딱 맞아떨어지게 별도 집계
                # (사용자 요청 2026-07-22: 비교할 때 8h이상 건수도 필요)
                a["o8_days"] += 1
            if r["min"] < FULL_8H:
                a["u8_days"] += 1
                if 360 <= r["min"] < 480:
                    a["t68"] += 1
                if NEAR_MISS_LO <= r["min"] < FULL_8H:
                    a["near_days"] += 1
                    a["near_dates"].append((r["day"], r["raw"], r["amt"]))
                    est = est_8h_amt(r["name"])
                    if est and est > r["amt"]:
                        a["gain"] += est - r["amt"]
                        a["gain_est"] = True
        return agg

    cur_agg = summarize(cur)
    prev_agg = summarize(prev)
    # 계약상 8h미만 제외자: 기회 지표(근소차·잠재매출) 0 처리 + 플래그(급여일·매출·u8건수는 유지)
    for ag in (cur_agg, prev_agg):
        for nm in excl:
            if nm in ag:
                ag[nm]["excl"] = True
                ag[nm]["near_days"] = 0
                ag[nm]["near_dates"] = []
                ag[nm]["gain"] = 0
    return {"cur_agg": cur_agg, "prev_agg": prev_agg}


# ── HTML 렌더 ──────────────────────────────────────────────────────────────
def _hhmm(mins: int) -> str:
    return f"{mins // 60}:{mins % 60:02d}"


def render_html(branch: str, y: int, m: int, data: dict, agg: dict,
                excl: set | frozenset = frozenset()) -> str:
    cur_agg, prev_agg = agg["cur_agg"], agg["prev_agg"]
    roster, grade_of, hold_last = data["roster"], data["grade_of"], data["hold_last"]
    e = html.escape

    # 대상 = 수급중+보류
    active = {nm: st for nm, st in roster.items() if ("수급중" in st or "보류" in st or st in ("1",))}

    # 근소차 우선 정렬(잠재매출 큰 순) — 계약제외자는 near_days=0이라 자동 제외
    near_rows = sorted(
        [(nm, a) for nm, a in cur_agg.items() if a["near_days"] > 0],
        key=lambda kv: (-kv[1]["gain"], -kv[1]["near_days"]))
    # 기회 지표(8h미만·근소차·잠재)는 계약제외자를 뺀 값
    total_near = sum(a["near_days"] for _, a in cur_agg.items())
    total_u8 = sum(a["u8_days"] for nm, a in cur_agg.items() if nm not in excl)
    total_pay = sum(a["pay_days"] for _, a in cur_agg.items())
    # ★총급여일 = 8h이상 + 8h미만 이 정확히 맞아떨어지게 전원 기준으로 따로 센다.
    #   total_u8 은 계약제외자를 뺀 값이라 (총급여일 − total_u8) 은 8h이상이 아니다 —
    #   이걸 헷갈려 숫자가 안 맞았다(사용자 지적 2026-07-22 "정확한 데이터 산정 필요").
    total_o8 = sum(a.get("o8_days", 0) for _, a in cur_agg.items())
    total_u8_all = sum(a["u8_days"] for _, a in cur_agg.items())
    total_gain = sum(a["gain"] for _, a in cur_agg.items())
    excl_days = sum(a["u8_days"] for nm, a in cur_agg.items() if nm in excl and a["u8_days"])
    excl_ppl = sum(1 for nm, a in cur_agg.items() if nm in excl and a["u8_days"])

    def delta(nm, key):
        c = cur_agg.get(nm, {}).get(key, 0)
        p = prev_agg.get(nm, {}).get(key, 0)
        d = c - p
        if d > 0:
            return f'<span class="up">▲{d}</span>'
        if d < 0:
            return f'<span class="down">▼{-d}</span>'
        return '<span class="flat">–</span>'

    rows_near = ""
    for nm, a in near_rows:
        dates = ", ".join(f"{d}일({rt})" for d, rt, _ in sorted(a["near_dates"]))
        gain = f'{a["gain"]:,}원<sup>추정</sup>' if a["gain"] else "—"
        rows_near += (
            f"<tr><td>{e(nm)}</td><td class='ce'>{e(grade_of.get(nm,'?'))}</td>"
            f"<td class='num'>{a['near_days']}</td><td>{e(dates)}</td>"
            f"<td class='num'>{gain}</td><td class='num'>{delta(nm,'near_days')}</td>"
            f"<td class='ce'>{'○' if a['transport'] else ''}</td></tr>")

    # 8h미만 전체(근소차 아니어도). 계약제외자는 맨 아래로, 회색 배지 표시.
    u8_rows = sorted([(nm, a) for nm, a in cur_agg.items() if a["u8_days"] > 0],
                     key=lambda kv: (kv[0] in excl, -kv[1]["u8_days"]))
    rows_u8 = ""
    for nm, a in u8_rows:
        is_excl = nm in excl
        badge = " <span style='background:#e9ecf3;color:#667;font-size:10px;padding:1px 5px;border-radius:6px'>계약 8h미만·제외</span>" if is_excl else ""
        style = " style='color:#99a'" if is_excl else ""
        # 8h미만과 6~8h 를 한 칸으로 합침 — 6~8h 는 8h미만의 부분집합이라 칸을 나누면 헷갈린다
        # (사용자 요청 2026-07-22 "보기가 너무 복잡"). 총 일수를 크게, 그중 6~8h 를 작게 덧붙인다.
        u8_cell = str(a["u8_days"])
        if a["t68"]:
            u8_cell += (f" <span style='color:#8a93a5;font-size:11px'>"
                        f"(6~8h {a['t68']})</span>")
        rows_u8 += (
            f"<tr{style}><td>{e(nm)}{badge}</td><td class='ce'>{e(grade_of.get(nm,'?'))}</td>"
            f"<td class='num'>{a['pay_days']}</td>"
            f"<td class='num'>{a.get('o8_days', a['pay_days'] - a['u8_days'])}</td>"
            f"<td class='num'>{u8_cell}</td>"
            f"<td class='num'>{a['near_days'] if not is_excl else '—'}</td>"
            f"<td class='num'>{delta(nm,'u8_days')}</td>"
            f"<td class='ce'>{'○' if a['transport'] else ''}</td></tr>")

    # 보류자 마지막 일정 (등급=hold_grade, 보류일·주요질환=hold_info). 복귀예정 등 수동 제외.
    hold_grade = data.get("hold_grade", {})
    hold_info = data.get("hold_info", {})
    hold_ex = manual_hold_exclude(branch_key(branch))
    hold_names_show = [nm for nm in hold_last if nm not in hold_ex]
    hold_rows = ""
    for nm in sorted(hold_names_show, key=lambda n: (hold_info.get(n, {}).get("since") or hold_last[n] or "0")):
        info = hold_info.get(nm, {})
        since = info.get("since") or "—"
        reason = info.get("reason") or ""
        # 이름·등급·보류일은 짧으니 내용폭으로 붙이고(ce), 남는 폭은 '보류 사유'가 먹게 둔다
        hold_rows += (f"<tr><td class='ce'>{e(nm)}</td>"
                      f"<td class='ce'>{e(hold_grade.get(nm) or grade_of.get(nm,'?'))}</td>"
                      f"<td class='ce'>{e(since)}</td><td>{e(reason)}</td></tr>")

    n_active = len(active)
    n_hold = len(hold_names_show)
    _t = date.today()
    partial_note = (" · <b>진행중 당월: 등록일정 전체 기준(오늘·예정 포함)</b>"
                    if (y, m) == (_t.year, _t.month) else "")
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>매출점검 {e(branch)} {y}-{m:02d}</title>
<style>
 /* 전체 틀을 가운데로. 여백은 padding 으로 — 탭바가 margin:-24px 로 뚫고 나가는 구조라
    margin 을 쓰면 탭바가 어긋난다. 표·탭바가 이 틀을 공유해 좌우 끝이 맞는다. */
 body{{font-family:'맑은 고딕',sans-serif;max-width:1500px;margin:0 auto;padding:24px;
   color:#1a2233;background:#f6f8fb}}
 h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:16px;margin:28px 0 8px;border-left:4px solid #2f6fdb;padding-left:8px}}
 .sub{{color:#667;font-size:13px;margin-bottom:16px}}
 .cards{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}}
 .card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:14px 18px;min-width:150px;box-shadow:0 1px 3px rgba(0,0,0,.04)}}
 .card .k{{font-size:12px;color:#778}} .card .v{{font-size:22px;font-weight:700;margin-top:4px}}
 .card.hi .v{{color:#d9480f}}
 /* ★큰 표는 width:100% 유지 — 내용폭(auto)으로 좁히면 탭바는 끝까지 가는데 표만 좁아져
    좌우가 어긋나 보인다(사용자 지적 2026-07-22 '탭과 네모박스 차이가 크다'). */
 /* ★width:100% 로 늘리면 남는 폭을 마지막 자유 칸(수급자 이름)이 혼자 먹어 터무니없이 넓어진다
    (사용자 지적 2026-07-22 '수급자 칸이 100은 되는 것 같은데'). → 내용폭에 맞춘다. */
 /* ★모든 표를 틀 폭에 100% 로 맞춘다 — auto 면 표마다 폭이 달라 들쭉날쭉(전체 일치 요청).
    대신 틀(body)을 1080px 로 좁혀 남는 폭이 이름 칸으로 크게 몰리지 않게 했다. */
 table{{border-collapse:collapse;width:auto;max-width:100%;background:#fff;font-size:13px;
   box-shadow:0 1px 3px rgba(0,0,0,.04)}}
 /* ★기본을 center 로 — left 로 두면 클래스 없는 칸(수급자명·해당 날짜·보류 사유)이
    왼쪽에 남아 표마다 정렬이 어긋난다(사용자 지적 2026-07-22). */
 th,td{{border:1px solid #e6ebf2;padding:7px 9px;text-align:center}}
 /* ★text-align:center 를 반드시 여기 둘 것 — 위 th,td 규칙이 left 라 안 주면 머리글이 왼쪽에 붙는다 */
 th{{background:#eef3fb;font-weight:600;white-space:nowrap;text-align:center}}
 /* ★width:1% = '내용 최소폭으로 붙여라'. 안 주면 width:100% 가 좁은 칸(송영·근소차 등)까지
    똑같이 벌려 쓸데없이 넓어진다(사용자 지적 2026-07-22). 남는 폭은 이름·사유 칸이 먹는다. */
 td.num{{text-align:center;font-variant-numeric:tabular-nums;white-space:nowrap}}
 td.ce{{text-align:center;white-space:nowrap}}
 /* 이름 칸이 남는 폭을 다 먹지 않게 상한을 준다 */
 td:first-child{{max-width:180px}}

 tr:nth-child(even) td{{background:#fbfcfe}}
 sup{{color:#d9480f;font-size:9px}}
 .up{{color:#d9480f;font-weight:700}} .down{{color:#2b8a3e}} .flat{{color:#aab}}
 .note{{color:#778;font-size:12px;margin-top:8px;line-height:1.6}}
</style></head><body>
<h1>💰 이번달 매출 극대화 점검 — {e(branch)}</h1>
<div class="sub">대상월 {y}-{m:02d} · 전월 대비 · 대상: 수급중·보류 {n_active}명 · 급여일 기준(비급여·한도초과·미이용 제외){partial_note}{f" · <b>계약상 8h미만 {excl_ppl}명 {excl_days}일 제외</b>" if excl_ppl else ""}</div>

<div class="cards">
 <div class="card"><div class="k">총 급여일</div><div class="v">{total_pay:,}</div>
  <div style="font-size:11px;color:#8a93a5;margin-top:4px">8h이상 {total_o8:,} + 8h미만 {total_u8_all:,}</div></div>
 <div class="card"><div class="k">8시간 이상 급여일</div><div class="v">{total_o8:,}</div>
  <div style="font-size:11px;color:#8a93a5;margin-top:4px">전체의 {round(total_o8/total_pay*100) if total_pay else 0}%</div></div>
 <div class="card"><div class="k">8시간 미만 급여일</div><div class="v">{total_u8_all:,}</div>
  <div style="font-size:11px;color:#8a93a5;margin-top:4px">{f'그중 계약제외 {excl_days:,}일' if excl_days else '연장 검토 대상'}</div></div>
 <div class="card hi"><div class="k">근소차 7:40~7:59 (연장후보)</div><div class="v">{total_near:,}건</div></div>
 <div class="card hi"><div class="k">연장 시 잠재 월매출<sup>추정</sup></div><div class="v">{total_gain:,}원</div></div>
</div>

<h2>① 근소차 우선 — 8시간에 20분 이내(7:40~7:59)</h2>
<table><thead><tr><th>수급자</th><th>등급</th><th>근소차 일수</th><th>해당 날짜(실제시간)</th>
 <th>연장 시 차액<sup>추정</sup></th><th>전월비</th><th>송영</th></tr></thead>
 <tbody>{rows_near or '<tr><td colspan=7 style="text-align:center;color:#999">해당 없음</td></tr>'}</tbody></table>

<h2>② 8시간 미만 급여일 — 전체</h2>
<table><thead><tr><th>수급자</th><th>등급</th><th>총급여일</th><th>8h이상</th>
 <th>8h미만 <span style="font-weight:400;color:#8a93a5;font-size:11px">(그중 6~8h)</span></th>
 <th>근소차</th><th>8h미만 전월비</th><th>송영</th></tr></thead>
 <tbody>{rows_u8 or '<tr><td colspan=8 style="text-align:center;color:#999">해당 없음</td></tr>'}</tbody></table>

<h2>③ 보류자 현황 ({n_hold}명) — 보류일 오래된 순</h2>
<table><thead><tr><th>수급자</th><th>등급</th><th>보류일</th><th>보류 사유</th></tr></thead>
 <tbody>{hold_rows or '<tr><td colspan=4 style="text-align:center;color:#999">보류자 없음</td></tr>'}</tbody></table>

<div class="note">
 · <b>급여일</b> = 급여수가&gt;0 (비급여·한도초과·미이용·<b>등급외</b> 제외). 실제 급여시간은 2-8 '월간 이동서비스 현황'의 청구 시간.<br>
 · <b>매출/급여수가</b> = 1일 총 급여비용(공단부담금 + 본인부담금 합, 제공기준). 본인부담금(≈10%)만이 아니며 공단청구액만도 아님.<br>
 · <b>계약상 8h미만 제외</b> = 애초에 8시간 미만으로 계약한 이용자는 연장 대상이 아니라 근소차·잠재매출에서 뺌(급여일·매출엔 포함). 명단: 평가준비 아닌 <code>매출점검/&lt;지점&gt;/_8시간미만계약제외.txt</code>.<br>
 · <b>근소차</b> = 실제 급여시간 7:40~7:59 (8시간 상위구간까지 20분 이내). 20분만 늘리면 8~10h 수가로 매출↑.<br>
 · <b>연장 시 차액[추정]</b> = 해당 수급자(없으면 같은 등급)의 관측된 8~10h 급여수가 중앙값 − 그날 수가. 실제 수가는 가감산·거리에 따라 다를 수 있어 <b>추정치</b>.<br>
 · <b>전월비</b> ▲=증가 ▼=감소. · <b>송영 ○</b> = 이동서비스(차량) 이용자 → 등·하원 시간 조정으로 연장 실현 가능.<br>
 · 개인정보 포함 — 로컬 전용, 외부 공유 금지.
</div>
<script>
 /* 표 폭 자동 통일 — 기준은 ② 8시간 미만 표. 합본의 fitTables 와 같은 규칙(지점 단독 페이지용). */
 (function(){{
   function fit(){{
     /* ★합본에 이 body 가 통째로 복사돼 들어간다(_extract_report). 그때 이 스크립트도 같이
        따라와서 document 전체 표(전체요약·이력·비급여·보류자)를 건드려 폭을 망가뜨렸다.
        합본에는 자체 fitTables 가 있으므로, 탭바가 보이면 여기선 아무것도 하지 않는다. */
     if (document.querySelector('.tabbar')) return;
     var ts = document.querySelectorAll('table');
     if(ts.length < 2) return;
     for(var i=0;i<ts.length;i++) ts[i].style.width='';
     var w = Math.round((ts[1]||ts[0]).getBoundingClientRect().width);
     if(!w) return;
     for(var j=0;j<ts.length;j++) ts[j].style.width = w+'px';
   }}
   window.addEventListener('load', fit);
   window.addEventListener('resize', fit);
 }})();
</script>
</body></html>"""


# ── 합본 렌더 (지점별 탭 전환) ─────────────────────────────────────────────
def _extract_report(text: str) -> dict:
    style = re.search(r"<style>(.*?)</style>", text, re.S)
    body = re.search(r"<body>(.*?)</body>", text, re.S)
    cards = re.findall(r'class="v">([^<]+)<', text)
    tgt = re.search(r"수급중·보류 (\d+)명", text)
    return {"style": style.group(1) if style else "",
            "body": body.group(1) if body else "",
            "cards": cards, "target": tgt.group(1) if tgt else "?"}


def combine_month(y: int, m: int, branches, progress=print):
    """이미 생성된 지점별 HTML을 하나로 합쳐 지점 탭으로 전환해 보는 합본 생성."""
    ym = f"{y}{m:02d}"
    parts = []
    for b in branches:
        key = branch_key(b.name)
        f = out_dir(key) / f"매출점검_{key}_{ym}.html"
        if f.exists():
            parts.append((key, b.name, _extract_report(f.read_text(encoding="utf-8"))))
        else:
            progress(f"  (합본) {key} HTML 없음 — 건너뜀")
    if not parts:
        progress("  합본할 지점 HTML이 없습니다.")
        return None

    shared_style = parts[0][2]["style"]

    # 지점 합계 파일(전월비용) 로드
    hist_dir = app_data_dir() / "revenue_history"

    def _load_tot(key):
        f = hist_dir / f"{key}_{ym}_totals.json"
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def _diff(prev, cur, good_up=True, unit=""):
        """'전월 → 이번달 (±차이)' 내부 span. good_up=True면 증가=개선(초록)."""
        d = cur - prev
        good = (d > 0) if good_up else (d < 0)
        cls = "down" if good else "up"  # down=초록, up=빨강
        diff = ("<span class='flat'>(±0)</span>" if d == 0
                else f"<span class='{cls}'>({'+' if d > 0 else '−'}{abs(d):,}{unit})</span>")
        return f"<span style='color:#99a'>{prev:,}{unit}</span> → <b>{cur:,}{unit}</b> {diff}"

    def _money(prev, cur, cnt_p=None, cnt_c=None, good_up=True):
        """매출 금액 셀(전월→이번달) + 아래 작은 글씨로 건수(전월→이번달)."""
        sub = (f"<div style='font-size:11px;color:#8894a6'>{cnt_p:,}건 → {cnt_c:,}건</div>"
               if cnt_p is not None else "")
        return (f"<td class='num' style='white-space:nowrap'>{_diff(prev, cur, good_up, '원')}{sub}</td>")

    ov = ""
    # rev_billed/rev_over8_billed = 한도초과 차감(청구기준). 구버전 파일이면 rev_total로 폴백.
    K = ("pay", "over8", "u8", "near", "rev_total", "rev_over8", "rev_under8", "gain",
         "rev_billed", "rev_over8_billed", "rev_under8_billed", "excess")
    tot = {k: 0 for k in K}
    ptot = {k: 0 for k in K}
    have_prev = False
    prev_ym = None
    ex_names, ex_u8, ex_rev, tot_excess = set(), 0, 0, 0
    np_total = np_meal = np_snack = 0   # 전체요약 비급여 합계(7-1 청구월 기준)
    for key, name, p in parts:
        t = _load_tot(key)
        link = f"<a href=\"#\" onclick=\"show('{key}');return false\">{name}</a>"
        if t and "rev_total" in t.get("cur", {}):
            have_prev = True
            prev_ym = t.get("prev_ym")
            cur, prv = t["cur"], t["prev"]
            for tt in (cur, prv):
                tt.setdefault("rev_billed", tt["rev_total"])
                tt.setdefault("rev_over8_billed", tt["rev_over8"])
                tt.setdefault("rev_under8_billed", tt["rev_under8"])
            target = t.get("target", p["target"])
            for k in K:
                tot[k] += cur.get(k, 0)
                ptot[k] += prv.get(k, 0)
            ex_names |= set(t.get("excl", []))
            ex_u8 += cur.get("u8_excl", 0)
            ex_rev += cur.get("rev_under8_excl", 0)
            tot_excess += cur.get("excess", 0)
            _np = t.get("nonpay") or {}
            np_total += _np.get("비급여계", 0)
            np_meal += _np.get("식사재료비", 0)
            np_snack += _np.get("간식비", 0)
            ov += (f"<tr class='ovrow' data-b='{key}'>"
                   f"<td style='white-space:nowrap;text-align:center'>{link}</td><td class='num'>{target}</td>"
                   f"<td class='num' style='white-space:nowrap'>{_diff(prv['rev_billed'], cur['rev_billed'], True, '원')}"
                   f"<div style='font-size:11px;color:#8894a6'>{prv['pay']:,}건 → {cur['pay']:,}건</div></td>"
                   f"{_money(prv['rev_over8_billed'], cur['rev_over8_billed'], prv['over8'], cur['over8'], True)}"
                   f"{_money(prv['rev_under8_billed'], cur['rev_under8_billed'], prv['u8'], cur['u8'], True)}"
                   f"{_money(prv['gain'], cur['gain'], prv['near'], cur['near'], False)}</tr>")
        else:
            ov += (f"<tr class='ovrow' data-b='{key}'><td>{link}</td><td class='num'>{p['target']}</td>"
                   f"<td colspan=4 style='color:#c00'>매출 금액은 재실행 후 표시됩니다</td></tr>")

    if have_prev:
        ov += ("<tr class='ovsum' style='font-weight:700;background:#eef3fb'><td>합계</td><td class='num'>–</td>"
               f"<td class='num' style='white-space:nowrap'>{_diff(ptot['rev_billed'], tot['rev_billed'], True, '원')}"
               f"<div style='font-size:11px;color:#667'>{ptot['pay']:,}건 → {tot['pay']:,}건</div></td>"
               f"{_money(ptot['rev_over8_billed'], tot['rev_over8_billed'], ptot['over8'], tot['over8'], True)}"
               f"{_money(ptot['rev_under8_billed'], tot['rev_under8_billed'], ptot['u8'], tot['u8'], True)}"
               f"{_money(ptot['gain'], tot['gain'], ptot['near'], tot['near'], False)}</tr>")

    cmp_note = (f" · 각 칸 상단=<b>매출 금액</b>, 하단=건수, 모두 <b>전월({prev_ym}) → 이번달 (±차이)</b>. "
                f"매출 <span class='down'>초록</span>=증가. 잠재매출 <span class='up'>빨강</span>=미포착↑(잡을 여지 큼)."
                if have_prev else " · (매출 금액: 재실행 후 표시)")

    # 보류자 마지막 일정 — 지점별로 구분(전체 요약용). 저장된 원자료/스냅샷에서 로드.
    def _gaptxt(last):
        if not last:
            return "13개월+ 일정 없음", "<b class='up'>13개월+ 없음</b>"
        ly, lm, _d = map(int, last.split("-"))
        gap = (y - ly) * 12 + (m - lm)
        g = (f"<b class='up'>{gap}개월 전</b>" if gap >= 3 else
             (f"{gap}개월 전" if gap > 0 else "이번달"))
        return last, g

    hold_sections, hold_total = "", 0
    for key, name, _ in parts:
        dj = hist_dir / f"{key}_{ym}_data.json"
        sj = hist_dir / f"{key}_{ym}.json"
        hold_last, hold_grade, hold_info = {}, {}, {}
        src = dj if dj.exists() else (sj if sj.exists() else None)
        if src:
            j = json.loads(src.read_text(encoding="utf-8"))
            hold_last = j.get("hold_last", {})
            hold_grade = j.get("hold_grade", {})
            hold_info = j.get("hold_info", {})
        hold_ex = manual_hold_exclude(key)   # 복귀예정 등 제외
        # 보류일(since) 오래된 순
        items = sorted(((nm, lv) for nm, lv in hold_last.items() if nm not in hold_ex),
                       key=lambda kv: hold_info.get(kv[0], {}).get("since") or kv[1] or "0000-00-00")
        if not items:
            continue
        hold_total += len(items)
        rows = ""
        for nm, last in items:
            info = hold_info.get(nm, {})
            since = info.get("since") or "—"
            reason = info.get("reason") or ""
            gaptag = ""
            if since and since != "—":
                sy, sm2, _sd = map(int, since.split("-"))
                gm = (y - sy) * 12 + (m - sm2)
                if gm >= 3:
                    gaptag = f" <span class='up' style='font-size:11px'>({gm}개월+)</span>"
            rows += (f"<tr><td class='nm'>{nm}</td><td class='ce'>{hold_grade.get(nm, '?')}</td>"
                     f"<td class='ce'>{since}{gaptag}</td><td>{reason}</td></tr>")
        hold_sections += (
            f"<div class='bh'>{name} <span class='cnt'>{len(items)}명</span></div>"
            f"<table class='hold'><thead><tr><th>수급자</th><th>등급</th><th>보류일</th>"
            f"<th>보류 사유</th></tr></thead><tbody>{rows}</tbody></table>")
    hold_table = ((f"<h2>🅱 보류자 현황 — 지점별 (전체 {hold_total}명)</h2>"
                   f"<div class='hint'>보류일 오래된 순 · 3개월 이상 <span class='up'>빨강</span> = 퇴소 검토/재개 상담 대상.</div>"
                   f"{hold_sections}") if hold_total else "")

    # ── 비급여 탭 (7-1 청구월 합계) ────────────────────────────────────────
    # 공단급여·본인부담금을 뺀 나머지 = 식사재료비·간식비·이미용비·진료약제비·기타·등급외한도초과.
    # ⚠️ 7-1은 **청구월(=전월)** 기준이라 위 매출표의 '이번달'과 대상월이 다르다 — 반드시 밝힌다.
    _NP = [l for _, l in NONPAY_COLS]
    np_rows, np_tot = "", {l: 0 for l in _NP}
    np_sum, np_any = 0, False
    for key, name, p in parts:
        # parts 는 HTML 파싱 결과라 totals 가 없다 → 합계 파일에서 직접 읽는다
        d = ((_load_tot(key) or {}).get("nonpay")) or {}
        if not d:
            np_rows += (f"<tr><td style='white-space:nowrap'>{name}</td>"
                        f"<td colspan={len(_NP) + 1} style='color:#c00'>재실행 후 표시됩니다</td></tr>")
            continue
        np_any = True
        s = d.get("비급여계", 0)
        np_sum += s
        for l in _NP:
            np_tot[l] += d.get(l, 0)
        np_rows += (f"<tr><td style='white-space:nowrap'>{name}</td>"
                    + "".join(f"<td class='num'>{d.get(l, 0):,}</td>" for l in _NP)
                    + f"<td class='num'><b>{s:,}</b></td></tr>")
    if np_any:
        np_rows += ("<tr style='font-weight:700;background:#eef3fb'><td>합계</td>"
                    + "".join(f"<td class='num'>{np_tot[l]:,}</td>" for l in _NP)
                    + f"<td class='num'>{np_sum:,}</td></tr>")
    # ── 월별 실적 (매출 + 비급여 항목) — 연·월 드롭다운으로 그 달만 ────────────
    # 매출과 비급여를 따로 두면 선택월이 어긋난다 → 한 표에 합치고 선택도 하나로.
    # 지점을 세로(행), 항목을 가로(열)로 둬야 비급여 항목별 금액이 다 보인다.
    hist_table = ""
    try:
        _hf = hist_dir / "revenue_monthly.json"
        _hist = json.loads(_hf.read_text(encoding="utf-8")) if _hf.exists() else {}
    except Exception:
        _hist = {}
    if _hist:
        _keys = [(k, n) for k, n, _ in parts if k in _hist]
        _yms = sorted({v for k, _ in _keys for v in _hist.get(k, {})}, reverse=True)
        if _keys and _yms:
            _blocks = ""
            for _ym in _yms:
                _rows, _sum = "", {"rev": 0, "계": 0, **{l: 0 for l in _NP}}
                for k, nm in _keys:
                    d = _hist.get(k, {}).get(_ym)
                    if not d:
                        _rows += (f"<tr class='brow' data-b='{k}'><td style='white-space:nowrap'>{nm}</td>"
                                  f"<td colspan={len(_NP) + 1} style='color:#c8ced8'>개소 전</td></tr>")
                        continue
                    np = d.get("nonpay") or {}
                    _sum["rev"] += d["rev_total"]
                    _sum["계"] += np.get("비급여계", 0)
                    for l in _NP:
                        _sum[l] += np.get(l, 0)
                    _rows += (f"<tr class='brow' data-b='{k}'><td style='white-space:nowrap'>{nm}</td>"
                              + "".join(f"<td class='num'>{np.get(l, 0):,}</td>" for l in _NP)
                              + f"<td class='num'><b>{np.get('비급여계', 0):,}</b></td></tr>")
                _rows += ("<tr class='sumrow' style='font-weight:700;background:#eef3fb'><td>합계</td>"
                          + "".join(f"<td class='num'>{_sum[l]:,}</td>" for l in _NP)
                          + f"<td class='num'>{_sum['계']:,}</td></tr>")
                _blocks += (
                    f"<div class='hblk' data-ym='{_ym}'>"
                    f"<div style='overflow-x:auto'><table class='hist'><thead><tr>"
                    f"<th>지점</th>"
                    + "".join(f"<th>{l}</th>" for l in _NP)
                    + "<th>비급여 계</th></tr></thead>"
                    f"<tbody>{_rows}</tbody></table></div></div>")

            _years = sorted({v[:4] for v in _yms}, reverse=True)
            _yopt = "".join(f"<option value='{yy}'>{yy}년</option>" for yy in _years)
            hist_table = (
                f"<h2 style='margin-top:26px'>🧾 월별 비급여</h2>"
                f"<div class='sub'>연도·월을 고르면 그 달 실적이 나옵니다. "
                f"식사재료비·간식비 등 공단급여·본인부담금을 <b>제외한</b> 비용(7-1 청구 기준).<br>"
                f"전월 대비 증감은 넣지 않았습니다 — 실적만 봅니다.</div>"
                f"<div class='histpick'>"
                f"<select id='histY' onchange='histYear(this.value)'>{_yopt}</select>"
                f"<select id='histM' onchange='histShow(this.value)'></select>"
                f"<select id='histB' onchange='histBranch(this.value)'>"
                f"<option value='_all'>전체 지점</option>"
                + "".join(f"<option value='{k}'>{n}</option>" for k, n in _keys)
                + f"</select></div>{_blocks}")

    np_table = (f"<h2 style='margin-top:26px'>🧾 비급여 항목 — {prev_ym}</h2>"
                f"<div class='sub'>공단급여·본인부담금을 <b>제외한</b> 비용. "
                f"출처 7-1 본인부담금 청구관리 합계 행.<br>"
                f"⚠️ 7-1은 <b>청구월 기준</b>이라 위 매출표의 '이번달'이 아니라 "
                f"<b>{prev_ym}</b> 실적입니다(당월은 아직 청구 전).</div>"
                f"<div style='overflow-x:auto'><table class='nonpay'><thead><tr><th>지점</th>"
                + "".join(f"<th>{l}</th>" for l in _NP)
                + "<th>비급여 계</th></tr></thead>"
                f"<tbody>{np_rows}</tbody></table></div>"
                f"<div class='note'>· <b>비급여 계</b> = 식사재료비+간식비+이미용비+진료약제비+기타비용+등급외/한도초과. "
                f"7-1의 <b>부담금합계 − 급여비용 본인부담금</b> 과 일치한다(수집 시 검산).<br>"
                f"· 공단급여·본인부담금은 위 매출표에 이미 포함돼 있어 여기서 뺐다.</div>")

    btns = "<button class='tabbtn active' data-k='_ov' onclick=\"show('_ov')\">전체 요약</button>" + \
        "".join(f"<button class='tabbtn' data-k='{k}' onclick=\"show('{k}')\">{n}</button>"
                for k, n, _ in parts)
    _tc = date.today()
    partial_c = (" <b>진행중 당월: 등록일정 전체 기준</b>."
                 if (y, m) == (_tc.year, _tc.month) else "")
    # ── 위쪽 매출표: 과거 달도 볼 수 있게 월별 표를 미리 만들어 둔다 ─────────────
    # 이번달 표는 위 ov(실시간 수집분) 그대로 쓰고, 과거 달은 이력(revenue_monthly.json)에서
    # '그 달 vs 그 전달' 로 만든다. 비교 방식은 위쪽 표의 성격(전월 대비)을 그대로 따른다.
    ovm_blocks, ovm_yms = "", []
    if _hist:
        _hk = [(k, n) for k, n, _ in parts if k in _hist]
        _all = sorted({v for k, _ in _hk for v in _hist.get(k, {})}, reverse=True)
        for _v in _all:
            _py = int(_v[:4]); _pm = int(_v[4:])
            _pv = f"{_py - 1}12" if _pm == 1 else f"{_py}{_pm - 1:02d}"
            _r, _c, _p = "", {"rev": 0, "o8": 0, "u8": 0, "d": 0}, {"rev": 0, "o8": 0, "u8": 0, "d": 0}
            for k, nm in _hk:
                cu = _hist.get(k, {}).get(_v)
                pr = _hist.get(k, {}).get(_pv) or {}
                if not cu:
                    continue
                for src, acc in ((cu, _c), (pr, _p)):
                    acc["rev"] += src.get("rev_total", 0)
                    acc["o8"] += src.get("rev_over8", 0)
                    acc["u8"] += src.get("rev_under8", 0)
                    acc["d"] += src.get("pay_days", 0)
                _r += (f"<tr class='ovrow' data-b='{k}'><td style='white-space:nowrap'>{nm}</td>"
                       f"<td class='num'>{cu.get('people', 0)}</td>"
                       f"<td class='num' style='white-space:nowrap'>"
                       f"{_diff(pr.get('rev_total', 0), cu.get('rev_total', 0), True, '원')}"
                       f"<div style='font-size:11px;color:#8894a6'>"
                       f"{pr.get('pay_days', 0):,}건 → {cu.get('pay_days', 0):,}건</div></td>"
                       + _money(pr.get('rev_over8', 0), cu.get('rev_over8', 0),
                                pr.get('over8', 0), cu.get('over8', 0), True)
                       + _money(pr.get('rev_under8', 0), cu.get('rev_under8', 0),
                                pr.get('u8', 0), cu.get('u8', 0), True)
                       + "</tr>")
            _r += ("<tr class='ovsum' style='font-weight:700;background:#eef3fb'><td>합계</td><td class='num'>–</td>"
                   f"<td class='num' style='white-space:nowrap'>{_diff(_p['rev'], _c['rev'], True, '원')}"
                   f"<div style='font-size:11px;color:#667'>{_p['d']:,}건 → {_c['d']:,}건</div></td>"
                   + _money(_p["o8"], _c["o8"], None, None, True)
                   + _money(_p["u8"], _c["u8"], None, None, True) + "</tr>")
            ovm_yms.append(_v)
            ovm_blocks += (
                f"<div class='ovblk' data-ym='{_v}'>"
                f"<div style='overflow-x:auto'><table><thead><tr><th>지점</th><th>이용인원</th>"
                f"<th>총매출<br><small>전월→해당월</small></th>"
                f"<th>8시간 이상 매출<br><small>금액 · 건수</small></th>"
                f"<th>8시간 미만 매출<br><small>금액 · 건수</small></th></tr></thead>"
                f"<tbody>{_r}</tbody></table></div></div>")
    _ovyears = sorted({v[:4] for v in ovm_yms}, reverse=True)
    ovm_pick = ""
    if ovm_yms:
        ovm_pick = (f"<div class='histpick'>"
                    f"<select id='ovY' onchange='ovYear(this.value)'>"
                    f"<option value='_now'>이번달({y}-{m:02d})</option>"
                    + "".join(f"<option value='{yy}'>{yy}년</option>" for yy in _ovyears)
                    + f"</select><select id='ovM' onchange='ovShow(this.value)' "
                      f"style='display:none'></select></div>")

    # ── 위쪽 매출표를 과거 달도 볼 수 있게 ────────────────────────────────────
    # 이번달은 위 ov(실시간 수집분)를 그대로 쓰고, 과거 달은 이력에서 '그 달 vs 그 전달'로 만든다.
    # ⚠️ 잠재매출(근소차 연장 차액)은 사람별 수가 계산이 필요해 이력엔 없다 → 이번달 표에만 있다.
    ovm, ovm_yms = "", []
    if _hist:
        _ok = [(k, n) for k, n, _ in parts if k in _hist]
        for _v in sorted({x for k, _ in _ok for x in _hist.get(k, {})}, reverse=True):
            _yy, _mm = int(_v[:4]), int(_v[4:])
            _pv = f"{_yy - 1}12" if _mm == 1 else f"{_yy}{_mm - 1:02d}"
            _r = ""
            _c = {"rev": 0, "o8": 0, "u8": 0, "d": 0}
            _pp = {"rev": 0, "o8": 0, "u8": 0, "d": 0}
            for k, nm in _ok:
                cu = _hist.get(k, {}).get(_v)
                if not cu:
                    continue
                pr = _hist.get(k, {}).get(_pv) or {}
                for src, acc in ((cu, _c), (pr, _pp)):
                    acc["rev"] += src.get("rev_total", 0)
                    acc["o8"] += src.get("rev_over8", 0)
                    acc["u8"] += src.get("rev_under8", 0)
                    acc["d"] += src.get("pay_days", 0)
                _r += (f"<tr class='ovrow' data-b='{k}'><td style='white-space:nowrap'>{nm}</td>"
                       f"<td class='num'>{cu.get('people', 0)}</td>"
                       f"<td class='num' style='white-space:nowrap'>"
                       f"{_diff(pr.get('rev_total', 0), cu.get('rev_total', 0), True, '원')}"
                       f"<div style='font-size:11px;color:#8894a6'>"
                       f"{pr.get('pay_days', 0):,}건 → {cu.get('pay_days', 0):,}건</div></td>"
                       + _money(pr.get("rev_over8", 0), cu.get("rev_over8", 0),
                                pr.get("over8", 0), cu.get("over8", 0), True)
                       + _money(pr.get("rev_under8", 0), cu.get("rev_under8", 0),
                                pr.get("u8", 0), cu.get("u8", 0), True) + "</tr>")
            if not _r:
                continue
            _r += ("<tr class='ovsum' style='font-weight:700;background:#eef3fb'><td>합계</td><td class='num'>–</td>"
                   f"<td class='num' style='white-space:nowrap'>{_diff(_pp['rev'], _c['rev'], True, '원')}"
                   f"<div style='font-size:11px;color:#667'>{_pp['d']:,}건 → {_c['d']:,}건</div></td>"
                   + _money(_pp["o8"], _c["o8"], None, None, True)
                   + _money(_pp["u8"], _c["u8"], None, None, True) + "</tr>")
            ovm_yms.append(_v)
            ovm += (f"<div class='ovblk' data-ym='{_v}' style='display:none'>"
                    f"<div style='overflow-x:auto'><table><thead><tr>"
                    f"<th>지점</th><th>이용인원</th>"
                    f"<th>총매출<br><small>전월→{_v[:4]}-{_v[4:]}</small></th>"
                    f"<th>8시간 이상 매출<br><small>금액 · 건수</small></th>"
                    f"<th>8시간 미만 매출<br><small>금액 · 건수</small></th></tr></thead>"
                    f"<tbody>{_r}</tbody></table></div></div>")
    ov_pick = ""
    if ovm_yms:
        ov_pick = (f"<div class='histpick'>"
                   f"<select id='ovY' onchange='ovYear(this.value)'>"
                   f"<option value='_now'>이번달 ({y}-{m:02d})</option>"
                   + "".join(f"<option value='{yy}'>{yy}년</option>"
                             for yy in sorted({v[:4] for v in ovm_yms}, reverse=True))
                   + f"</select>"
                     f"<select id='ovM' onchange='ovShow(this.value)' style='display:none'></select>"
                     f"<select id='ovB' onchange='ovBranch(this.value)'>"
                     f"<option value='_all'>전체 지점</option>"
                     + "".join(f"<option value='{k}'>{n}</option>" for k, n in _ok)
                     + f"</select></div>")

    ov_panel = (f"<div id='p__ov' class='branch active'><h1>💰 매출 극대화 점검 합본 — {y}-{m:02d}</h1>"
                f"<div class='sub'>지점 탭을 눌러 상세를 보세요.{partial_c} 급여일 기준(비급여·한도초과·미이용 제외).{cmp_note}</div>"
                f"{ov_pick}"
                f"<div class='ovblk' data-ym='_now'>"
                f"<div style='overflow-x:auto'><table><thead><tr><th>지점</th><th>대상<br>(수급중·보류)</th>"
                f"<th>총매출<br><small>전월→이번달</small></th><th>8시간 이상 매출<br><small>금액 · 건수</small></th>"
                f"<th>8시간 미만 매출<br><small>금액 · 건수</small></th>"
                f"<th>잠재매출<sup>추정</sup><br><small>금액 · 근소차건</small></th></tr></thead>"
                f"<tbody>{ov}</tbody></table></div></div>"
                f"{ovm}"
                f"{hist_table}"
                f"{hold_table}"
                f"<div class='note'>· <b>매출 = 공단 청구기준 급여비용</b>(7-1 급여비용 공단+본인). "
                f"<b>전월</b>은 청구월 실측이라 정확(한도초과·등급외 자동 제외), <b>당월</b>은 전월 비율로 추정(진행중·청구 전)."
                + (f" 전 지점 이번달 제공기준 대비 <b>{tot_excess:,}원</b>(한도초과+등급외) 차감됨.<br>" if tot_excess else "<br>")
                + f"· 8h이상/미만도 같은 비율로 청구기준 환산. 본인부담금 탭이 작은 건 본인부담(≈10%)만이라서."
                + (f"<br>· <b>계약상 8h미만 제외 {len(ex_names)}명</b>: 8시간 미만 매출 집계에서 "
                   f"이번달 {ex_u8:,}건·{ex_rev:,}원을 뺌(연장 대상 아님). 급여일·총매출엔 포함."
                   if ex_names else "")
                + "</div></div>")
    panels = ov_panel + "".join(f"<div id='p_{k}' class='branch'>{p['body']}</div>"
                                for k, n, p in parts)

    combined = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>매출점검 합본 {y}-{m:02d}</title>
<style>
{shared_style}
 .tabbar{{position:sticky;top:0;background:#f6f8fb;padding:10px 0 8px;margin:-24px -24px 8px;
   padding-left:24px;border-bottom:1px solid #dde3ec;z-index:5;display:flex;gap:6px;flex-wrap:wrap}}
 .tabbtn{{border:1px solid #cdd6e4;background:#fff;border-radius:8px;padding:7px 14px;font-size:14px;
   cursor:pointer;font-family:inherit;color:#334}}
 .tabbtn.active{{background:#2f6fdb;border-color:#2f6fdb;color:#fff;font-weight:700}}
 .branch{{display:none}} .branch.active{{display:block}}
 .hint{{color:#8a94a6;font-size:12px;margin:2px 0 10px}}
 .bh{{margin:16px 0 6px;font-weight:700;font-size:14px;color:#1a2233;
   border-left:3px solid #2f6fdb;padding-left:8px;box-sizing:border-box}}
 .bh .cnt{{color:#8a94a6;font-weight:400;font-size:12px;margin-left:4px}}
 /* 폭을 고정해야 지점별로 어긋나지 않는다(auto 면 사유 길이에 따라 제각각). */
 /* ★table-layout:fixed + 칸 폭 고정 — 안 하면 지점마다 '보류 사유' 길이에 따라
    앞 칸(수급자·등급·보류일) 폭이 제각각 늘어나 지점끼리 세로줄이 안 맞는다
    (사용자 지적 2026-07-22 '사이즈 정렬 필요'). */
 /* 표 폭도 내용에 맞게 줄인다 — 100% 로 두면 '보류 사유' 칸만 화면 끝까지 늘어난다
    (사용자 요청 2026-07-22 '보류사유는 칸을 줄여서'). 칸 폭은 고정해 지점끼리 세로줄을 맞춘다. */
 /* 비급여 표는 보류자 표와 **항상 같은 폭**이어야 한다(사용자 요청 2026-07-22).
    둘 다 같은 고정폭을 쓰면 내용이 바뀌어도 어긋나지 않는다. */
 table.hold,table.nonpay{{width:867px;max-width:100%;margin:0 0 6px 0;table-layout:fixed}}
 /* 월별 이력은 행이 20+개라 연월 칸을 고정해 세로줄을 맞춘다 */
 table.hist{{width:auto;max-width:100%}}
 .histpick{{display:flex;gap:8px;margin:10px 0}}
 .histpick select{{border:1px solid #cdd6e4;background:#fff;border-radius:8px;
   padding:6px 12px;font-size:14px;font-family:inherit;color:#1a2233;cursor:pointer}}
 .hblk{{display:none}}   /* histYear 가 최신 달 하나만 켠다 */
 table.hist th:first-child,table.hist td:first-child{{width:90px;white-space:nowrap}}
 table.nonpay th:first-child,table.nonpay td:first-child{{width:110px}}
 table.hold th:nth-child(1),table.hold td:nth-child(1){{width:110px}}
 table.hold th:nth-child(2),table.hold td:nth-child(2){{width:80px}}
 table.hold th:nth-child(3),table.hold td:nth-child(3){{width:170px}}
 table.hold th:nth-child(4),table.hold td:nth-child(4){{width:430px;white-space:normal}}
 /* 보류자 표는 전 칸 가운데 정렬(사유 포함) */
 table.hold td{{text-align:center}}
 table.hold td.nm{{font-weight:600;white-space:nowrap}} table.hold td.ce{{text-align:center;white-space:nowrap}}
 table.hold th{{white-space:nowrap}}
</style></head><body>
<div class="tabbar">{btns}</div>
{panels}
<script>
 /* 표 폭 자동 통일 — 기준은 ② 8시간 미만 표(사용자 지정 2026-07-22).
    ①·③ 은 행 수·내용에 따라 폭이 제각각이라(근소차가 줄면 ① 이 좁아짐) 나란히 보면 어긋난다.
    ⚠️ display:none 인 패널은 폭이 0으로 측정되므로 반드시 '보이게 한 뒤' 재계산할 것. */
 function fitTables(panel){{
   if(!panel) return;
   /* ★전체요약(p__ov)은 성격이 다른 표(매출·이력·비급여·보류자)가 섞여 있어
      한 폭으로 맞추면 비급여 표가 눌려 글자가 겹친다. 지점 탭에서만 맞춘다. */
   if(panel.id === 'p__ov') return;
   var ts = panel.querySelectorAll('table');
   if(ts.length < 2) return;
   for(var i=0;i<ts.length;i++) ts[i].style.width='';      // 이전 값 지우고 자연폭부터 다시
   var base = ts[1] || ts[0];                              // ts[1] = ② 8시간 미만 표
   var w = Math.round(base.getBoundingClientRect().width);
   if(!w) return;
   /* 지정폭이 내용보다 좁으면 브라우저가 최소폭을 쓰므로 눌려 깨지지 않는다 */
   for(var j=0;j<ts.length;j++) ts[j].style.width = w+'px';
 }}
 /* 월별 이력: 연도 고르면 그 해 월 버튼만, 월 고르면 그 달 행만 보인다.
    26개월을 한꺼번에 늘어놓으면 길어서 못 본다(사용자 요청 2026-07-22). */
 /* 위쪽 매출표: '이번달'(실시간 수집분) 또는 과거 달(이력) 하나만 보인다. */
 function ovShow(v){{
   document.querySelectorAll('.ovblk').forEach(function(b){{
     b.style.display = (b.dataset.ym === v) ? 'block' : 'none';
   }});
   var bs = document.getElementById('ovB');
   if (bs) ovBranch(bs.value);   /* 달을 바꿔도 고른 지점이 유지되게 */
 }}
 function ovBranch(k){{
   /* 한 지점만 고르면 그 행만 남기고 합계는 숨긴다(한 줄 합계는 같은 값이라 군더더기) */
   document.querySelectorAll('.ovrow').forEach(function(r){{
     r.style.display = (k === '_all' || r.dataset.b === k) ? '' : 'none';
   }});
   document.querySelectorAll('.ovsum').forEach(function(r){{
     r.style.display = (k === '_all') ? '' : 'none';
   }});
 }}
 function ovYear(y){{
   var ms = document.getElementById('ovM');
   if (y === '_now') {{ ms.style.display = 'none'; ovShow('_now'); return; }}
   var vs = [];
   document.querySelectorAll('.ovblk').forEach(function(b){{
     var v = b.dataset.ym;
     if (v.length === 6 && v.slice(0,4) === y) vs.push(v);
   }});
   vs.sort();
   ms.style.display = '';
   ms.innerHTML = vs.map(function(v){{
     return "<option value='" + v + "'>" + parseInt(v.slice(4),10) + "월</option>";
   }}).join('');
   if (vs.length) {{ ms.value = vs[vs.length-1]; ovShow(ms.value); }}
 }}
 function histShow(ym){{
   document.querySelectorAll('.hblk').forEach(function(b){{
     b.style.display = (b.dataset.ym === ym) ? 'block' : 'none';
   }});
   var bs = document.getElementById('histB');
   if (bs) histBranch(bs.value);   /* 달을 바꿔도 고른 지점이 유지되게 */
 }}
 function histBranch(k){{
   /* '전체 지점'이면 다 보이고 합계도 보인다. 한 지점만 고르면 그 행만 남기고 합계는 숨긴다
      (한 줄짜리 합계는 같은 값이라 군더더기). */
   document.querySelectorAll('.hblk .brow').forEach(function(r){{
     r.style.display = (k === '_all' || r.dataset.b === k) ? '' : 'none';
   }});
   document.querySelectorAll('.hblk .sumrow').forEach(function(r){{
     r.style.display = (k === '_all') ? '' : 'none';
   }});
 }}
 function histYear(y){{
   var ms = [];
   document.querySelectorAll('.hblk').forEach(function(b){{
     if (b.dataset.ym.slice(0,4) === y) ms.push(b.dataset.ym);
   }});
   ms.sort();
   var sel = document.getElementById('histM');
   sel.innerHTML = ms.map(function(v){{
     return "<option value='" + v + "'>" + parseInt(v.slice(4),10) + "월</option>";
   }}).join('');
   if (ms.length) {{ sel.value = ms[ms.length-1]; histShow(sel.value); }}   /* 그 해 최신 달 */
 }}
 function show(k){{
   document.querySelectorAll('.branch').forEach(e=>e.classList.remove('active'));
   var el=document.getElementById('p_'+k); if(el) el.classList.add('active');
   document.querySelectorAll('.tabbtn').forEach(b=>b.classList.toggle('active', b.dataset.k===k));
   fitTables(el);                                          // 보인 뒤에 계산해야 폭이 잡힌다
   window.scrollTo(0,0);
 }}
 window.addEventListener('load', function(){{
   fitTables(document.querySelector('.branch.active'));
   var ys=document.getElementById('histY'); if(ys) histYear(ys.value);   /* 최신 연도·달로 시작 */
   ovShow('_now');   /* 위쪽 표는 이번달로 시작 */
 }});
 window.addEventListener('resize', function(){{ fitTables(document.querySelector('.branch.active')); }});
</script>
</body></html>"""
    dest = OUT_ROOT / f"매출점검_합본_{ym}.html"
    dest.write_text(combined, encoding="utf-8")
    progress(f"  ✅ 합본 HTML: {dest}")
    # 관제탑 링크가 매달 깨지지 않도록 이름 고정 사본도 함께 갱신(항상 최신월을 가리킴)
    latest = OUT_ROOT / "매출점검_합본_최신.html"
    latest.write_text(combined, encoding="utf-8")
    progress(f"  ✅ 합본 HTML(최신 고정): {latest}")
    return dest


# ── 실행 ───────────────────────────────────────────────────────────────────
def branch_key(name: str) -> str:
    return name.split()[0].replace("점", "")


def finalize_branch(b, y, m, py, pm, data, hist_dir, progress=print):
    """수집(또는 저장)된 원자료 → 제외 적용·집계·HTML·스냅샷·합계 저장. (케어포 접속 없음)"""
    key = branch_key(b.name)
    excl = manual_exclude(key)
    agg = aggregate(data, excl)
    out_html = render_html(b.name, y, m, data, agg, excl)
    dest = out_dir(key) / f"매출점검_{key}_{y}{m:02d}.html"
    dest.write_text(out_html, encoding="utf-8")
    progress(f"  ✅ HTML: {dest}" + (f"  (계약 8h미만 {len(excl)}명 제외)" if excl else ""))

    # 스냅샷(개인정보 로컬)
    snap = {"branch": b.name, "ym": f"{y}-{m:02d}",
            "cur_agg": {nm: {k: (list(a[k]) if isinstance(a[k], set) else a[k])
                             for k in ("pay_days", "u8_days", "near_days", "t68", "gain")}
                        for nm, a in agg["cur_agg"].items()},
            "hold_last": data["hold_last"], "hold_grade": data.get("hold_grade", {}),
            "hold_info": data.get("hold_info", {})}
    (hist_dir / f"{key}_{y}{m:02d}.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")

    # 지점 합계(이번달+전월) — 계약 8h미만 제외 반영(기회 지표만 제외, 매출·급여일은 유지)
    def _tot(recs, ag):
        over = [r for r in recs if r["min"] >= FULL_8H]
        under_all = [r for r in recs if r["min"] < FULL_8H]
        under = [r for r in under_all if r["name"] not in excl]        # 기회 대상
        under_x = [r for r in under_all if r["name"] in excl]          # 계약제외
        return {"pay": len(recs), "over8": len(over),
                "u8": len(under), "u8_excl": len(under_x),
                "near": sum(1 for r in under if NEAR_MISS_LO <= r["min"] < FULL_8H),
                "rev_total": sum(r["amt"] for r in recs),
                "rev_over8": sum(r["amt"] for r in over),
                "rev_under8": sum(r["amt"] for r in under),
                "rev_under8_excl": sum(r["amt"] for r in under_x),
                "gain": sum(x["gain"] for nm, x in ag.items() if nm not in excl)}
    n_active = sum(1 for st in data["roster"].values()
                   if "수급중" in st or "보류" in st or st == "1")
    cur_t, prev_t = _tot(data["cur"], agg["cur_agg"]), _tot(data["prev"], agg["prev_agg"])
    # 청구기준 매출 = 7-1 급여비용(공단+본인). 전월=청구월 실측(정확), 당월=전월 비율로 추정.
    # (7-1 급여비용은 한도초과·등급외가 자동 제외된 순수 공단청구 급여비용)
    billed_prev = int(data.get("billed_prev", 0) or 0)
    ratio = (billed_prev / prev_t["rev_total"]) if (billed_prev and prev_t["rev_total"]) else 1.0
    # 8h미만 매출(표시)은 계약제외분(구○숙 등)도 포함해야 8h이상+8h미만=총매출 항등 유지
    prev_t["rev_billed"] = billed_prev or prev_t["rev_total"]
    prev_t["rev_over8_billed"] = round(prev_t["rev_over8"] * ratio)
    prev_t["rev_under8_billed"] = round((prev_t["rev_under8"] + prev_t["rev_under8_excl"]) * ratio)
    cur_t["rev_billed"] = round(cur_t["rev_total"] * ratio)
    cur_t["rev_over8_billed"] = round(cur_t["rev_over8"] * ratio)
    cur_t["rev_under8_billed"] = round((cur_t["rev_under8"] + cur_t["rev_under8_excl"]) * ratio)
    prev_t["excess"] = prev_t["rev_total"] - prev_t["rev_billed"]
    cur_t["excess"] = cur_t["rev_total"] - cur_t["rev_billed"]
    totals = {"branch": b.name, "ym": f"{y}-{m:02d}", "prev_ym": f"{py}-{pm:02d}",
              "target": n_active, "excl": sorted(excl), "billed_prev": billed_prev,
              "nonpay": data.get("nonpay", {}),
              "cur": cur_t, "prev": prev_t}
    (hist_dir / f"{key}_{y}{m:02d}_totals.json").write_text(
        json.dumps(totals, ensure_ascii=False, indent=2), encoding="utf-8")


def _save_raw(key, y, m, data, hist_dir):
    """재스크래핑 없이 재생성(제외 변경 등)할 수 있게 원자료 durable 저장."""
    raw = {"cur": data["cur"], "prev": data["prev"], "roster": data["roster"],
           "grade_of": data["grade_of"], "hold_last": data["hold_last"],
           "hold_grade": data.get("hold_grade", {}), "hold_info": data.get("hold_info", {}),
           "billed_prev": data.get("billed_prev", 0), "nonpay": data.get("nonpay", {})}
    (hist_dir / f"{key}_{y}{m:02d}_data.json").write_text(
        json.dumps(raw, ensure_ascii=False), encoding="utf-8")


def rerender(y, m, py, pm, branches, hist_dir, progress=print):
    """저장된 원자료(_data.json)로 케어포 접속 없이 HTML·합계·합본 재생성(제외 명단 변경 반영)."""
    done = []
    for b in branches:
        key = branch_key(b.name)
        f = hist_dir / f"{key}_{y}{m:02d}_data.json"
        if not f.exists():
            progress(f"  {key}: 원자료 없음 — 재실행(스크래핑) 필요, 건너뜀")
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        finalize_branch(b, y, m, py, pm, data, hist_dir, progress)
        done.append(b)
    if done:
        combine_month(y, m, done if len(done) > 1 else branches, progress)


def main():
    argv = [a for a in sys.argv[1:]]
    branch_filter = argv[0] if argv and not re.match(r"\d{4}-\d{2}", argv[0]) else ""
    ym = next((a for a in argv if re.match(r"\d{4}-\d{2}", a)), None)
    if ym:
        y, m = map(int, ym.split("-"))
    else:
        y, m = date.today().year, date.today().month
    py, pm = (y - 1, 12) if m == 1 else (y, m - 1)

    cfg = Config.load(config_path())

    branches_all = cfg.branches
    hist_dir = app_data_dir() / "revenue_history"
    hist_dir.mkdir(parents=True, exist_ok=True)
    pr = lambda s: print(s, flush=True)

    # 합본 전용 모드: 케어포 접속 없이 이미 만들어진 지점 HTML만 합친다.
    if branch_filter in ("combine", "합본", "merge"):
        combine_month(y, m, branches_all, progress=pr)
        return

    # 재생성 모드: 저장된 원자료로 접속 없이 제외명단 변경 등 반영해 재생성.
    if branch_filter in ("rerender", "재생성", "제외적용"):
        print(f"저장된 원자료로 재생성 ({y}-{m:02d}) — 케어포 접속 없음", flush=True)
        rerender(y, m, py, pm, branches_all, hist_dir, progress=pr)
        return

    branches = branches_all
    if branch_filter and branch_filter != "전체":
        branches = [b for b in branches if branch_filter in b.name] or branches

    with sync_playwright() as pw:
        for b in branches:
            key = branch_key(b.name)
            print(f"\n===== {b.name} 매출점검 ({y}-{m:02d}, 전월 {py}-{pm:02d}) =====", flush=True)
            browser = None
            try:
                browser, page = login(pw, b.ctmnumb)
                g = extract_g_pammgno(page)
                data = collect_branch(page, g, y, m, py, pm, progress=pr)
                _save_raw(key, y, m, data, hist_dir)         # 원자료 durable 저장(재생성용)
                finalize_branch(b, y, m, py, pm, data, hist_dir, progress=pr)
            except Exception as ex:
                print(f"  ❌ {b.name} 실패: {ex}", flush=True)
                import traceback
                traceback.print_exc()
            finally:
                if browser:
                    browser.close()

    # 전체/다지점 실행 뒤 합본 자동 생성
    if len(branches) > 1:
        combine_month(y, m, branches, progress=pr)


if __name__ == "__main__":
    main()
