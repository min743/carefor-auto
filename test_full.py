"""
풀 파이프라인 테스트 (실제 운영과 동일한 흐름):
케어포 4개 지점 스크래핑 → 구글시트 → 슬랙 메시지(클립보드 복사).
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from datetime import date
from pathlib import Path

from src.config import Config, config_path
from src.main import BranchProgress, run_daily_report


TEST_DATE = date(2026, 6, 19)  # 금요일 (주말은 데이터 없음)


def on_progress(p: BranchProgress):
    icon = {"running": "⏳", "success": "✅", "error": "❌"}.get(p.status, "·")
    line = f"{icon} {p.name}"
    if p.status == "success":
        line += f"  현원 {p.hyeon_won} / 결석 {p.gyeol_seok} / 출석 {p.chul_seok}"
    elif p.status == "error":
        line += f"  실패: {p.message[:60]}"
    print(line)


def main():
    # config.yaml 자동 생성
    cfg_path = config_path()
    if not cfg_path.exists():
        example = Path(__file__).resolve().parent / "config.example.yaml"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    config = Config.load(cfg_path)

    print(f"테스트 날짜: {TEST_DATE}")
    print()
    result = run_daily_report(config, target_date=TEST_DATE, progress_callback=on_progress)

    print()
    print("=" * 50)
    print(f"시트 입력:  {'✅' if result['wrote_sheet'] else '❌'}")
    if result.get('sent_slack'):
        print("슬랙:       ✅ webhook 자동 전송")
    elif result.get('copied_clipboard'):
        print("슬랙:       📋 클립보드에 메시지 복사됨 (Ctrl+V로 붙여넣기)")
    else:
        print("슬랙:       ⏭ 미실행")
    if result.get("errors"):
        print()
        print("알림:")
        for e in result["errors"]:
            print(f"  - {e}")
    print()
    print("─" * 50)
    print("생성된 슬랙 메시지 (클립보드에 복사됨):")
    print("─" * 50)
    print(result.get("slack_message", ""))


if __name__ == "__main__":
    main()
