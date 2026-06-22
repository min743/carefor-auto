"""
케어포 portal 자격증명을 Windows 자격증명 관리자에 저장.
한 번만 실행하면 됨. 비밀번호 바뀌면 다시 실행.

실행:
  .venv\\Scripts\\python.exe setup_credentials.py
"""
import getpass

from src import credentials


def main():
    print("=" * 50)
    print("케어포 자동로그인 portal 자격증명 저장")
    print("=" * 50)
    print()
    print("입력한 정보는 Windows 자격증명 관리자에 암호화 저장됩니다.")
    print("(본인 Windows 계정으로 로그인된 상태에서만 복호화 가능)")
    print()

    # 기존 저장 여부 확인
    existing = credentials.get_portal_credentials()
    if existing:
        existing_id, _ = existing
        ans = input(f"이미 저장된 ID가 있습니다 ({existing_id}). 덮어쓰시겠어요? [y/N]: ").strip().lower()
        if ans != "y":
            print("취소됨.")
            return

    portal_id = input("Portal ID (예: caring): ").strip()
    if not portal_id:
        print("ID가 비어있습니다. 종료.")
        return

    portal_pw = getpass.getpass("Portal 비밀번호 (화면에 안 보임): ")
    if not portal_pw:
        print("비밀번호가 비어있습니다. 종료.")
        return

    # 오타 방지: 한 번 더 입력받아서 비교
    portal_pw_confirm = getpass.getpass("Portal 비밀번호 확인 (한 번 더): ")
    if portal_pw != portal_pw_confirm:
        print("❌ 두 비밀번호가 다릅니다. 다시 시도하세요.")
        return
    print(f"(비밀번호 길이 {len(portal_pw)}자 확인됨)")

    credentials.set_portal_credentials(portal_id, portal_pw)
    print()
    print("✅ Portal 자격증명 저장 완료.")
    print()

    # 추가: 구글시트 webhook URL
    print("─" * 50)
    print("구글시트 Apps Script Webhook URL 저장 (선택사항)")
    print("─" * 50)
    print("Apps Script 배포 후 받은 webhook URL이 있으면 입력하세요.")
    print("나중에 등록해도 됩니다. 건너뛰려면 그냥 엔터.")
    sheet_url = input("Sheet Webhook URL: ").strip()
    if sheet_url:
        credentials.set_sheet_webhook(sheet_url)
        print("✅ Sheet Webhook 저장 완료.")
    else:
        print("(건너뜀)")
    print()

    # 슬랙 webhook URL
    print("─" * 50)
    print("슬랙 Webhook URL 저장 (선택사항)")
    print("─" * 50)
    slack_url = input("Slack Webhook URL: ").strip()
    if slack_url:
        credentials.set_slack_webhook(slack_url)
        print("✅ Slack Webhook 저장 완료.")
    else:
        print("(건너뜀)")


if __name__ == "__main__":
    main()
