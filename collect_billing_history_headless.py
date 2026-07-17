"""GitHub Actions 에서 34③ 7-1 청구서 발송이력을 수집하는 headless 래퍼.

`audit.collect_billing_history` 는 로컬 전제(keyring 자격증명 + config_path() 의 config.yaml)라
CI 러너에서 그대로 못 돈다. collect_result_eval_headless.py(34②) 와 같은 방식으로
환경변수(Secrets) → credentials 패치 + CONFIG_YAML 파일화를 먼저 해준다.

수집물(audit_results/청구발송_<지점>.json)은 수급자명이 있는 개인정보 →
러너 안에서만 쓰이고 저장소에 커밋되지 않는다([[pii-commit-guard]], .gitignore).

청구년월 범위: 지점별 BRANCH_CUTOFFS(평가기간 시작)부터 이번 달까지만 돈다.
  item34.judge3() 이 cutoff 이전 청구년월 행을 어차피 버리므로(평가기간 밖) 판정 결과는
  동일하고, 월별 루프(월당 ~5초)라 불필요한 월을 빼면 그만큼 CI 시간이 줄어든다.
  (스크립트 기본값 --from 202407 은 천안점 cutoff 202405 보다 늦어 2개월을 놓친다 →
   cutoff 기준이 더 정확하기도 하다.)

사용: python collect_billing_history_headless.py [지점...] [--from YYYYMM] [--to YYYYMM]
      인자 없으면 4지점 전체, --from 미지정 시 지점별 cutoff.

⚠️ 이 파일은 최상단에서 수집이 실행된다 — import 하면 케어포를 긁는다. 실행으로만 쓸 것.
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
# collect_billing_history 는 Config.load(config_path()) 를 쓰므로 그 경로에 써야 한다.
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
from audit import collect_billing_history as cbh  # noqa: E402
from audit.items import BRANCH_CUTOFFS  # noqa: E402

_args = sys.argv[1:]


def _opt(name: str) -> str | None:
    if name in _args:
        i = _args.index(name)
        if i + 1 < len(_args):
            return _args[i + 1]
    return None


_from = _opt("--from")
_to = _opt("--to")
_opt_vals = {v for v in (_from, _to) if v}
# 옵션 값(202407 등)이 지점명으로 오인되지 않게 제외
_branches = [a for a in _args if not a.startswith("-") and a not in _opt_vals] \
    or ["청주", "둔산", "서구", "천안"]


def _cutoff_from(key: str) -> str:
    """지점 cutoff(YYYY.MM.DD) → 청구년월 시작(YYYYMM). 못 찾으면 스크립트 기본값."""
    c = next((v for k, v in BRANCH_CUTOFFS.items() if key in k), None)
    return c.replace(".", "")[:6] if c else "202407"


_failed = []
for _b in _branches:
    _f = _from or _cutoff_from(_b)
    _extra = ["--from", _f] + (["--to", _to] if _to else [])
    print(f"\n── 청구발송이력 수집: {_b} (청구년월 {_f}~{_to or '이번달'})", flush=True)
    sys.argv = ["collect_billing_history", _b] + _extra
    try:
        cbh.main()
    except SystemExit as e:
        if e.code:
            _failed.append(_b)
    except Exception as e:
        print(f"  {_b} 수집 실패: {e}", flush=True)
        _failed.append(_b)

if _failed:
    print(f"\n⚠️ 수집 실패: {_failed} — 해당 지점 34③은 판정에서 스킵됩니다(점검은 계속).", flush=True)
else:
    print("\n✅ 청구발송이력 수집 완료 (34③ 판정 가능)", flush=True)
