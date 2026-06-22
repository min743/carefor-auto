"""
슬랙 메시지 생성 + 전송/클립보드 복사.

운영 방식:
- webhook URL이 저장돼있으면: 자동 전송
- 없으면: 메시지 텍스트를 Windows 클립보드에 복사 (수동 붙여넣기용)
"""
from __future__ import annotations

import io
from datetime import date

import pyperclip
import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def _display_width(s: str) -> int:
    """한글/CJK는 2칸, 나머지는 1칸으로 계산."""
    w = 0
    for ch in s:
        cp = ord(ch)
        if (0xAC00 <= cp <= 0xD7FF or  # 한글 완성형
                0x1100 <= cp <= 0x11FF or  # 한글 자모
                0x4E00 <= cp <= 0x9FFF or  # CJK 통합한자
                0x3000 <= cp <= 0x303F or  # CJK 기호·구두점
                0xFF01 <= cp <= 0xFF60 or  # 전각 ASCII
                0xFFE0 <= cp <= 0xFFE6):   # 전각 기호
            w += 2
        else:
            w += 1
    return w


def _rpad(s: str, width: int) -> str:
    """display_width 기준 우측 공백 패딩 (왼쪽 정렬)."""
    return s + " " * max(0, width - _display_width(s))


def _lpad(s: str, width: int) -> str:
    """display_width 기준 좌측 공백 패딩 (오른쪽 정렬)."""
    return " " * max(0, width - _display_width(s)) + s


def build_message(target_date: date, branches_data: list[dict]) -> str:
    """
    branches_data 예시:
      [{"name": "둔산점", "hyeon_won": 73, "gyeol_seok": 0, "chul_seok": 62, "capacity": 80}, ...]

    출력 형식 (코드블록 표):
      📊 충청본부 주간보호 출석 현황 2026.06.22(월)

      ```
                    둔산점  서구점  천안점  청주오창점
      현원(수급중)      73      45      38          52
      결석               0       2       1           0
      출석              62      40      35          50
      ```
    """
    weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][target_date.weekday()]
    title = f"📊 충청본부 주간보호 출석 현황 {target_date.strftime('%Y.%m.%d')}({weekday_kr})"

    LABEL_W = 14   # "현원(수급중)" = 12 display + 여백 2
    COL_PAD = 2    # 열 사이 최소 여백

    names = [b["name"] for b in branches_data]
    col_ws = [max(_display_width(n), 3) + COL_PAD for n in names]

    header = _rpad("", LABEL_W) + "".join(
        _lpad(n, w) for n, w in zip(names, col_ws)
    )
    sep = "─" * (LABEL_W + sum(col_ws))

    metric_rows = [
        ("현원(수급중)", "hyeon_won"),
        ("결석",         "gyeol_seok"),
        ("출석",         "chul_seok"),
    ]
    data_rows = [
        _rpad(label, LABEL_W) + "".join(
            _lpad(str(b[key]), w) for b, w in zip(branches_data, col_ws)
        )
        for label, key in metric_rows
    ]

    table = "\n".join([header, sep] + data_rows)
    return f"{title}\n\n```\n{table}\n```"


def send_via_webhook(webhook_url: str, text: str) -> None:
    res = requests.post(webhook_url, json={"text": text}, timeout=15)
    res.raise_for_status()


def _resolve_channel_id(client: WebClient, channel: str) -> str:
    """채널 이름을 채널 ID로 변환. 이미 ID 형식이면 그대로 반환."""
    if len(channel) >= 9 and channel[0] in ("C", "G", "D", "Z"):
        return channel
    name = channel.lstrip("#")
    cursor = None
    while True:
        kwargs = {"types": "public_channel,private_channel", "limit": 200}
        if cursor:
            kwargs["cursor"] = cursor
        resp = client.conversations_list(**kwargs)
        for ch in resp["channels"]:
            if ch["name"] == name:
                return ch["id"]
        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            break
    raise ValueError(f"채널을 찾을 수 없습니다: {channel}")


def send_image_via_api(
    bot_token: str,
    channel: str,
    image_bytes: bytes,
    title: str = "지점별 출석 현황",
) -> None:
    """Slack Token으로 이미지를 채널에 파일로 업로드."""
    client = WebClient(token=bot_token)
    channel_id = _resolve_channel_id(client, channel)
    client.files_upload_v2(
        channel=channel_id,
        file=io.BytesIO(image_bytes),
        filename="attendance.png",
        title=title,
    )


def copy_to_clipboard(text: str) -> None:
    pyperclip.copy(text)
