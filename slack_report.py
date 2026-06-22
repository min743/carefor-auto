"""
케어포 → 슬랙 자동 보고 (구글시트 없음).
윈도우 작업 스케줄러에서 매일 오전 11시 실행.

실행:
  .venv\Scripts\pythonw.exe slack_report.py        # 창 없이 백그라운드
  .venv\Scripts\python.exe  slack_report.py        # 콘솔 확인용
"""
from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import Config, config_path, log_dir
from src.main import run_slack_only


def _setup_logging() -> None:
    log_file = log_dir() / "slack_report.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8")],
    )


def main() -> None:
    _setup_logging()
    log = logging.getLogger("slack_report")
    log.info("=== 슬랙 자동 보고 시작 ===")

    cfg_path = config_path()
    if not cfg_path.exists():
        log.error("config.yaml 없음: %s", cfg_path)
        sys.exit(1)

    config = Config.load(cfg_path)

    def on_progress(p):
        if p.status == "running":
            log.info("[%s] 수집 중...", p.name)
        elif p.status == "success":
            log.info("[%s] 완료 — 현원 %s / 결석 %s / 출석 %s",
                     p.name, p.hyeon_won, p.gyeol_seok, p.chul_seok)
        elif p.status == "error":
            log.error("[%s] 실패: %s", p.name, p.message)

    result = run_slack_only(config, target_date=date.today(), progress_callback=on_progress)

    if result.get("saved_image_path"):
        log.info("이미지 저장: %s", result["saved_image_path"])
    if result["sent_slack"]:
        log.info("슬랙 전송 완료")
    for err in result.get("errors", []):
        log.error("오류: %s", err)

    log.info("=== 종료 (ok=%s) ===", result["ok"])
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
