import requests, json, sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src import credentials

API_URL = 'https://script.google.com/macros/s/AKfycbzc4VeJQQpeYblx3Ack2Yrp1q0pJPe_LM9g8_3noB6-DJCaLMsFVENZDFOXYzvUHT0/exec'

# 지점별 케어포 센터코드 (천안점은 데이터 입력 중이라 주행거리 스크래핑 제외)
BRANCH_CTMNUMB = {
    "둔산점":     "23017000602",
    "서구점":     "23017000617",
    "청주 오창점": "24311001003",
}

# 자차 제외 차량번호 (끝 4자리)
EXCLUDE_CAR_SUFFIX = {"3702", "5346"}


def _is_excluded_car(car_no: str) -> bool:
    """자차(개인 차량)는 차량번호 끝 4자리로 판별."""
    digits = ''.join(filter(str.isdigit, car_no))
    return digits[-4:] in EXCLUDE_CAR_SUFFIX if len(digits) >= 4 else False


def fetch_vehicle_data():
    res = requests.post(API_URL,
        headers={'Content-Type': 'text/plain;charset=utf-8'},
        data=json.dumps({'action': 'getAll'}),
        timeout=20)
    res.encoding = 'utf-8'
    raw = res.json()
    branches = raw['data']['branches']
    data = raw['data']['data']
    result = {}
    for br in branches:
        if not data.get(br):
            continue
        cars = [c for c in data[br] if not _is_excluded_car(c.get('carNumber', ''))]
        result[br] = cars
    return result


def fetch_carefor_mileage(headless: bool = True) -> dict[str, int]:
    """케어포에서 전 지점 차량별 누적 주행거리 수집. 반환: {차량번호: km}"""
    from src.carefor_client import fetch_branch_car_mileage
    result: dict[str, int] = {}
    for branch, ctmnumb in BRANCH_CTMNUMB.items():
        try:
            mileage = fetch_branch_car_mileage(ctmnumb, branch, headless=headless)
            result.update(mileage)
            print(f"  {branch}: {len(mileage)}대 수집")
        except Exception as e:
            print(f"  {branch} 오류: {e}")
    return result


def apply_carefor_mileage(branches_data: dict, carefor_km: dict[str, int]) -> dict:
    """구글시트 차량 데이터에 케어포 주행거리 덮어쓰기."""
    for branch, cars in branches_data.items():
        for car in cars:
            car_no = car.get('carNumber', '')
            # 차량번호 끝 4자리로 매칭 (공백/형식 차이 대응)
            matched_km = None
            for cn, km in carefor_km.items():
                if cn.replace(" ", "") == car_no.replace(" ", ""):
                    matched_km = km
                    break
            if matched_km is not None:
                car['totalKm'] = matched_km
    return branches_data


def classify(cars, today):
    oil_over, oil_soon, insp_soon = [], [], []
    for c in cars:
        total_km  = c.get('totalKm') or 0
        next_km   = c.get('oilNextKm') or 0
        oil_date  = c.get('oilDate')
        remain    = next_km - total_km

        # 오일 교환 초과: km 초과 OR 1년 이상 경과
        oil_over_km   = total_km > 0 and remain < 0
        oil_over_date = False
        days_since_oil = None
        if oil_date:
            try:
                days_since_oil = (today - date.fromisoformat(oil_date)).days
                oil_over_date  = days_since_oil >= 365
            except ValueError:
                pass

        if oil_over_km or oil_over_date:
            if oil_over_km:
                detail = f"{abs(remain):,}km 초과"
            else:
                months = days_since_oil // 30
                detail = f"1년 경과" if months >= 12 else f"{months}개월 경과"
            oil_over.append((c['carNumber'], c.get('carModel', ''), detail))

        elif total_km > 0 and remain <= 1000:
            oil_soon.append((c['carNumber'], c.get('carModel', ''), f"{remain:,}km 남음"))

        # 정기검사 임박 (60일 이내)
        end = c.get('inspectEnd')
        if end:
            try:
                days_left = (date.fromisoformat(end) - today).days
                if 0 <= days_left <= 60:
                    insp_soon.append((c['carNumber'], c.get('carModel', ''), f"{days_left}일 남음"))
            except ValueError:
                pass

    return oil_over, oil_soon, insp_soon


def build_vehicle_message(today, branches_data):
    weekday_kr = ["월","화","수","목","금","토","일"][today.weekday()]

    all_oil_over, all_oil_soon, all_insp_soon = [], [], []
    total_cars = 0
    for br, cars in branches_data.items():
        total_cars += len(cars)
        oo, os_, is_ = classify(cars, today)
        all_oil_over  += [(br, *x) for x in oo]
        all_oil_soon  += [(br, *x) for x in os_]
        all_insp_soon += [(br, *x) for x in is_]

    lines = [
        f":car: *충청본부 차량관리 주간 보고*",
        f"{today.year}년 {today.month}월 {today.day}일",
        f"",
        f"*전체 차량: {total_cars}대*",
    ]

    # 오일 교환 초과
    lines.append(f"")
    lines.append(f":red_circle: *오일 교환 초과 ({len(all_oil_over)}대)*")
    if all_oil_over:
        for br, num, model, detail in all_oil_over:
            lines.append(f"• {br} {num} {model} — {detail}")
    else:
        lines.append("• 해당 없음")

    # 오일 교체 임박
    lines.append(f"")
    lines.append(f":large_yellow_circle: *오일 교체 임박 ({len(all_oil_soon)}대, 1,000km 이내)*")
    if all_oil_soon:
        for br, num, model, detail in all_oil_soon:
            lines.append(f"• {br} {num} {model} — {detail}")
    else:
        lines.append("• 해당 없음")

    # 정기검사 임박
    lines.append(f"")
    lines.append(f":large_yellow_circle: *정기 검사 임박 ({len(all_insp_soon)}대, 60일 이내)*")
    if all_insp_soon:
        for br, num, model, detail in all_insp_soon:
            lines.append(f"• {br} {num} {model} — {detail}")
    else:
        lines.append("• 해당 없음")

    return "\n".join(lines)


if __name__ == "__main__":
    today = date.today()
    branches_data = fetch_vehicle_data()
    msg = build_vehicle_message(today, branches_data)
    sys.stdout.buffer.write((msg + "\n").encode("utf-8"))
