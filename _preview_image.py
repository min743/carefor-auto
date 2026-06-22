import sys
sys.path.insert(0, ".")
from datetime import date
from src.image_report import generate_image

branches = [
    {"name": "둔산점",     "hyeon_won": 69, "gyeol_seok": 0, "chul_seok": 65, "capacity": 80},
    {"name": "서구점",     "hyeon_won": 79, "gyeol_seok": 3, "chul_seok": 71, "capacity": 80},
    {"name": "천안점",     "hyeon_won": 64, "gyeol_seok": 2, "chul_seok": 50, "capacity": 70},
    {"name": "청주오창점", "hyeon_won": 50, "gyeol_seok": 3, "chul_seok": 47, "capacity": 60},
]

img_bytes = generate_image(date(2026, 6, 22), branches)
out = "attendance_preview.png"
with open(out, "wb") as f:
    f.write(img_bytes)
print(f"저장됨: {out}  ({len(img_bytes):,} bytes)")
