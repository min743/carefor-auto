"""
케어포 로그인 + 데이터 수집 (Playwright headless).

전략:
1. 자동로그인 portal HTML에서 지점별 자격증명 자동 추출
2. login2(ctmnumb) 호출 → 새 탭으로 SPA 진입
3. 새 탭에서 g_pammgno (지점별 관리번호) 추출
4. SPA hash URL 직접 구성하여 데이터 화면으로 이동
5. DOM에서 텍스트 추출

SPA URL 형식 (hash routing, base64):
  https://dn.carefor.co.kr/#<base64>
  decode: page|{type, view}%{title, g_pammgno, move_scroll}|<type>

데이터 화면:
  - 수급자 현황(1-7): type=left_sub1, view=/share/patient/view.patient_report
  - 시설운영일지(6-4): type=left_sub6, view=/safe/view.daily_center
  - 월간 입소자(2-8): type=left_sub2, view=/transport/view.monthly_attend_stat
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import date

from playwright.sync_api import Page, sync_playwright

from . import credentials


@dataclass
class BranchAttendance:
    name: str
    ctmnumb: str
    hyeon_won: int      # 현원(수급중)
    gyeol_seok: int     # 결석
    chul_seok: int      # 출석
    avg_attendees: float = 0.0  # 월평균 입소자 수


PORTAL_URL = "https://eform.caring.co.kr/carefor"
DN_BASE = "https://dn.carefor.co.kr/"


def build_spa_hash(type_: str, view: str, title: str, g_pammgno: str) -> str:
    """SPA hash URL 생성. 사용자 캡쳐한 URL과 동일한 직렬화 포맷 유지."""
    # 두 번째 JSON 객체는 공백 없는 표준 JSON
    json_part = json.dumps(
        {"title": title, "g_pammgno": g_pammgno, "move_scroll": True},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    payload = f"page|{{'type':'{type_}', 'view':'{view}'}}%{json_part}|{type_}"
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


def patient_report_url(g_pammgno: str) -> str:
    h = build_spa_hash("left_sub1", "/share/patient/view.patient_report", "1-7.수급자 현황 리포트", g_pammgno)
    return f"{DN_BASE}#{h}"


def daily_center_url(g_pammgno: str) -> str:
    h = build_spa_hash("left_sub6", "/safe/view.daily_center", "6-4.시설운영일지", g_pammgno)
    return f"{DN_BASE}#{h}"


def monthly_attend_url(g_pammgno: str) -> str:
    h = build_spa_hash("left_sub2", "/transport/view.monthly_attend_stat", "2-8.월간 입소자, 일정, 서비스 현황", g_pammgno)
    return f"{DN_BASE}#{h}"


def _navigate_spa(page: Page, full_url: str) -> None:
    """
    SPA hash 변경. hash-only 변경은 reload를 안 일으키므로
    명시적으로 hashchange 이벤트 트리거.
    """
    new_hash = full_url.split("#", 1)[1]
    page.evaluate(
        f"""
        (() => {{
            const newHash = '{new_hash}';
            if (window.location.hash !== '#' + newHash) {{
                window.location.hash = newHash;
            }} else {{
                window.dispatchEvent(new HashChangeEvent('hashchange'));
            }}
        }})()
        """
    )
    # SPA가 새 뷰 렌더링할 시간
    page.wait_for_timeout(1500)


def extract_g_pammgno(page: Page) -> str:
    """
    로그인 직후 페이지에서 g_pammgno 추출.
    SPA가 글로벌 변수나 hash URL에 보유.
    """
    # 1) 현재 URL hash에서 찾기
    url = page.url
    if "#" in url:
        try:
            hash_part = url.split("#", 1)[1]
            decoded = base64.b64decode(hash_part).decode("utf-8")
            m = re.search(r'"g_pammgno":\s*"(\d+)"', decoded)
            if m:
                return m.group(1)
        except Exception:
            pass

    # 2) 페이지의 글로벌 JS 변수에서 찾기
    try:
        result = page.evaluate("typeof g_pammgno !== 'undefined' ? String(g_pammgno) : (window.g_pammgno || '')")
        if result:
            return str(result)
    except Exception:
        pass

    # 3) 페이지 내 input/data 속성에서 찾기
    try:
        result = page.evaluate("""
            (() => {
                const el = document.querySelector('[name="g_pammgno"], [data-pammgno], #g_pammgno');
                return el ? (el.value || el.dataset.pammgno || el.textContent || '') : '';
            })()
        """)
        if result:
            return str(result).strip()
    except Exception:
        pass

    raise RuntimeError("g_pammgno 추출 실패 — 로그인 후 페이지에 해당 값이 없습니다")


def scrape_patient_report(page: Page) -> int:
    """
    1-7 수급자 현황 리포트에서 '수급중' 상태 인원 카운트.
    테이블 셀에서 정확히 '수급중'인 항목만 집계.
    """
    page.wait_for_load_state("networkidle", timeout=30000)
    page.wait_for_timeout(2000)
    _close_popups(page)
    page.wait_for_timeout(500)

    # 방법 1: 테이블 셀 중 정확히 '수급중'인 것 카운트
    count = page.evaluate("""
        (() => {
            const cells = document.querySelectorAll('td, th');
            let cnt = 0;
            cells.forEach(td => {
                if (td.textContent.trim() === '수급중') cnt++;
            });
            return cnt;
        })()
    """)
    if count > 0:
        return count

    # 방법 2: innerText 줄 단위에서 정확히 '수급중'인 행 카운트
    body_text = page.evaluate("document.body.innerText")
    lines = [l.strip() for l in body_text.split("\n")]
    count = sum(1 for l in lines if l == "수급중")
    if count > 0:
        return count

    raise RuntimeError("1-7 수급자 현황에서 '수급중' 인원을 찾을 수 없습니다")


def scrape_daily_center(page: Page) -> tuple[int, int]:
    """
    시설운영일지 화면에서 현원, 결석 추출.
    화면 텍스트 예시:
      "수급자현황(총 73명)"
      "결석(0명)"
    """
    page.wait_for_load_state("networkidle", timeout=30000)
    page.wait_for_timeout(2000)
    _close_popups(page)
    page.wait_for_timeout(500)

    body_text = page.evaluate("document.body.innerText")

    m_hyeon = re.search(r"수급자현황\s*\(\s*총?\s*(\d+)\s*명\s*\)", body_text)
    m_gyeol = re.search(r"결석\s*\(\s*(\d+)\s*명\s*\)", body_text)

    if not m_hyeon:
        raise RuntimeError("시설운영일지에서 '수급자현황(총 N명)' 텍스트를 찾을 수 없습니다")
    if not m_gyeol:
        raise RuntimeError("시설운영일지에서 '결석(N명)' 텍스트를 찾을 수 없습니다")

    return int(m_hyeon.group(1)), int(m_gyeol.group(1))


def _close_popups(page: Page) -> None:
    """팝업/알림창 닫기. 페이지 진입 시 자주 뜸 (수급자 인정 만료 등)."""
    page.wait_for_timeout(500)
    try:
        # "창닫기" 버튼들 모두 클릭
        page.evaluate("""
            (() => {
                const buttons = document.querySelectorAll('button, input[type="button"]');
                let closed = 0;
                buttons.forEach(b => {
                    const txt = (b.textContent || b.value || '').trim();
                    if (['창닫기', '닫기', '확인', 'X', '×'].includes(txt)) {
                        try {
                            const rect = b.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                b.click();
                                closed++;
                            }
                        } catch (e) {}
                    }
                });
                return closed;
            })()
        """)
    except Exception:
        pass


def scrape_monthly_attend(page: Page, target: date) -> tuple[int, float]:
    """
    월간 입소자 화면에서 오늘 날짜 행의 합계 + 월평균 입소자 수 추출.
    반환: (오늘_출석, 월평균)  예) (62, 60.3)
    """
    page.wait_for_load_state("networkidle", timeout=30000)
    page.wait_for_timeout(2000)
    _close_popups(page)
    page.wait_for_timeout(500)

    date_pattern = target.strftime("%Y.%m.%d")
    date_re = re.compile(r"20\d{2}\.\d{2}\.\d{2}")

    today_total: int | None = None
    daily_totals: list[int] = []

    # 방법 1: <tr><td> 표 구조
    rows = page.evaluate("""
        Array.from(document.querySelectorAll('tr')).map(tr =>
            Array.from(tr.querySelectorAll('td,th')).map(td => td.textContent.trim())
        ).filter(r => r.length > 0)
    """)
    for row in rows:
        has_date = any(date_re.search(cell) for cell in row)
        if not has_date:
            continue
        for cell in reversed(row):
            if re.fullmatch(r"\d+", cell.strip()):
                val = int(cell.strip())
                daily_totals.append(val)
                if any(date_pattern in c for c in row):
                    today_total = val
                break

    if today_total is not None and daily_totals:
        avg = round(sum(daily_totals) / len(daily_totals), 1)
        return today_total, avg

    # 방법 2: 텍스트 라인 매칭
    body_text = page.evaluate("document.body.innerText")
    lines = [l.strip() for l in body_text.split("\n") if l.strip()]
    daily_totals = []

    for i, line in enumerate(lines):
        if not date_re.search(line):
            continue
        nums_in_line = re.findall(r"\b\d+\b", line)
        if len(nums_in_line) >= 6:
            val = int(nums_in_line[-1])
            daily_totals.append(val)
            if date_pattern in line:
                today_total = val
        else:
            following = []
            for j in range(i + 1, min(i + 15, len(lines))):
                if date_re.search(lines[j]):
                    break
                if re.fullmatch(r"-|\d+", lines[j]):
                    following.append(lines[j])
                if len(following) >= 7:
                    break
            if len(following) >= 6:
                last = following[-1]
                if last != "-":
                    val = int(last)
                    daily_totals.append(val)
                    if date_pattern in line:
                        today_total = val

    if today_total is None:
        raise RuntimeError(f"월간 입소자 표에서 {date_pattern} 행을 찾을 수 없습니다")

    avg = round(sum(daily_totals) / len(daily_totals), 1) if daily_totals else 0.0
    return today_total, avg


def fetch_branch_attendance(
    ctmnumb: str,
    branch_name: str,
    target_date: date | None = None,
    headless: bool = True,
) -> BranchAttendance:
    """한 지점의 오늘 출석 데이터 수집."""
    target_date = target_date or date.today()

    portal_creds = credentials.get_portal_credentials()
    if not portal_creds:
        raise RuntimeError(
            "케어포 portal 자격증명이 저장되어 있지 않습니다. "
            "먼저 'python setup_credentials.py' 를 실행해 ID/비밀번호를 저장하세요."
        )
    portal_id, portal_pw = portal_creds

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            http_credentials={"username": portal_id, "password": portal_pw}
        )
        portal_page = ctx.new_page()

        # 1) portal 진입
        portal_page.goto(PORTAL_URL, wait_until="domcontentloaded")
        portal_page.wait_for_function("typeof login2 === 'function'", timeout=15000)

        # 2) login2 호출 → 새 탭으로 dn.carefor.co.kr 열림
        with ctx.expect_page(timeout=30000) as new_page_info:
            portal_page.evaluate(f"login2('{ctmnumb}')")
        data_page = new_page_info.value
        data_page.wait_for_load_state("domcontentloaded", timeout=30000)
        data_page.wait_for_load_state("networkidle", timeout=30000)

        # 3) g_pammgno 추출
        g_pammgno = extract_g_pammgno(data_page)

        # 4) 1-7 수급자 현황 → 현원(수급중)
        _navigate_spa(data_page, patient_report_url(g_pammgno))
        hyeon_won = scrape_patient_report(data_page)

        # 4-1) 시설운영일지 → 결석
        _navigate_spa(data_page, daily_center_url(g_pammgno))
        _, gyeol_seok = scrape_daily_center(data_page)

        # 5) 월간 입소자 진입 → 출석 + 월평균
        _navigate_spa(data_page, monthly_attend_url(g_pammgno))
        chul_seok, avg_attendees = scrape_monthly_attend(data_page, target_date)

        browser.close()

        return BranchAttendance(
            name=branch_name,
            ctmnumb=ctmnumb,
            hyeon_won=hyeon_won,
            gyeol_seok=gyeol_seok,
            chul_seok=chul_seok,
            avg_attendees=avg_attendees,
        )
