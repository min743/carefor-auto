# -*- coding: utf-8 -*-
"""노션 생일쿠폰 발송 내역(위펀 거래명세서) → 월별·지점별 생일자 명단.

- 토큰: NOTION_TOKEN 환경변수(클라우드) 또는 keyring notion_token(로컬 선택).
  없으면 조용히 건너뜀 (클라우드 전용 기능).
- 노션 전월 자료는 매월 8일 이후 업로드 → N월 대조는 (N+1)월 8일 이후에만 판정.
- 파일: CC_(둔산·서구) / CCC_(천안·청주오창) 위펀_전체_거래명세서_YYYY년MM월.xlsx
"""
from __future__ import annotations

import io
import os
import re
from datetime import date

PAGE_ID = "aaf9857c2dab4e88be4fddc595d8ccd5"  # 생일쿠폰 발송 내역 페이지
NOTION_VERSION = "2022-06-28"
FILE_RE = re.compile(r"(CCC?|ccc?)_.*거래명세서.*?(\d{4})년\s*(\d{1,2})월.*\.xlsx", re.I)

_cache: dict | None = None


def _get_token() -> str | None:
    token = os.environ.get("NOTION_TOKEN")
    if token:
        return token
    try:
        from src import credentials
        return credentials.get("notion_token")
    except Exception:
        return None


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION}


def _walk_blocks(block_id: str, headers: dict, depth: int = 0, out=None) -> list:
    """블록 트리 순회하며 file 블록 수집."""
    import requests
    if out is None:
        out = []
    if depth > 4:
        return out
    cursor = None
    while True:
        url = f"https://api.notion.com/v1/blocks/{block_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        resp = requests.get(url, headers=headers, timeout=30)
        data = resp.json()
        for blk in data.get("results", []):
            t = blk.get("type")
            if t == "file":
                f = blk["file"]
                name = f.get("name") or "".join(x.get("plain_text", "") for x in blk["file"].get("caption", []))
                url_ = (f.get("file") or f.get("external") or {}).get("url", "")
                out.append({"name": name, "url": url_})
            elif t == "child_database":
                _walk_database(blk["id"], headers, out)
            elif blk.get("has_children"):
                _walk_blocks(blk["id"], headers, depth + 1, out)
        cursor = data.get("next_cursor")
        if not cursor:
            break
    return out


def _walk_database(db_id: str, headers: dict, out: list) -> None:
    """데이터베이스 행의 files 속성 + 행 페이지 내부 파일 수집."""
    import requests
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        resp = requests.post(f"https://api.notion.com/v1/databases/{db_id}/query",
                             headers={**headers, "Content-Type": "application/json"},
                             json=body, timeout=30)
        data = resp.json()
        for page in data.get("results", []):
            for prop in (page.get("properties") or {}).values():
                if prop.get("type") == "files":
                    for f in prop.get("files", []):
                        out.append({"name": f.get("name", ""),
                                    "url": (f.get("file") or f.get("external") or {}).get("url", "")})
            _walk_blocks(page["id"], headers, 3, out)
        cursor = data.get("next_cursor")
        if not cursor:
            break


def _parse_invoice(content: bytes) -> dict:
    """거래명세서 엑셀 → {지점명: [성명...]}"""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(content), read_only=True)
    result: dict[str, list] = {}
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        rows = list(ws.iter_rows(values_only=True))
        hdr_i = next((i for i, r in enumerate(rows)
                      if r and "성명" in [str(c) for c in r if c]), None)
        if hdr_i is None:
            continue
        hdr = [str(c) if c else "" for c in rows[hdr_i]]
        i_branch = next((i for i, h in enumerate(hdr) if "이벤트명" in h), 2)
        i_name = next((i for i, h in enumerate(hdr) if "성명" in h), 3)
        for r in rows[hdr_i + 1:]:
            if not r or r[0] is None:
                continue
            branch = str(r[i_branch] or "").strip()
            name = str(r[i_name] or "").strip()
            if branch and re.match(r"^[가-힣]{2,4}$", name):
                result.setdefault(branch.replace(" ", ""), []).append(name)
    return result


def fetch_birthdays(progress_cb=print) -> dict | None:
    """{'YYYY-MM': {지점명(공백제거): [성명...]}} — 토큰 없으면 None."""
    global _cache
    if _cache is not None:
        return _cache
    token = _get_token()
    if not token:
        return None
    try:
        import requests
        headers = _headers(token)
        files = _walk_blocks(PAGE_ID, headers)
        monthly: dict[str, dict] = {}
        n_parsed = 0
        for f in files:
            m = FILE_RE.search(f.get("name") or "")
            if not m or not f.get("url"):
                continue
            ym = f"{m.group(2)}-{int(m.group(3)):02d}"
            try:
                content = requests.get(f["url"], timeout=60).content
                parsed = _parse_invoice(content)
                n_parsed += 1
            except Exception:
                continue
            dst = monthly.setdefault(ym, {})
            for br, names in parsed.items():
                dst.setdefault(br, []).extend(names)
        progress_cb(f"  노션 생일쿠폰: 파일 {len(files)}개 중 명세서 {n_parsed}개 파싱, {len(monthly)}개월")
        if n_parsed == 0 and files:
            # 진단: 패턴 미일치 시 엑셀류 파일명 샘플 출력 (업무 문서명만 — 개인정보 제외 위해 xlsx 한정)
            samples = [f["name"][:60] for f in files if str(f.get("name", "")).lower().endswith(".xlsx")][:10]
            progress_cb(f"  [진단] xlsx 파일명 샘플: {samples}")
        _cache = monthly
        return monthly
    except Exception as e:
        progress_cb(f"  노션 생일쿠폰 조회 실패(건너뜀): {e}")
        return None


def compare(branch_name: str, birthday_log: dict, today: date | None = None,
            progress_cb=print) -> tuple[list, list] | None:
    """노션 생일자 vs 케어포 대장 지급 대조.
    반환: (미지급 의심 ["YYYY-MM 이름", ...], 판정 월 목록) / 토큰 없으면 None"""
    today = today or date.today()
    data = fetch_birthdays(progress_cb)
    if data is None:
        return None
    key = branch_name.replace(" ", "")
    missing, months = [], []
    for ym in sorted(data):
        y, m = int(ym[:4]), int(ym[5:7])
        # N월 자료는 (N+1)월 8일 이후에만 판정
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        if today < date(ny, nm, 8):
            continue
        expected = [n for br, names in data[ym].items() if key in br or br in key for n in names]
        if not expected:
            continue
        months.append(ym)
        given = set(birthday_log.get(ym, []))
        for n in expected:
            if n not in given:
                missing.append(f"{ym} {n}")
    return missing, months
