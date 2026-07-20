"""노션 차량현황 데이터베이스에서 검사유효기간 + 보험(보험사·만기) 수집."""
from __future__ import annotations

import os
import re
import requests
from datetime import datetime

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


def _text_any(prop: dict | None) -> str:
    """노션 속성에서 텍스트 추출 — rich_text/select/title 어느 타입이든."""
    if not prop:
        return ""
    if prop.get("rich_text"):
        return "".join(t.get("plain_text", "") for t in prop["rich_text"]).strip()
    if prop.get("select"):
        return (prop.get("select") or {}).get("name", "") or ""
    if prop.get("title"):
        return "".join(t.get("plain_text", "") for t in prop["title"]).strip()
    return ""


def _cert_expiry(names: list[str]) -> tuple[str, str]:
    """증서 파일명에서 만기일 파싱. YYYYMMDD(위치 무관) 또는 YYYY-MM-DD/YYYY.MM.DD 허용.
    여러 날짜가 있으면 가장 늦은 날(갱신 증서의 만기)을 쓴다. (만기 'YYYY-MM-DD', 파일명) 반환."""
    for n in names:
        s = n or ""
        cand = []
        for m in re.finditer(r"(?:19|20)\d{6}", s):                 # 8자리 날짜(어디든)
            try:
                cand.append(datetime.strptime(m.group(0), "%Y%m%d"))
            except ValueError:
                pass
        for m in re.finditer(r"((?:19|20)\d{2})[-.](\d{1,2})[-.](\d{1,2})", s):  # 구분자 날짜
            try:
                cand.append(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))))
            except ValueError:
                pass
        if cand:
            return max(cand).strftime("%Y-%m-%d"), n
    return "", (names[0] if names else "")


def _plate_mismatch(car_no: str, names: list[str]) -> str:
    """증서 파일명에 이 차량과 다른 번호판 끝 4자리가 박혀 있으면 그 파일명 반환(불일치 의심).
    파일명 앞 8자리(날짜)는 제외하고 4자리 그룹만 본다. 판단 불가(파일명에 판번호 없음)는 통과."""
    tails = re.findall(r"\d{4}", car_no)
    tail = tails[-1] if tails else ""
    if not tail:
        return ""
    for n in names:
        rest = re.sub(r"^\s*\d{8}", "", n or "")          # 앞 날짜 제거
        groups = re.findall(r"\d{4}", rest)
        if groups and tail not in groups:
            return n
    return ""


def fetch_insurance() -> tuple[dict[str, dict], list[dict]]:
    """차량번호 → {branch, model, insurer, expiry, cert} + 오류목록 반환.
    보험사(텍스트) + 증서 파일명 앞 8자리(만기)를 읽음. 누락·파싱실패·차량번호 불일치는
    결과에서 빼고 errors 로 모은다(사용자 방침: 상이한 건 제외하고 오류만 보고)."""
    token = _get_token()
    headers = _headers(token)
    result: dict[str, dict] = {}
    errors: list[dict] = []
    cursor = None
    today_str = datetime.now().strftime("%Y-%m-%d")   # 만기 지난 건은 무조건 반영 금지 → 확인 필요로 뺀다

    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        resp = requests.post(
            f"https://api.notion.com/v1/databases/{DATABASE_ID}/query",
            headers=headers, json=body, timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        for page in data.get("results", []):
            props = page["properties"]
            title_list = props.get("차량번호", {}).get("title", [])
            car_no = title_list[0].get("plain_text", "").strip() if title_list else ""
            if not car_no:
                continue
            branch = _text_any(props.get("소속"))
            model = _text_any(props.get("종류"))
            insurer = _text_any(props.get("보험사"))
            files = props.get("보험증서", {}).get("files", []) or []
            names = [f.get("name", "") for f in files]
            expiry, cert = _cert_expiry(names)

            if not insurer and not names:
                errors.append({"car": car_no, "branch": branch, "reason": "보험사·증서 없음"})
                continue
            if not insurer:
                errors.append({"car": car_no, "branch": branch, "reason": "보험사 없음"})
                continue
            if not names:
                errors.append({"car": car_no, "branch": branch, "reason": "증서 파일 없음"})
                continue
            if not expiry:
                errors.append({"car": car_no, "branch": branch, "reason": "증서 파일명에 만기 날짜 없음",
                               "cert": names[0]})
                continue
            bad = _plate_mismatch(car_no, names)
            if bad:
                errors.append({"car": car_no, "branch": branch, "reason": "증서 파일명 차량번호 불일치 의심", "cert": bad})
                continue
            if expiry < today_str:            # 만기 이미 지남 = 증서 갱신 안됐거나 실효 → 확인 필요
                errors.append({"car": car_no, "branch": branch, "reason": "만기일 지남 — 확인 필요",
                               "expiry": expiry, "cert": cert})
                continue

            result[car_no] = {"branch": branch, "model": model,
                              "insurer": insurer, "expiry": expiry, "cert": cert}

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return result, errors
