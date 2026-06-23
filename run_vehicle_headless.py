"""
GitHub Actions에서 차량관리 보고를 슬랙으로 전송.
"""
import os, sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_cars import fetch_vehicle_data, fetch_carefor_mileage, apply_carefor_mileage, build_vehicle_message
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

# 2) 케어포에서 실제 주행거리 수집
print("케어포 주행거리 수집 중...")
carefor_km = fetch_carefor_mileage(headless=True)
print(f"총 {len(carefor_km)}대 주행거리 수집 완료")

# 3) 주행거리 반영
branches_data = apply_carefor_mileage(branches_data, carefor_km)

msg = build_vehicle_message(today, branches_data)

print("\n=== 메시지 미리보기 ===")
print(msg)
print("=" * 40)

if os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"):
    print("DRY_RUN 모드: 슬랙 전송 건너뜀")
else:
    client = WebClient(token=token)
    client.chat_postMessage(channel=channel, text=msg)
    print("차량관리 보고 전송 완료")
