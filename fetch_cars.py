import requests, json, sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src import credentials

API_URL = 'https://script.google.com/macros/s/AKfycbzc4VeJQQpeYblx3Ack2Yrp1q0pJPe_LM9g8_3noB6-DJCaLMsFVENZDFOXYzvUHT0/exec'

def fetch_vehicle_data():
    res = requests.post(API_URL,
        headers={'Content-Type': 'text/plain;charset=utf-8'},
        data=json.dumps({'action': 'getAll'}),
        timeout=20)
    res.encoding = 'utf-8'
    raw = res.json()
    branches = raw['data']['branches']
    data = raw['data']['data']
    return {br: data[br] for br in branches if data.get(br)}


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

    from slack_sdk import WebClient
    token = credentials.get_slack_bot_token()
    client = WebClient(token=token)
    client.chat_postMessage(channel="C087JL55TA6", text=msg)
    print("\n슬랙 전송 완료")
