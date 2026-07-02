"""지점관리 점검 실행기.

사용법:
  py run_audit.py                      # 4개 지점 전체 순차 점검
  py run_audit.py --branch 천안점       # 한 지점만
  py run_audit.py --branch 천안점 --limit 5 --headed   # 5명만, 브라우저 보이게 (테스트)
  py run_audit.py --cutoff 2024.05.31  # 점검 시작일 지정 (기본: 지점별 기본값)

완료 후 audit_dashboard.html 을 열면 결과가 표시됩니다.
"""
from __future__ import annotations

import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8")

from src.config import Config, config_path
from audit.collector import run_branch_audit
from audit.items import BRANCH_CUTOFFS


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", help="지점명 (미지정 시 전체)")
    ap.add_argument("--cutoff", help="점검 시작일 YYYY.MM.DD (미지정 시 지점별 기본값)")
    ap.add_argument("--limit", type=int, default=0, help="테스트용: 앞에서 N명만 스캔")
    ap.add_argument("--headed", action="store_true", help="브라우저 창 표시")
    args = ap.parse_args()

    cfg = Config.load(config_path())
    branches = cfg.branches
    if args.branch:
        branches = [b for b in branches if args.branch in b.name]
        if not branches:
            print(f"지점 '{args.branch}' 을 config 에서 찾을 수 없습니다.")
            return

    for b in branches:
        cutoff = args.cutoff or BRANCH_CUTOFFS.get(b.name, "2024.01.01")
        print(f"\n===== {b.name} 점검 시작 (기준일 {cutoff}) =====")
        try:
            out = run_branch_audit(
                ctmnumb=b.ctmnumb,
                branch_name=b.name,
                cutoff=cutoff,
                limit=args.limit,
                headless=not args.headed,
            )
            ir = out["item_results"]
            print(f"----- {b.name} 결과 -----")
            for no in ("20", "21", "22"):
                print(f"  항목 {no}: {ir[no]['status']} — {ir[no]['detail']}")
            st = out["analysis"]["stats"] if "stats" in out.get("analysis", {}) else None
            if st:
                print(f"  낙상↔욕구 대조: {st['total_rounds']}회차 중 불일치 {st['disc']}건")
        except Exception as e:
            print(f"[{b.name}] 실패: {e}")

    # 구글시트 '지점점검' 탭 업로드 (본부 공유)
    try:
        from audit.sheet_upload import upload
        upload()
    except Exception as e:
        print(f"구글시트 업로드 건너뜀: {e}")

    print("\n완료. audit_dashboard.html 을 열어 확인하세요.")


if __name__ == "__main__":
    main()
