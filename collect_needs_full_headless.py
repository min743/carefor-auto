"""GitHub Actions 에서 20① 욕구사정 상세 폼을 수집하는 headless 래퍼.

`audit.collect_needs_full` 은 로컬 전제(keyring 자격증명 + config_path() 의 config.yaml)라
CI 러너에서 그대로 못 돈다. collect_result_eval_headless.py 와 같은 방식으로
환경변수(Secrets) → credentials 패치 + CONFIG_YAML 파일화를 먼저 해준다.

수집물(audit_results/needs_full_<지점>.json)은 수급자명·총평·판단근거가 있는 개인정보 →
러너 안에서만 쓰이고 저장소에 커밋되지 않는다([[pii-commit-guard]], .gitignore).

★ 이 스크립트는 import 만 해도 케어포를 긁는다(수집이 모듈 최상단에서 실행된다).
  테스트 목적으로 import 하지 말 것 — collect_result_eval_headless.py 와 동일한 구조다.

★ cutoff: 안 넘기면 audit.collect_needs_full 의 기본값(2024.07.31)을 그대로 쓴다.
  '현 cutoff 유지'(사용자 확정 2026-07-17) — 2024.01.01 재수집은 보류 상태다.
  주의: BRANCH_CUTOFFS(천안 2024.05.31 등)와 다를 수 있고, 수집 시작이 평가기간 시작보다
  늦으면 그 구간이 통째로 비는데, 그건 item20.judge() 가 detail 에 '※수집 시작 … 미수집'
  으로 드러낸다(조용히 넘어가지 않는다).

사용: python collect_needs_full_headless.py [지점...] [--cutoff YYYY.MM.DD]
      인자 없으면 4지점 전체.
"""
import os
import sys

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
# collect_needs_full 은 Config.load(config_path()) 를 쓰므로 그 경로에 써야 한다.
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
from audit import collect_needs_full as cnf  # noqa: E402

_args = sys.argv[1:]
_cutoff = None
if "--cutoff" in _args:
    i = _args.index("--cutoff")
    if i + 1 < len(_args):
        _cutoff = _args[i + 1]
    _args = _args[:i] + _args[i + 2:]
_branches = [a for a in _args if not a.startswith("-")] or ["청주", "둔산", "서구", "천안"]

_failed = []
for _b in _branches:
    print(f"\n── 욕구사정 수집: {_b}", flush=True)
    # main() 은 argv[1]=지점키, argv[2]=cutoff 를 읽는다. cutoff 를 안 주면 모듈 기본값 사용.
    sys.argv = ["collect_needs_full", _b] + ([_cutoff] if _cutoff else [])
    try:
        cnf.main()
    except SystemExit as e:
        if e.code:
            _failed.append(_b)
    except Exception as e:
        print(f"  {_b} 수집 실패: {e}", flush=True)
        _failed.append(_b)

if _failed:
    print(f"\n⚠️ 수집 실패: {_failed} — 해당 지점 20①은 '주의(미수집)'로 표시됩니다(점검은 계속).", flush=True)
else:
    print("\n✅ 욕구사정 수집 완료 (20① 판정 가능)", flush=True)
