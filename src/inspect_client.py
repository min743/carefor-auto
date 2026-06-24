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

    # 차량번호 입력
    page.fill('input[placeholder*="자동차등록번호"]', car_number)
    page.wait_for_timeout(500)

    # 법인번호 입력 (앞 6자리)
    page.fill('input[placeholder*="등록번호 앞 6자리"]', corp_number[:6])
    page.wait_for_timeout(500)

    # 검색 버튼 클릭
    page.click('button:has-text("검색"), input[value*="검색"]')
    page.wait_for_timeout(3000)

    body_text = page.evaluate("document.body.innerText")

    # 테이블에서 날짜 추출: YYYY-MM-DD 형식
    dates = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", body_text)
    valid_dates = []
    for d_str in dates:
        try:
            valid_dates.append(date.fromisoformat(d_str))
        except ValueError:
            pass

    if len(valid_dates) < 2:
        return None

    # 만료일 = 가장 미래 날짜
    return max(valid_dates)


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
