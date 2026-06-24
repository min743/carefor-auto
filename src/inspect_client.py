"""
cyberts.kr 자동차 정기검사 유효기간 조회.
1) GET 페이지 → CSRF 토큰 추출
2) POST with CSRF → JSON 파싱 (gsFymd/gsTymd: YYYYMMDD)
3) 검사 가능기간 = 종료일 -90일 ~ +31일
"""
from __future__ import annotations

import re
import json
import requests
from bs4 import BeautifulSoup
from datetime import date, timedelta

CYBERTS_URL = "https://www.cyberts.kr/is/pvi/pvi/readIsPviPrsecValidityInqireView.do"
CYBERTS_API = "https://www.cyberts.kr/is/pvi/pvi/readIsPviPrsecValidityInqireInfo.do"

_BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def calc_inspect_period(expiry: date) -> tuple[date, date]:
    """만료일 기준 검사 가능기간: 만료-90일 ~ 만료+31일."""
    return expiry - timedelta(days=90), expiry + timedelta(days=31)


def _get_csrf_token(session: requests.Session) -> str | None:
    """메인 페이지 GET → CSRF 토큰 추출."""
    resp = session.get(CYBERTS_URL, headers=_BASE_HEADERS, timeout=20)
    resp.encoding = "utf-8"
    # meta 또는 script에서 CSRF 토큰 추출
    m = re.search(r'"X-CSRF-TOKEN"\s*,\s*"([^"]+)"', resp.text)
    if not m:
        m = re.search(r'_csrf["\s]+[:=]\s*["\']([a-f0-9\-]{30,})["\']', resp.text)
    if m:
        return m.group(1)
    # 폼 hidden _csrf 값으로도 시도
    soup = BeautifulSoup(resp.text, "lxml")
    tag = soup.find("input", {"name": "_csrf"})
    if tag:
        return tag.get("value")
    return None


def fetch_inspect_expiry(car_number: str, corp_number: str, session: requests.Session | None = None) -> date | None:
    """
    cyberts.kr에서 정기검사 유효기간 종료일 조회.
    corp_number: 사업자/법인등록번호 (앞 6자리 사용)
    """
    if session is None:
        session = requests.Session()

    csrf = _get_csrf_token(session)
    if not csrf:
        print(f"    [WARN] CSRF 토큰 추출 실패")
        csrf = ""

    car_no_clean = car_number.replace(" ", "")
    regi_6 = re.sub(r"[^0-9]", "", corp_number)[:6]

    form_data = {
        "searchType": "car",
        "searchCarNo": car_no_clean,
        "searchRegiNum": regi_6,
        "_csrf": csrf,
    }
    post_headers = {
        **_BASE_HEADERS,
        "Referer": CYBERTS_URL,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-CSRF-TOKEN": csrf,
        "X-Requested-With": "XMLHttpRequest",
    }

    print(f"    POST {car_no_clean} / regi={regi_6} / csrf={csrf[:8]}...")
    try:
        resp = session.post(CYBERTS_API, headers=post_headers, data=form_data, timeout=20)
        resp.encoding = "utf-8"
    except Exception as e:
        print(f"    [ERROR] POST 실패: {e}")
        return None

    print(f"    응답: status={resp.status_code}, len={len(resp.text)}")

    if resp.status_code != 200 or not resp.text.strip():
        return None

    try:
        data = json.loads(resp.text)
    except json.JSONDecodeError:
        print(f"    JSON 파싱 실패: {resp.text[:200]}")
        return None

    items = data.get("isPviPrsecValidityInqireDomain") or []
    if not items:
        print(f"    데이터 없음")
        return None

    # gsTymd: 종료일 YYYYMMDD
    expiry_str = items[0].get("gsTymd", "")
    if len(expiry_str) == 8:
        try:
            expiry = date(int(expiry_str[:4]), int(expiry_str[4:6]), int(expiry_str[6:8]))
            print(f"    만료일: {expiry}")
            return expiry
        except ValueError:
            pass

    print(f"    날짜 파싱 실패: {expiry_str}")
    return None


def fetch_all_inspect_dates(
    branches_data: dict,
    headless: bool = True,  # 호환성 유지 (미사용)
) -> dict[str, tuple[date, date]]:
    """
    구글시트 전 차량의 법인번호로 cyberts.kr에서 검사 가능기간 조회.
    반환: {차량번호: (inspect_start, inspect_end)}
    """
    result: dict[str, tuple[date, date]] = {}
    # 세션 1개 재사용 (쿠키/세션 유지)
    session = requests.Session()

    for branch, cars in branches_data.items():
        print(f"\n[{branch}]")
        for car in cars:
            car_no = car.get("carNumber", "")
            corp_no = car.get("corpNumber", "").strip()
            if not corp_no:
                print(f"  {car_no}: 법인번호 없음 → 건너뜀")
                continue
            try:
                expiry = fetch_inspect_expiry(car_no, corp_no, session=session)
                if expiry:
                    start, end = calc_inspect_period(expiry)
                    result[car_no] = (start, end)
                    print(f"  → 검사가능: {start} ~ {end}")
                else:
                    print(f"  {car_no}: 조회 실패")
            except Exception as e:
                print(f"  {car_no} 오류: {e}")

    return result
