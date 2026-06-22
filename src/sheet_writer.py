"""
구글시트에 일일 출석 데이터 전송 (Apps Script webhook 방식).

Apps Script가 구글시트 안에서 실행되므로 인증 키 파일 불필요.
필요한 건 webhook URL 하나 — Windows 자격증명 관리자에 저장.
"""
from __future__ import annotations

from datetime import date

import requests


def post_daily_rows(
    webhook_url: str,
    target_date: date,
    branches_data: list[dict],
    timeout: int = 30,
) -> dict:
    """
    구글시트 Apps Script webhook으로 출석 데이터 POST.

    branches_data 예시:
      [
        {"name": "둔산점", "hyeon_won": 73, "gyeol_seok": 0, "chul_seok": 63, "capacity": 76},
        ...
      ]

    반환: {"ok": True, "inserted": N, "updated": M, "date": "YYYY-MM-DD"}
    """
    payload = {
        "date": target_date.isoformat(),
        "branches": branches_data,
    }
    res = requests.post(webhook_url, json=payload, timeout=timeout)
    res.raise_for_status()
    result = res.json()
    if not result.get("ok"):
        raise RuntimeError(f"시트 webhook 오류: {result.get('error', '알 수 없음')}")
    return result
