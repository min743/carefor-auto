"""노션 차량현황 데이터베이스에서 검사유효기간 수집."""
from __future__ import annotations

import os
import requests

NOTION_VERSION = "2022-06-28"
DATABASE_ID = "ede28a5b-34b0-408f-ad49-35e822812521"


def _get_token() -> str:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        from . import credentials
        token = credentials.get("notion_token")
    if not token:
        raise RuntimeError("NOTION_TOKEN이 없습니다.")
    return token


def fetch_inspect_dates() -> dict[str, str]:
    """차량번호 → 검사유효기간(YYYY-MM-DD) 딕셔너리 반환."""
    token = _get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    result: dict[str, str] = {}
    cursor = None

    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor

        resp = requests.post(
            f"https://api.notion.com/v1/databases/{DATABASE_ID}/query",
            headers=headers,
            json=body,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        for page in data.get("results", []):
            props = page["properties"]

            # 차량번호 (title)
            title_list = props.get("차량번호", {}).get("title", [])
            car_no = title_list[0].get("plain_text", "").strip() if title_list else ""
            if not car_no:
                continue

            # 검사유효기간 (date)
            date_obj = props.get("검사유효기간", {}).get("date")
            if date_obj and date_obj.get("start"):
                result[car_no] = date_obj["start"]

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return result
