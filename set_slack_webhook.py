"""슬랙 Webhook URL 저장."""
from src import credentials


def main():
    print("Slack Incoming Webhook URL 붙여넣기:")
    url = input("URL: ").strip()
    if not url.startswith("https://hooks.slack.com/"):
        print("⚠️ Slack Webhook URL 형식이 아닙니다.")
        return
    credentials.set_slack_webhook(url)
    print("✅ 저장 완료.")


if __name__ == "__main__":
    main()
