# -*- coding: utf-8 -*-
"""
상담 공지 통합 발송 — 항상 3종 세트로 전송

  ① 신규상담 상담시트 입력 현황 (요약)
  ② 센터별 상담 대기 명단 (아웃콜 차수, 요약)
  ③ 상담 상세 명단 엑셀 → 드라이브 덮어쓰기 업로드 + 링크 공지 (항상 포함)

실행:
  py -X utf8 send_consult_notices.py            # 3종 모두 전송
  py -X utf8 send_consult_notices.py --dry-run  # 전송 없이 생성만
"""
from __future__ import annotations

import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8")

import consult_report
import publish_excel
import waitlist_report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    argv = ["--dry-run"] if args.dry_run else []

    # 한 페이지 통합 공지 하나만 발송 (집계표 + 상담 대기 + 엑셀 링크)
    # ①consult_report.py ②waitlist_report.py 는 개별 실행용으로만 유지
    print("=== 통합 공지 (엑셀 생성·업로드 포함) ===")
    sys.argv = ["publish_excel.py"] + argv
    publish_excel.main()

    print("\n전체 완료")


if __name__ == "__main__":
    main()
