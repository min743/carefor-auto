"""노션 차량현황 데이터베이스에서 검사유효기간 수집."""
from __future__ import annotations

import os
import re
import requests

NOTION_VERSION = "2022-06-28"
DATABASE_ID = "ede28a5b-34b0-408f-ad49-35e822812521"
DATE_RANGE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})")


def _get_token() -> str:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        from . import credentials
        token = credentials.get("notion_token")
    if not token:
        raise RuntimeError("NOTION_TOKEN이 없습니다.")
    return token


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _fetch_comment_dates(page_id: str, headers: dict) -> tuple[str, str]:
    """페이지 댓글에서 '자동차검사 가능기간 : YYYY-MM-DD ~ YYYY-MM-DD' 파싱."""
    try:
        resp = requests.get(
            f"https://api.notion.com/v1/comments?block_id={page_id}",
            headers=headers,
            timeout=10,
        )
        for c in resp.json().get("results", []):
            text = "".join(t.get("plain_text", "") for t in c.get("rich_text", []))
            if "가능기간" in text:
                m = DATE_RANGE_RE.search(text)
                if m:
                    return m.group(1), m.group(2)
    except Exception:
        pass
    return "", ""


def fetch_inspect_dates() -> dict[str, dict]:
    """
    차량번호 → {inspect_start, inspect_end} 딕셔너리 반환.
    댓글에 가능기간이 있으면 댓글 기준, 없으면 노션 필드(만료일)만 사용.
    """
    token = _get_token()
    headers = _headers(token)
    result: dict[str, dict] = {}
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

            title_list = props.get("차량번호", {}).get("title", [])
            car_no = title_list[0].get("plain_text", "").strip() if title_list else ""
            if not car_no:
                continue

            # 노션 필드 만료일
            date_obj = props.get("검사유효기간", {}).get("date") or {}
            notion_end = date_obj.get("start", "")

            # 댓글에서 시작~종료 파싱
            c_start, c_end = _fetch_comment_dates(page["id"], headers)

            result[car_no] = {
                "inspect_start": c_start,
                "inspect_end": c_end or notion_end,
            }

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return result
