"""
구글시트 webhook 단독 테스트.
가짜 데이터 1줄을 Apps Script로 보내서 시트에 반영되는지 확인.
"""
from datetime import date

from src import credentials, sheet_writer


def main():
    url = credentials.get_sheet_webhook()
    if not url:
        print("❌ Webhook URL 미저장. set_sheet_webhook.py 먼저 실행.")
        return

    print(f"Webhook URL: {url[:60]}...")
    print()
    print("테스트 데이터 전송 중...")

    fake_data = [
        {"name": "둔산점", "hyeon_won": 73, "gyeol_seok": 0, "chul_seok": 63, "capacity": 76},
    ]

    try:
        result = sheet_writer.post_daily_rows(
            webhook_url=url,
            target_date=date(2026, 6, 19),  # 금요일
            branches_data=fake_data,
        )
        print(f"✅ 응답: {result}")
        print()
        print("구글시트의 '데이터' 탭을 열어 6/19일 둔산점 행이 추가됐는지 확인하세요.")
    except Exception as e:
        print(f"❌ 실패: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
