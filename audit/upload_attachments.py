# -*- coding: utf-8 -*-
"""정비 내역서 첨부(로컬) → 구글 드라이브 업로드 → index.json 에 링크 기록.

앱에서 📎 를 눌러 내역서를 바로 보게 하려는 용도.

⚠️ 케어포 원본 URL 은 못 쓴다 — 경로 끝이 난수라 재구성이 불가능하고(수집 때 저장 안 했다),
   재수집해 URL 을 얻어도 **케어포 로그인 세션이 있어야만** 열린다.
   그래서 이미 받아둔 로컬 파일을 드라이브에 올린다.

🔒 공개 범위는 **caring.co.kr 도메인 한정**(anyone 아님) — 정비명세서에 정비소 상호·금액·
   차량번호가 있다. 링크만 알면 아무나 보는 상태로 두지 않는다.
   ※ 회사 구글 계정으로 로그인돼 있어야 열린다.

실행: py -X utf8 -m audit.upload_attachments [--force]
     (이미 올린 건 건너뛴다. index.json 의 files_url 로 판단)
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
RES = pathlib.Path(__file__).resolve().parent.parent / "audit_results"
FOLDER_NAME = "차량 정비이력 첨부"
DOMAIN = "caring.co.kr"


def token() -> str:
    d = json.loads((pathlib.Path.home() / ".clasprc.json").read_text())
    tok = d.get("tokens", {}).get("default") or d.get("token") or {}
    body = urllib.parse.urlencode({
        "client_id": d.get("oauth2ClientSettings", {}).get("clientId") or tok.get("client_id"),
        "client_secret": d.get("oauth2ClientSettings", {}).get("clientSecret") or tok.get("client_secret"),
        "refresh_token": tok.get("refresh_token"), "grant_type": "refresh_token"}).encode()
    return json.load(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=body)))["access_token"]


def req(at, url, data=None, method=None, ctype="application/json"):
    r = urllib.request.Request(url, data=data,
                               headers={"Authorization": "Bearer " + at, "Content-Type": ctype},
                               method=method)
    try:
        return json.load(urllib.request.urlopen(r, timeout=180))
    except urllib.error.HTTPError as e:
        return {"ERR": e.read().decode()[:200]}


def get_folder(at) -> str:
    q = urllib.parse.quote(
        f"name='{FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false")
    r = req(at, f"https://www.googleapis.com/drive/v3/files?q={q}&fields=files(id)")
    if r.get("files"):
        return r["files"][0]["id"]
    f = req(at, "https://www.googleapis.com/drive/v3/files",
            json.dumps({"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}).encode())
    return f["id"]


def upload(at, path: pathlib.Path, name: str, parent: str) -> str | None:
    meta = json.dumps({"name": name, "parents": [parent]}).encode()
    ct = mimetypes.guess_type(name)[0] or "application/octet-stream"
    b = b"==bnd=="
    data = (b"--" + b + b"\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n" + meta +
            b"\r\n--" + b + b"\r\nContent-Type: " + ct.encode() + b"\r\n\r\n" + path.read_bytes() +
            b"\r\n--" + b + b"--")
    up = req(at, "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
             data, ctype="multipart/related; boundary=" + b.decode())
    if up.get("ERR"):
        print(f"    업로드 실패 {name}: {up['ERR'][:80]}")
        return None
    fid = up["id"]
    pm = req(at, f"https://www.googleapis.com/drive/v3/files/{fid}/permissions",
             json.dumps({"type": "domain", "role": "reader", "domain": DOMAIN}).encode())
    if pm.get("ERR"):
        print(f"    권한 설정 실패 {name}: {pm['ERR'][:80]}")
    return fid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="이미 올린 것도 다시")
    args = ap.parse_args()

    at = token()
    parent = get_folder(at)
    print(f"드라이브 폴더: {parent} (공개범위: {DOMAIN} 도메인 한정)\n")

    t0, n_up, n_skip = time.time(), 0, 0
    for d in sorted(RES.glob("정비이력_*")):
        idx = d / "index.json"
        data = json.loads(idx.read_text(encoding="utf-8"))
        for r in data["records"]:
            files = r.get("files") or []
            if not files:
                continue
            if r.get("files_url") and not args.force:
                n_skip += 1
                continue
            urls = []
            for fn in files:
                p = d / r["car"] / fn
                if not p.exists():
                    continue
                # 드라이브에서 알아볼 수 있게 이름을 지점·차량·정비일로 붙인다
                name = f"{data['branch']}_{r['car']}_{r['date']}_{fn}"
                fid = upload(at, p, name, parent)
                if fid:
                    urls.append(f"https://drive.google.com/file/d/{fid}/view")
                    n_up += 1
            if urls:
                r["files_url"] = urls
                print(f"  [{data['branch'][:2]}] {r['car'][:12]:<12} {r['date']} → {len(urls)}개", flush=True)
        idx.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n완료: 업로드 {n_up}개 · 건너뜀 {n_skip}건 ({time.time()-t0:.0f}초)")
    print("다음: py -X utf8 -m audit.push_maintenance_to_sheet")


if __name__ == "__main__":
    main()
