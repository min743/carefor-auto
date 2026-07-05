# -*- coding: utf-8 -*-
"""
상담 공지 엑셀 → 구글 드라이브 업로드 → 슬랙에 다운로드 링크 공지

흐름:
  1. consult_excel.generate() 로 지점별 엑셀 생성
  2. 내 드라이브 '상담공지_엑셀/YYYY-MM-DD' 폴더에 업로드
  3. 링크 공유(보기) 설정 후 슬랙 webhook으로 링크 목록 전송

인증: ~/.clasprc.json 의 구글 OAuth 토큰 (drive.file 권한 — 이 앱이 만든 파일만 접근)

실행:
  py -X utf8 publish_excel.py            # 생성+업로드+슬랙 공지
  py -X utf8 publish_excel.py --dry-run  # 슬랙 전송 없이 링크만 출력
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

try:
    import keyring
except ImportError:
    keyring = None

import consult_excel

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FOLDER_MIME = "application/vnd.google-apps.folder"
RC = os.path.join(os.path.expanduser("~"), ".clasprc.json")
ROOT_FOLDER = "상담공지_엑셀"


# ---------- 구글 OAuth (clasp 토큰 재사용) ----------
def google_token() -> str:
    env = os.environ.get("GOOGLE_OAUTH_JSON")  # GitHub Actions용: .clasprc.json 내용
    store = json.loads(env) if env else json.loads(open(RC, encoding="utf-8").read())
    cred = store["tokens"]["default"]
    if not env and cred.get("expiry_date", 0) > time.time() * 1000 + 60000:
        return cred["access_token"]
    data = urllib.parse.urlencode({
        "client_id": cred["client_id"],
        "client_secret": cred["client_secret"],
        "refresh_token": cred["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    with urllib.request.urlopen(req, timeout=30) as res:
        tok = json.loads(res.read().decode())
    if not env:
        cred["access_token"] = tok["access_token"]
        cred["expiry_date"] = int(time.time() * 1000) + tok.get("expires_in", 3600) * 1000
        open(RC, "w", encoding="utf-8").write(json.dumps(store, indent=2))
    return tok["access_token"]


# ---------- Drive API ----------
def drive(token: str, method: str, path: str, body: dict | None = None, query: dict | None = None) -> dict:
    url = f"https://www.googleapis.com/drive/v3{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as res:
        raw = res.read().decode()
        return json.loads(raw) if raw else {}


def find_or_create_folder(token: str, name: str, parent: str | None = None) -> str:
    q = f"name = '{name}' and mimeType = '{FOLDER_MIME}' and trashed = false"
    if parent:
        q += f" and '{parent}' in parents"
    found = drive(token, "GET", "/files", query={"q": q, "fields": "files(id)"})
    if found.get("files"):
        return found["files"][0]["id"]
    meta = {"name": name, "mimeType": FOLDER_MIME}
    if parent:
        meta["parents"] = [parent]
    return drive(token, "POST", "/files", body=meta)["id"]


def upload_file(token: str, path: Path, folder_id: str, drive_name: str) -> dict:
    """같은 이름 파일이 있으면 내용 덮어쓰기(링크 유지), 없으면 새로 만들고 링크공유(뷰어) 설정."""
    q = (f"name = '{drive_name}' and '{folder_id}' in parents and trashed = false")
    found = drive(token, "GET", "/files", query={"q": q, "fields": "files(id,webViewLink)"})
    content = path.read_bytes()

    if found.get("files"):
        # 기존 파일 초기화: 내용만 교체 → 파일 ID·링크·권한 그대로 유지
        fid = found["files"][0]["id"]
        req = urllib.request.Request(
            f"https://www.googleapis.com/upload/drive/v3/files/{fid}?uploadType=media&fields=id,webViewLink",
            data=content, method="PATCH",
            headers={"Authorization": f"Bearer {token}", "Content-Type": XLSX_MIME},
        )
        with urllib.request.urlopen(req, timeout=120) as res:
            return json.loads(res.read().decode())

    boundary = uuid.uuid4().hex
    meta = json.dumps({"name": drive_name, "parents": [folder_id]}).encode()
    body = b"".join([
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
        meta,
        f"\r\n--{boundary}\r\nContent-Type: {XLSX_MIME}\r\n\r\n".encode(),
        content,
        f"\r\n--{boundary}--".encode(),
    ])
    req = urllib.request.Request(
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,webViewLink",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": f"multipart/related; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=120) as res:
        info = json.loads(res.read().decode())
    # 링크가 있는 모든 사용자: 뷰어(보기 전용)
    drive(token, "POST", f"/files/{info['id']}/permissions",
          body={"type": "anyone", "role": "reader"})
    return info


# ---------- 슬랙 ----------
def send_slack(payload: dict | str) -> None:
    hook = os.environ.get("SLACK_WEBHOOK_URL") or (
        keyring.get_password("carefor-auto", "slack_webhook_url") if keyring else None)
    if not hook:
        raise SystemExit("slack_webhook_url 자격증명이 없습니다.")
    if isinstance(payload, str):
        payload = {"text": payload}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(hook, data=body,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    out = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    if out.strip() != "ok":
        raise SystemExit(f"슬랙 전송 실패: {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    today = date.today()
    out_dir, paths, summaries = consult_excel.generate(today)

    token = google_token()
    root_id = find_or_create_folder(token, ROOT_FOLDER)

    links = []
    for p in paths:
        # 드라이브에는 날짜 없는 고정 이름으로 → 매번 같은 파일에 덮어쓰기 (링크 불변)
        center = p.stem.split("_")[0]
        drive_name = f"{center}_상담공지.xlsx"
        info = upload_file(token, p, root_id, drive_name)
        links.append((center, info["webViewLink"]))
        print(f"업로드(덮어쓰기): {drive_name}")

    weekday = "월화수목금토일"[today.weekday()]
    link_map = dict(links)

    # 지점별 한 줄 요약 + 해당 지점 엑셀 링크 (집계표·상세 명단은 전부 엑셀 안에)
    center_lines = []
    for s in summaries:
        extra_txt = f" (입소완료 {s['urgent']}건)" if s["urgent"] else ""
        link = link_map.get(s["center"], "")
        center_lines.append(
            f"*{s['center']}*  —  미입력 {s['miss']}건 · 대기 {s['wait']}건{extra_txt}"
            f"  →  <{link}|📎 엑셀>")

    total_miss = sum(s["miss"] for s in summaries)
    total_wait = sum(s["wait"] for s in summaries)

    msg = {
        "text": f"☎️ 상담 관리 공지 {today.strftime('%Y.%m.%d')}({weekday})",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "☎️ 상담 관리 공지", "emoji": True}},
            {"type": "context", "elements": [{"type": "mrkdwn",
                "text": f"{today.strftime('%Y.%m.%d')}({weekday}) · 전일자 기준 · "
                        f"상담시트 미입력 *{total_miss}건* · 상담 대기 *{total_wait}건*"}]},
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(center_lines)}},
            {"type": "section", "text": {"type": "mrkdwn",
                "text": f"전체 통합본  →  <{link_map.get('전체', '')}|📎 엑셀>"}},
            {"type": "divider"},
            {"type": "context", "elements": [{"type": "mrkdwn",
                "text": "엑셀 구성: 요약(집계표) · 신규상담 미입력 상세 · 상담 대기명단(아웃콜 차수·기한·연락처)\n"
                        "🔒 보기 전용 · 링크는 항상 동일, 내용만 최신 갱신 · 상담 결과 입력 시 대기명단에서 자동 제외"}]},
        ],
    }

    print("--- 슬랙 메시지 ---")
    print(json.dumps(msg, ensure_ascii=False)[:1500])
    if args.dry_run:
        print("(dry-run: 전송 안 함)")
        return
    send_slack(msg)
    print("전송 완료 → #차량관리 (webhook)")


if __name__ == "__main__":
    main()
