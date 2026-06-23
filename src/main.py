"""
오케스트레이터: 케어포 데이터 수집 → 구글시트 입력 → 슬랙 전송.

GUI/CLI에서 호출. 진행 상황은 progress_callback으로 보고.
"""
from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from . import credentials, sheet_writer, slack_notifier
from .carefor_client import fetch_branch_attendance
from .config import Config
from .image_report import generate_image


logger = logging.getLogger(__name__)


@dataclass
class BranchProgress:
    name: str
    status: str  # "pending" | "running" | "success" | "error"
    message: str = ""
    hyeon_won: int | None = None
    gyeol_seok: int | None = None
    chul_seok: int | None = None


ProgressCallback = Callable[[BranchProgress], None]


def run_slack_only(
    config: Config,
    target_date: date | None = None,
    progress_callback: ProgressCallback | None = None,
    dry_run: bool = False,
) -> dict:
    """
    케어포 데이터 수집 → 슬랙 전송만. 구글시트 입력 없음.
    스케줄러/headless 환경에서 호출.
    """
    target_date = target_date or date.today()
    cb = progress_callback or (lambda p: None)

    branches_data: list[dict] = []
    errors: list[str] = []

    for branch in config.branches:
        cb(BranchProgress(name=branch.name, status="running"))
        try:
            att = fetch_branch_attendance(branch.ctmnumb, branch.name, target_date=target_date, headless=True)
            row = {
                "name": branch.name,
                "hyeon_won": att.hyeon_won,
                "gyeol_seok": att.gyeol_seok,
                "chul_seok": att.chul_seok,
                "avg_attendees": att.avg_attendees,
                "capacity": branch.capacity,
            }
            branches_data.append(row)
            cb(BranchProgress(
                name=branch.name, status="success",
                hyeon_won=att.hyeon_won, gyeol_seok=att.gyeol_seok, chul_seok=att.chul_seok,
            ))
        except Exception as e:
            logger.exception("Failed for branch %s", branch.name)
            errors.append(f"{branch.name}: {e}")
            cb(BranchProgress(name=branch.name, status="error", message=str(e)))

    if not branches_data:
        return {"ok": False, "errors": errors, "sent_slack": False}

    # 이미지 생성 + 바탕화면 저장 (서버 환경에서는 저장 생략)
    image_bytes: bytes | None = None
    saved_image_path: str | None = None
    try:
        image_bytes = generate_image(target_date, branches_data)
        desktop = Path.home() / "Desktop"
        if desktop.exists():
            filename = f"출석현황_{target_date.strftime('%Y%m%d')}.png"
            img_path = desktop / filename
            img_path.write_bytes(image_bytes)
            saved_image_path = str(img_path)
            logger.info("이미지 저장: %s", saved_image_path)
    except Exception as e:
        logger.exception("이미지 생성 실패")
        errors.append(f"image: {e}")

    # 슬랙 이미지 전송 (Bot Token)
    sent_image = False
    if image_bytes and not dry_run:
        bot_token = credentials.get_slack_bot_token()
        if bot_token:
            try:
                slack_notifier.send_image_via_api(bot_token, config.slack_channel_name, image_bytes)
                sent_image = True
            except Exception as e:
                logger.exception("Slack 이미지 전송 실패")
                errors.append(f"slack image: {e}")

    # 슬랙 텍스트 보고 (webhook)
    slack_message = slack_notifier.build_message(target_date, branches_data)
    sent_slack = False
    if not dry_run:
        webhook = credentials.get_slack_webhook()
        if webhook:
            try:
                slack_notifier.send_via_webhook(webhook, slack_message)
                sent_slack = True
            except Exception as e:
                logger.exception("Slack webhook send failed")
                errors.append(f"slack: {e}")
        else:
            errors.append("slack: webhook URL이 저장되어 있지 않습니다")

    return {
        "ok": not errors,
        "errors": errors,
        "sent_slack": sent_slack,
        "sent_image": sent_image,
        "slack_message": slack_message,
        "saved_image_path": saved_image_path,
        "branches_data": branches_data,
    }


def run_daily_report(
    config: Config,
    target_date: date | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict:
    """
    오늘(또는 지정 날짜) 출석 데이터 수집 → 시트 입력 → 슬랙 전송.
    반환: 결과 요약 dict
    """
    target_date = target_date or date.today()
    cb = progress_callback or (lambda p: None)

    branches_data: list[dict] = []
    errors: list[str] = []

    # 1) 지점별 데이터 수집
    for branch in config.branches:
        cb(BranchProgress(name=branch.name, status="running"))
        try:
            att = fetch_branch_attendance(branch.ctmnumb, branch.name, headless=True)
            row = {
                "name": branch.name,
                "hyeon_won": att.hyeon_won,
                "gyeol_seok": att.gyeol_seok,
                "chul_seok": att.chul_seok,
                "avg_attendees": att.avg_attendees,
                "capacity": branch.capacity,
            }
            branches_data.append(row)
            cb(BranchProgress(
                name=branch.name, status="success",
                hyeon_won=att.hyeon_won, gyeol_seok=att.gyeol_seok, chul_seok=att.chul_seok,
            ))
        except Exception as e:
            logger.exception("Failed for branch %s", branch.name)
            errors.append(f"{branch.name}: {e}")
            cb(BranchProgress(name=branch.name, status="error", message=str(e)))

    if not branches_data:
        return {"ok": False, "errors": errors, "wrote_sheet": False, "sent_slack": False, "sent_image": False}

    # 2) 구글시트(Apps Script webhook) 입력
    wrote_sheet = False
    webhook = credentials.get_sheet_webhook()
    if not webhook:
        errors.append("sheet: webhook URL이 자격증명에 저장되어 있지 않음")
    else:
        try:
            sheet_writer.post_daily_rows(
                webhook_url=webhook,
                target_date=target_date,
                branches_data=branches_data,
            )
            wrote_sheet = True
        except Exception as e:
            logger.exception("Sheet write failed")
            errors.append(f"sheet: {e}")

    # 3) 이미지 생성 + 슬랙 이미지 전송
    image_bytes: bytes | None = None
    saved_image_path: str | None = None
    sent_image = False
    try:
        image_bytes = generate_image(target_date, branches_data)
        desktop = Path.home() / "Desktop"
        filename = f"출석현황_{target_date.strftime('%Y%m%d')}.png"
        img_path = desktop / filename
        img_path.write_bytes(image_bytes)
        saved_image_path = str(img_path)
        logger.info("이미지 저장: %s", saved_image_path)
    except Exception as e:
        logger.exception("이미지 생성 실패")
        errors.append(f"image: {e}")

    if image_bytes and config.slack_enabled:
        bot_token = credentials.get_slack_bot_token()
        if bot_token:
            try:
                slack_notifier.send_image_via_api(bot_token, config.slack_channel_name, image_bytes)
                sent_image = True
            except Exception as e:
                logger.exception("Slack 이미지 전송 실패")
                errors.append(f"slack image: {e}")

    # 4) 슬랙 텍스트: webhook 있으면 자동 전송, 없으면 클립보드 복사
    sent_slack = False
    copied_clipboard = False
    slack_message = slack_notifier.build_message(target_date, branches_data)

    if config.slack_enabled:
        webhook = credentials.get_slack_webhook()
        if webhook:
            try:
                slack_notifier.send_via_webhook(webhook, slack_message)
                sent_slack = True
            except Exception as e:
                logger.exception("Slack webhook send failed — falling back to clipboard")
                errors.append(f"slack webhook: {e}")

        if not sent_slack:
            try:
                slack_notifier.copy_to_clipboard(slack_message)
                copied_clipboard = True
            except Exception as e:
                logger.exception("Clipboard copy failed")
                errors.append(f"clipboard: {e}")

    return {
        "ok": not errors,
        "errors": errors,
        "wrote_sheet": wrote_sheet,
        "sent_slack": sent_slack,
        "sent_image": sent_image,
        "copied_clipboard": copied_clipboard,
        "slack_message": slack_message,
        "saved_image_path": saved_image_path,
        "branches_data": branches_data,
    }
