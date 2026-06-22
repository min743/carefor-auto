"""
구글시트 Apps Script webhook URL만 저장하는 간이 스크립트.

실행: .venv\\Scripts\\python.exe set_sheet_webhook.py
"""
from src import credentials


def main():
    print("Apps Script 배포 화면에서 받은 웹앱 URL 붙여넣기:")
    url = input("URL: ").strip()
    if not url.startswith("https://script.google.com/"):
        print("⚠️ Apps Script URL 형식이 아닙니다. 다시 확인해주세요.")
        return
    credentials.set_sheet_webhook(url)
    print("✅ 저장 완료.")


if __name__ == "__main__":
    main()
