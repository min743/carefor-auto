"""GitHub Actions 에서 34② 결과평가 팝업을 수집하는 headless 래퍼.

`audit.collect_result_eval` 은 로컬 전제(keyring 자격증명 + config_path() 의 config.yaml)라
CI 러너에서 그대로 못 돈다. run_audit_headless.py 와 같은 방식으로
환경변수(Secrets) → credentials 패치 + CONFIG_YAML 파일화를 먼저 해준다.

수집물(audit_results/결과평가_<지점>/)은 수급자명·총평이 있는 개인정보 →
러너 안에서만 쓰이고 저장소에 커밋되지 않는다([[pii-commit-guard]], .gitignore).

사용: python collect_result_eval_headless.py [지점...] [--limit N]
      인자 없으면 4지점 전체.
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
}
_original_get = _creds.get


def _patched_get(key: str) -> str | None:
    # env 에 값이 있으면 그것, 없으면 원래 경로(로컬 keyring) — 로컬 테스트도 그대로 동작
    v = _env_map.get(key)
    return v if v else _original_get(key)


_creds.get = _patched_get

# ── config.yaml 준비 ────────────────────────────────────────────────────
# collect_result_eval 은 Config.load(config_path()) 를 쓰므로 그 경로에 써야 한다
# (run_audit_headless 는 /tmp/config.yaml 에 쓰고 경로를 직접 넘기는 방식이라 서로 다름).
from src.config import config_path  # noqa: E402

_cfg_yaml = os.environ.get("CONFIG_YAML")
if _cfg_yaml:
    _p = config_path()
    _p.parent.mkdir(parents=True, exist_ok=True)
    _p.write_text(_cfg_yaml, encoding="utf-8")

if not config_path().exists():
    print("ERROR: config.yaml 이 없습니다 (CONFIG_YAML 환경변수 미설정).", flush=True)
    sys.exit(1)

# ── 지점별 수집 ─────────────────────────────────────────────────────────
from audit import collect_result_eval as cre  # noqa: E402

_args = sys.argv[1:]
_branches = [a for a in _args if not a.startswith("-")] or ["청주", "둔산", "서구", "천안"]
_extra = [a for a in _args if a.startswith("-")]
# --limit 는 값이 뒤에 오므로 함께 넘김
if "--limit" in _args:
    i = _args.index("--limit")
    if i + 1 < len(_args):
        _extra = ["--limit", _args[i + 1]]
        _branches = [a for a in _branches if a != _args[i + 1]]

_failed = []
for _b in _branches:
    print(f"\n── 결과평가 수집: {_b}", flush=True)
    sys.argv = ["collect_result_eval", _b] + _extra
    try:
        cre.main()
    except SystemExit as e:
        if e.code:
            _failed.append(_b)
    except Exception as e:
        print(f"  {_b} 수집 실패: {e}", flush=True)
        _failed.append(_b)

if _failed:
    print(f"\n⚠️ 수집 실패: {_failed} — 해당 지점 34②는 판정에서 스킵됩니다(점검은 계속).", flush=True)
else:
    print("\n✅ 결과평가 수집 완료 (34② 판정 가능)", flush=True)
