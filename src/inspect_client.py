"""
cyberts.kr 자동차 정기검사 유효기간 조회.
차량번호 + 법인번호로 검사 만료일 조회 후 가능기간 계산.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from playwright.sync_api import Page, sync_playwright

CYBERTS_URL = "https://www.cyberts.kr/is/pvi/pvi/readIsPviPvsecValidityInqireView.do"


def calc_inspect_period(expiry: date) -> tuple[date, date]:
    """만료일 기준 검사 가능기간 계산. (만료-90일 ~ 만료+31일)"""
    return expiry - timedelta(days=90), expiry + timedelta(days=31)


def fetch_inspect_expiry(car_number: str, corp_number: str, headless: bool = True) -> date | None:
    """
    cyberts.kr에서 정기검사 만료일 조회.
    반환: 만료일 date 객체, 조회 실패 시 None
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            result = _query_inspect(page, car_number, corp_number)
        finally:
            browser.close()
    return result


def _query_inspect(page: Page, car_number: str, corp_number: str) -> date | None:
    page.goto(CYBERTS_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    # 차량번호 입력 필드
    car_input = page.query_selector(
        'input[name="vhrno"], input[id="vhrno"], input[placeholder*="차량번호"], input[placeholder*="자동차번호"]'
    )
    if car_input:
        car_input.fill(car_number)

    # 법인번호 입력 필드
    corp_input = page.query_selector(
        'input[name="bzno"], input[id="bzno"], input[name="corpNo"], input[placeholder*="법인"]'
    )
    if corp_input:
        corp_input.fill(corp_number)

    # 조회 버튼 클릭
    page.evaluate("""
        (() => {
            const btns = document.querySelectorAll('button, input[type="submit"], input[type="button"], a');
            for (const b of btns) {
                const txt = (b.textContent || b.value || '').trim();
                if (txt.includes('조회') || txt.includes('검색')) {
                    b.click();
                    return true;
                }
            }
            return false;
        })()
    """)
    page.wait_for_timeout(3000)

    body_text = page.evaluate("document.body.innerText")
    print(f"  [DEBUG cyberts] {body_text[:500]}")

    # 날짜 패턴 추출: YYYY.MM.DD 또는 YYYY-MM-DD 또는 YYYY년MM월DD일
    patterns = [
        r"(\d{4})[.\-년](\d{1,2})[.\-월](\d{1,2})",
    ]
    candidates = []
    for pat in patterns:
        for m in re.finditer(pat, body_text):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 2020 <= y <= 2035 and 1 <= mo <= 12 and 1 <= d <= 31:
                candidates.append(date(y, mo, d))

    if not candidates:
        return None

    # 만료일 = 가장 미래 날짜
    return max(candidates)


def fetch_all_inspect_dates(
    branches_data: dict,
    headless: bool = True,
) -> dict[str, tuple[date, date]]:
    """
    구글시트 전 차량의 법인번호를 읽어 cyberts.kr에서 검사 가능기간 조회.
    반환: {차량번호: (inspect_start, inspect_end)}
    """
    result: dict[str, tuple[date, date]] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        for branch, cars in branches_data.items():
            for car in cars:
                car_no = car.get('carNumber', '')
                corp_no = car.get('corpNumber', '').strip()
                if not corp_no:
                    continue

                try:
                    expiry = _query_inspect(page, car_no, corp_no)
                    if expiry:
                        start, end = calc_inspect_period(expiry)
                        result[car_no] = (start, end)
                        print(f"  {branch} {car_no}: {start} ~ {end}")
                    else:
                        print(f"  {branch} {car_no}: 만료일 조회 실패")
                except Exception as e:
                    print(f"  {branch} {car_no} 오류: {e}")

        browser.close()

    return result
