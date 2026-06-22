"""
PySide6 데스크탑 GUI — "오늘 데이터 가져오기" 버튼 한 번 클릭으로 전체 흐름 실행.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTableWidget, QTableWidgetItem, QHeaderView,
    QStatusBar, QMessageBox,
)

from .config import Config, config_path
from .main import BranchProgress, run_daily_report


class Worker(QThread):
    progress = Signal(object)  # BranchProgress
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, config: Config, target_date: date):
        super().__init__()
        self.config = config
        self.target_date = target_date

    def run(self):
        try:
            result = run_daily_report(
                self.config,
                target_date=self.target_date,
                progress_callback=lambda p: self.progress.emit(p),
            )
            self.done.emit(result)
        except Exception as e:
            self.failed.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self.worker: Worker | None = None

        self.setWindowTitle("케어포 → 구글시트 자동 입력기")
        self.resize(640, 520)

        central = QWidget()
        layout = QVBoxLayout(central)

        # 상단: 날짜 표시
        today = date.today()
        weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][today.weekday()]
        self.date_label = QLabel(f"📅 오늘 날짜: {today.strftime('%Y-%m-%d')} ({weekday_kr})")
        self.date_label.setStyleSheet("font-size: 16px; font-weight: bold; padding: 8px;")
        layout.addWidget(self.date_label)

        # 지점 진행 테이블
        self.table = QTableWidget(len(config.branches), 5)
        self.table.setHorizontalHeaderLabels(["지점", "상태", "현원", "결석", "출석"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for i, b in enumerate(config.branches):
            self.table.setItem(i, 0, QTableWidgetItem(b.name))
            self.table.setItem(i, 1, QTableWidgetItem("⏸ 대기"))
            for j in range(2, 5):
                self.table.setItem(i, j, QTableWidgetItem("-"))
        layout.addWidget(self.table)

        # 버튼
        btn_row = QHBoxLayout()
        self.run_button = QPushButton("오늘 데이터 가져오기")
        self.run_button.setStyleSheet(
            "background-color: #0066cc; color: white; padding: 12px; font-size: 14px; font-weight: bold;"
        )
        self.run_button.clicked.connect(self.on_run)
        btn_row.addWidget(self.run_button)
        layout.addLayout(btn_row)

        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("준비됨")

    def on_run(self):
        if self.worker and self.worker.isRunning():
            return
        self.run_button.setEnabled(False)
        self.statusBar().showMessage("케어포 데이터 수집 중...")
        for i in range(self.table.rowCount()):
            self.table.setItem(i, 1, QTableWidgetItem("⏸ 대기"))

        self.worker = Worker(self.config, date.today())
        self.worker.progress.connect(self.on_progress)
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def on_progress(self, p: BranchProgress):
        for i, b in enumerate(self.config.branches):
            if b.name == p.name:
                status_text = {
                    "running": "⏳ 처리 중",
                    "success": "✅ 완료",
                    "error": f"❌ 실패: {p.message[:30]}",
                }.get(p.status, p.status)
                self.table.setItem(i, 1, QTableWidgetItem(status_text))
                if p.status == "success":
                    self.table.setItem(i, 2, QTableWidgetItem(str(p.hyeon_won)))
                    self.table.setItem(i, 3, QTableWidgetItem(str(p.gyeol_seok)))
                    self.table.setItem(i, 4, QTableWidgetItem(str(p.chul_seok)))
                break

    def on_done(self, result: dict):
        self.run_button.setEnabled(True)
        sheet_ok = "✅" if result["wrote_sheet"] else "❌"
        if result.get("sent_slack"):
            slack_status = "✅ 자동 전송"
        elif result.get("copied_clipboard"):
            slack_status = "📋 클립보드 복사됨 — 슬랙에서 Ctrl+V → Enter"
        elif not self.config.slack_enabled:
            slack_status = "⏭ 비활성"
        else:
            slack_status = "❌ 실패"

        image_status = "✅ 이미지 전송됨" if result.get("sent_image") else (
            f"💾 {result['saved_image_path']}" if result.get("saved_image_path") else "❌ 이미지 없음"
        )

        msg = f"시트 입력: {sheet_ok}\n슬랙: {slack_status}\n이미지: {image_status}"
        if result["errors"]:
            msg += f"\n\n알림:\n" + "\n".join(result["errors"])
        QMessageBox.information(self, "완료", msg)
        self.statusBar().showMessage("완료")

    def on_failed(self, error_msg: str):
        self.run_button.setEnabled(True)
        QMessageBox.critical(self, "오류", error_msg)
        self.statusBar().showMessage("실패")


def main():
    cfg_path = config_path()
    if not cfg_path.exists():
        # config.yaml이 없으면 예제 파일을 복사
        example = Path(__file__).resolve().parent.parent / "config.example.yaml"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    config = Config.load(cfg_path)
    app = QApplication(sys.argv)
    win = MainWindow(config)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
