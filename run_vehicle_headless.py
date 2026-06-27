"""
GitHub Actions에서 차량관리 보고를 슬랙으로 전송.
"""
import os, sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_cars import (fetch_vehicle_data, fetch_carefor_mileage, apply_carefor_mileage,
                        save_mileage_to_sheet,
                        fetch_cyberts_inspect_dates, apply_cyberts_inspect_dates,
                        build_vehicle_message)
from slack_sdk import WebClient

# 환경변수에서 자격증명 패치 (keyring 우회)
import src.credentials as _creds
_env_map = {
    _creds.KEY_PORTAL_ID:       os.environ.get("CAREFOR_ID"),
    _creds.KEY_PORTAL_PASSWORD: os.environ.get("CAREFOR_PW"),
    _creds.KEY_SLACK_BOT_TOKEN: os.environ.get("SLACK_BOT_TOKEN"),
}
_orig_get = _creds.get
def _patched_get(key):
    if key in _env_map and _env_map[key]:
        return _env_map[key]
    return _orig_get(key)
_creds.get = _patched_get

token   = os.environ.get("SLACK_BOT_TOKEN")
channel = os.environ.get("SLACK_CHANNEL", "C0BC37EB38C")

if not token:
    print("ERROR: SLACK_BOT_TOKEN 환경변수가 없습니다.")
    sys.exit(1)

today = date.today()

# 1) 구글시트에서 차량 기본 정보 수집
print("구글시트 차량 데이터 수집 중...")
branches_data = fetch_vehicle_data()

# 2) 케어포에서 주행거리 + 오일 정보 수집
print("케어포 주행거리/오일 수집 중...")
carefor_data = fetch_carefor_mileage(headless=True)
print(f"총 {len(carefor_data)}대 수집 완료")

# 3) 데이터 반영 + 구글시트 저장
branches_data = apply_carefor_mileage(branches_data, carefor_data)
try:
    updated = save_mileage_to_sheet(carefor_data)
    print(f"구글시트 주행거리/오일 업데이트 완료: {updated}대")
    sheet_car_nos = {
        c.get('carNumber', '').replace(' ', '')
        for cars in branches_data.values() for c in cars
    }
    unmatched = [no for no in carefor_data if no.replace(' ', '') not in sheet_car_nos]
    if unmatched:
        print(f"  [구글시트 미등록 차량 {len(unmatched)}대]: {', '.join(unmatched)}")
except Exception as e:
    print(f"구글시트 저장 오류 (슬랙 보고는 계속): {e}")

# 4) cyberts.kr에서 정기검사 가능기간 조회 및 반영
print("cyberts.kr 정기검사 가능기간 조회 중...")
try:
    inspect_dates = fetch_cyberts_inspect_dates(branches_data, headless=True)
    print(f"   {len(inspect_dates)}대 검사기간 조회 완료")
    branches_data, inspect_save_data = apply_cyberts_inspect_dates(branches_data, inspect_dates)
    if inspect_save_data:
        updated = save_mileage_to_sheet({d['carNumber']: {
            'inspectStart': d['inspectStart'],
            'inspectEnd': d['inspectEnd']
        } for d in inspect_save_data})
        print(f"   구글시트 검사기간 업데이트 완료: {updated}대")
except Exception as e:
    print(f"   cyberts 조회 오류 (기존 값 유지): {e}")

msg = build_vehicle_message(today, branches_data)

print("\n=== 메시지 미리보기 ===")
print(msg)
print("=" * 40)

MENTION_IDS = [
    "U08908V4Y64",  # 둔산점 센터장
    "U07K74212MV",  # 서구점 센터장
    "U087FH5CKL0",  # 청주 오창점 센터장
    "U03DFLVSQ91",  # 천안점 센터장
    "U0B2T1QAN1X",  # 대전점 김유경 센터장
]
mention_text = " ".join(f"<@{uid}>" for uid in MENTION_IDS)
full_msg = f"{mention_text}\n{msg}"

if os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"):
    print("DRY_RUN 모드: 슬랙 전송 건너뜀")
else:
    client = WebClient(token=token, retry_handlers=[])
    client.chat_postMessage(channel=channel, text=full_msg)
    print("차량관리 보고 전송 완료")
