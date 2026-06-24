"""
cyberts.kr 자동차 정기검사 유효기간 조회.
requests로 세션 유지 + 폼 POST → BeautifulSoup으로 날짜 파싱 → 검사 가능기간 계산.
"""
from __future__ import annotations

import re
import requests
from bs4 import BeautifulSoup
from datetime import date, timedelta

CYBERTS_URL = "https://www.cyberts.kr/is/pvi/pvi/readIsPviPrsecValidityInqireView.do"
CYBERTS_API = "https://www.cyberts.kr/is/pvi/pvi/selectIsPviPrsecValidityInqire.do"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    "Referer": CYBERTS_URL,
    "Content-Type": "application/x-www-form-urlencoded",
}


def calc_inspect_period(expiry: date) -> tuple[date, date]:
    """만료일 기준 검사 가능기간: 만료-90일 ~ 만료+31일."""
    return expiry - timedelta(days=90), expiry + timedelta(days=31)


def _parse_date_kr(text: str) -> date | None:
    """'2026년 08월 31일' 또는 '2026-08-31' 형식 파싱."""
    m = re.search(r"(\d{4})[년-][\s]?(\d{1,2})[월-][\s]?(\d{1,2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def fetch_inspect_expiry(car_number: str, corp_number: str) -> date | None:
    """
    cyberts.kr에서 정기검사 만료일 조회.
    corp_number: 구글시트의 법인번호 (사업자등록번호 10자리 또는 법인등록번호 13자리)
    """
    session = requests.Session()

    # 메인 페이지 GET → 쿠키/세션 취득
    try:
        session.get(CYBERTS_URL, headers=HEADERS, timeout=20)
    except Exception as e:
        print(f"    [WARN] 메인페이지 GET 실패: {e}")

    car_no_clean = car_number.replace(" ", "")
    # 사업자번호: 숫자만 추출
    corp_clean = re.sub(r"[^0-9]", "", corp_number)

    form_data = {
        "vhrno": car_no_clean,
        "bzno": corp_clean,
    }
    print(f"    POST {car_no_clean} / bzno={corp_clean[:4]}***")

    try:
        resp = session.post(CYBERTS_API, headers=HEADERS, data=form_data, timeout=20)
        resp.encoding = "utf-8"
    except Exception as e:
        print(f"    [ERROR] POST 실패: {e}")
        return None

    html = resp.text
    print(f"    응답: status={resp.status_code}, len={len(html)}")
    if len(html) < 200:
        print(f"    응답 내용: {html!r}")

    # HTML에서 날짜 파싱 (테이블 셀에서 추출)
    soup = BeautifulSoup(html, "lxml")

    # 날짜 패턴: 2026년 08월 31일 or 2026-08-31
    all_dates: list[date] = []
    for text in soup.stripped_strings:
        d = _parse_date_kr(text)
        if d and d.year >= 2020:
            all_dates.append(d)

    # 정규식으로도 한번 더
    for m in re.findall(r"(20\d{2})[.\-년][\s]?(\d{1,2})[.\-월][\s]?(\d{1,2})", html):
        try:
            d = date(int(m[0]), int(m[1]), int(m[2]))
            if d.year >= 2020:
                all_dates.append(d)
        except ValueError:
            pass

    if not all_dates:
        print(f"    날짜 없음 → 조회 실패 (차단 또는 데이터 없음)")
        return None

    expiry = max(all_dates)
    print(f"    만료일: {expiry}")
    return expiry


def fetch_all_inspect_dates(
    branches_data: dict,
    headless: bool = True,  # 호환성 유지 (미사용)
) -> dict[str, tuple[date, date]]:
    """
    구글시트 전 차량의 법인번호로 cyberts.kr에서 검사 가능기간 조회.
    반환: {차량번호: (inspect_start, inspect_end)}
    """
    result: dict[str, tuple[date, date]] = {}

    for branch, cars in branches_data.items():
        print(f"\n[{branch}]")
        for car in cars:
            car_no = car.get("carNumber", "")
            corp_no = car.get("corpNumber", "").strip()
            if not corp_no:
                print(f"  {car_no}: 법인번호 없음 → 건너뜀")
                continue
            try:
                expiry = fetch_inspect_expiry(car_no, corp_no)
                if expiry:
                    start, end = calc_inspect_period(expiry)
                    result[car_no] = (start, end)
                    print(f"  {car_no} 검사가능: {start} ~ {end}")
                else:
                    print(f"  {car_no}: 만료일 조회 실패")
            except Exception as e:
                print(f"  {car_no} 오류: {e}")

    return result
