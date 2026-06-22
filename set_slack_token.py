"""
Slack Bot Token 저장 스크립트.

Slack App 설정에서 Bot Token(xoxb-...)을 발급받아 여기에 입력.
필요한 Bot Token Scopes: files:write, channels:read (또는 groups:read)

실행:
  .venv\Scripts\python.exe set_slack_token.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.credentials import get_slack_bot_token, set_slack_bot_token

current = get_slack_bot_token()
if current:
    print(f"현재 저장된 토큰: {current[:20]}...")
else:
    print("저장된 Bot Token 없음")

token = input("Bot Token 입력 (xoxb-...): ").strip()
if not token.startswith("xoxb-"):
    print("오류: Bot Token은 xoxb- 로 시작해야 합니다")
    sys.exit(1)

set_slack_bot_token(token)
print("저장 완료.")
