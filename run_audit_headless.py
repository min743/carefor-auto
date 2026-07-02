"""GitHub Actions 에서 실행되는 지점점검 headless 스크립트.

환경변수(Secrets)에서 자격증명을 읽어 4개 지점 점검 → 구글시트 업로드
→ 개인정보 없는 요약페이지(docs/audit_summary.html) 재생성.
개인정보가 포함된 audit_results/*.json 은 러너에서만 존재하고 저장소에 커밋되지 않는다.
"""
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# ── 환경변수 → credentials 패치 (keyring 우회) ──────────────────────────
import src.credentials as _creds

_env_map = {
    _creds.KEY_PORTAL_ID:       os.environ.get("CAREFOR_ID"),
    _creds.KEY_PORTAL_PASSWORD: os.environ.get("CAREFOR_PW"),
    _creds.KEY_AUDIT_WEBHOOK:   os.environ.get("AUDIT_WEBHOOK_URL"),
}

_original_get = _creds.get


def _patched_get(key: str) -> str | None:
    if key in _env_map:
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
from audit.collector import run_branch_audit
from audit.items import BRANCH_CUTOFFS

cfg = Config.load(cfg_path)
limit = int(os.environ.get("AUDIT_LIMIT", "0"))  # 테스트용: 지점당 N명 제한

failed = []
for b in cfg.branches:
    cutoff = BRANCH_CUTOFFS.get(b.name, "2024.01.01")
    print(f"\n===== {b.name} 점검 시작 (기준일 {cutoff}) =====", flush=True)
    try:
        out = run_branch_audit(
            ctmnumb=b.ctmnumb,
            branch_name=b.name,
            cutoff=cutoff,
            limit=limit,
            headless=True,
            progress_cb=lambda m: print(m, flush=True),
        )
        ir = out["item_results"]
        for no in ("20", "21", "22"):
            print(f"  항목 {no}: {ir[no]['status']} — {ir[no]['detail']}")
    except Exception as e:
        print(f"[{b.name}] 실패: {e}")
        failed.append(b.name)

# ── 구글시트 업로드 ───────────────────────────────────────────────────────
try:
    from audit.sheet_upload import upload
    upload()
except Exception as e:
    print(f"구글시트 업로드 실패: {e}")
    failed.append("시트업로드")

if failed:
    print(f"\n일부 실패: {failed}")
    sys.exit(1)
print("\n전체 점검 완료")
