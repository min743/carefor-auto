"""GitHub Actions 에서 매출점검을 돌리는 headless 래퍼 (본부가 버튼으로 실행).

`매출/revenue_check.py` 는 로컬 전제(keyring 자격증명 + config_path() 의 config.yaml)라
CI 러너에서 그대로 못 돈다. collect_result_eval_headless.py 와 같은 방식으로
환경변수(Secrets) → credentials 패치 + CONFIG_YAML 파일화를 먼저 해준다.

⚠️ 산출물(매출/<지점>/매출점검_*.html, 합본)은 **수급자 실명**이 그대로 들어 있다.
   러너 안에서만 쓰고 저장소에 커밋하지 않는다(.gitignore 로 이중 차단).
   허브에 올릴 때는 deploy_hub_ci 가 이름을 마스킹한다.

사용: python revenue_check_headless.py [지점|전체] [YYYY-MM]
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
# revenue_check 는 Config.load(config_path()) 를 쓰므로 그 경로에 써야 한다.
from src.config import config_path  # noqa: E402

_cfg_yaml = os.environ.get("CONFIG_YAML")
if _cfg_yaml:
    _p = config_path()
    _p.parent.mkdir(parents=True, exist_ok=True)
    _p.write_text(_cfg_yaml, encoding="utf-8")

if not config_path().exists():
    print("ERROR: config.yaml 이 없습니다 (CONFIG_YAML 환경변수 미설정).", flush=True)
    sys.exit(1)

# ── 매출점검 실행 ───────────────────────────────────────────────────────
# 매출/ 는 파이썬 패키지가 아니라 폴더 이름이라 import 가 안 된다 → 경로를 넣고 모듈로 부른다.
_REV = Path(__file__).resolve().parent / "매출"
sys.path.insert(0, str(_REV))

import revenue_check  # noqa: E402

# revenue_check.main() 은 sys.argv 를 읽는다 — 래퍼가 받은 인자를 그대로 넘긴다.
sys.argv = [sys.argv[0]] + sys.argv[1:]
revenue_check.main()

# 합본 경로를 워크플로가 집어갈 수 있게 알려준다(GITHUB_OUTPUT).
_cands = sorted(_REV.glob("매출점검_합본_*.html"))
if _cands:
    out = _cands[-1]
    print(f"합본: {out}", flush=True)
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as f:
            f.write(f"combined={out}\n")
else:
    print("⚠️ 합본 HTML 이 만들어지지 않았습니다.", flush=True)
    sys.exit(1)
