import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.image_report import generate_image
from src import credentials
from src.slack_notifier import send_image_via_api

target = date(2026, 6, 22)
branches = [
    {"name": "둔산점",    "hyeon_won": 69, "gyeol_seok": 4,  "chul_seok": 65, "capacity": 76, "avg_attendees": 56.74},
    {"name": "서구점",    "hyeon_won": 78, "gyeol_seok": 10, "chul_seok": 68, "capacity": 84, "avg_attendees": 59.68},
    {"name": "천안점",    "hyeon_won": 64, "gyeol_seok": 16, "chul_seok": 48, "capacity": 82, "avg_attendees": 44.37},
    {"name": "청주오창점","hyeon_won": 50, "gyeol_seok": 3,  "chul_seok": 47, "capacity": 62, "avg_attendees": 43.84},
]

img_bytes = generate_image(target, branches)

# 바탕화면 저장
out = Path.home() / "Desktop" / "출석현황_test.png"
out.write_bytes(img_bytes)
print(f"이미지 저장: {out}")

# 슬랙 전송
token = credentials.get_slack_bot_token()
send_image_via_api(token, "C0BC37EB38C", img_bytes, title="지점별 출석 현황")
print("슬랙 전송 완료")
