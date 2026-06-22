"""
케어포 스크래퍼 단독 테스트 (구글시트/슬랙 연동 없이).

실행:
  cd "C:\\Users\\alsgm\\OneDrive\\Desktop\\클로드 코드\\carefor-auto"
  .venv\\Scripts\\python.exe test_scraper.py

기본값: headless=False (브라우저 창 뜸 — 무엇이 일어나는지 볼 수 있음)
       1개 지점(둔산점)만 테스트 — 성공하면 ALL_BRANCHES=True 로 바꿔서 4개 다 테스트
"""
from datetime import date

from src.carefor_client import fetch_branch_attendance


HEADLESS = False     # True 로 바꾸면 브라우저 안 보이게 실행
ALL_BRANCHES = True  # True 로 바꾸면 4개 지점 모두 테스트

# 테스트할 날짜 — None 이면 오늘.
# 주말엔 데이터가 비어있으므로 평일로 지정해 테스트:
TEST_DATE = date(2026, 6, 19)  # 금요일

ALL = [
    ("23017000602", "둔산점"),
    ("23017000617", "서구점"),
    ("24413000644", "천안점"),
    ("24311001003", "청주 오창점"),
]


def main():
    targets = ALL if ALL_BRANCHES else ALL[:1]  # 기본은 둔산점만
    today = TEST_DATE or date.today()
    print(f"테스트 날짜: {today}\n")

    for ctmnumb, name in targets:
        print(f"=== {name} ({ctmnumb}) ===")
        try:
            att = fetch_branch_attendance(ctmnumb, name, target_date=today, headless=HEADLESS)
            print(f"  현원: {att.hyeon_won}")
            print(f"  결석: {att.gyeol_seok}")
            print(f"  출석: {att.chul_seok}")
        except Exception as e:
            print(f"  실패: {e}")
            import traceback
            traceback.print_exc()
        print()


if __name__ == "__main__":
    main()
