"""
GitHub Actions / 서버 환경에서 실행되는 headless 스크립트.
환경변수에서 자격증명을 읽어 케어포 → 슬랙 전송.
"""
import os
import sys
from pathlib import Path
from datetime import date

# ── 환경변수 → credentials 패치 (keyring 우회) ──────────────────────────
import src.credentials as _creds

_env_map = {
    _creds.KEY_PORTAL_ID:       os.environ.get("CAREFOR_ID"),
    _creds.KEY_PORTAL_PASSWORD: os.environ.get("CAREFOR_PW"),
    _creds.KEY_SLACK_BOT_TOKEN: os.environ.get("SLACK_BOT_TOKEN"),
    _creds.KEY_SLACK_WEBHOOK:   None,  # 텍스트 웹훅 비활성화 (이미지만 전송)
}

_original_get = _creds.get

def _patched_get(key: str) -> str | None:
    if key in _env_map and _env_map[key]:
        return _env_map[key]
    return _original_get(key)

_creds.get = _patched_get

# ── config.yaml 준비 ─────────────────────────────────────────────────────
CONFIG_YAML = os.environ.get("CONFIG_YAML")
if CONFIG_YAML:
    cfg_path = Path("/tmp/config.yaml")
    cfg_path.write_text(CONFIG_YAML, encoding="utf-8")
else:
    cfg_path = Path(__file__).parent / "config.yaml"

if not cfg_path.exists():
    print("ERROR: config.yaml이 없습니다. CONFIG_YAML 환경변수를 설정하세요.")
    sys.exit(1)

# ── 실행 ─────────────────────────────────────────────────────────────────
from src.config import Config
from src.main import run_slack_only

DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

cfg = Config.load(cfg_path)
result = run_slack_only(cfg, target_date=date.today(), dry_run=DRY_RUN)

print("\n=== 메시지 미리보기 ===")
print(result.get("slack_message", ""))
print("=" * 40)

if DRY_RUN:
    print("DRY_RUN 모드: 슬랙 전송 건너뜀")
else:
    print("=== 결과 ===")
    print(f"슬랙 이미지: {'✅' if result.get('sent_image') else '❌'}")
    print(f"슬랙 텍스트: {'✅' if result.get('sent_slack') else '❌'}")
    if result.get("errors"):
        print("오류:", result["errors"])
        sys.exit(1)
