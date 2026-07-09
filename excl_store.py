# -*- coding: utf-8 -*-
"""제외번호 시트(앱 관리 구글시트) 읽기/추가 — 여러 스크립트 공용.

시트: consult_report.EXCL_SSID / EXCL_SHEET (앱 계정 소유, 본사 데이터 아님).
쓰기: Drive media PATCH(전체 덮어쓰기). 읽기: consult 웹앱 ssid.
GitHub Actions에서도 동작(GOOGLE_OAUTH_JSON / CONSULT_WEBHOOK_URL env).
"""
from __future__ import annotations

import io
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date

from openpyxl import Workbook

import consult_report as cr

RC = os.path.join(os.path.expanduser("~"), ".clasprc.json")


def _token() -> str:
    env = os.environ.get("GOOGLE_OAUTH_JSON")
    store = json.loads(env) if env else json.load(open(RC, encoding="utf-8"))
    c = store["tokens"]["default"]
    if not env and c.get("expiry_date", 0) > time.time() * 1000 + 60000:
        return c["access_token"]
    data = urllib.parse.urlencode({
        "client_id": c["client_id"], "client_secret": c["client_secret"],
        "refresh_token": c["refresh_token"], "grant_type": "refresh_token"}).encode()
    return json.load(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data), timeout=30))["access_token"]


def read_rows() -> list:
    """제외번호 시트 전체 값(헤더 포함)."""
    return cr._webapp_values(ssid=cr.EXCL_SSID, sheet=cr.EXCL_SHEET)


def _overwrite(rows: list) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = cr.EXCL_SHEET
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    req = urllib.request.Request(
        f"https://www.googleapis.com/upload/drive/v3/files/{cr.EXCL_SSID}?uploadType=media",
        data=buf.getvalue(), method="PATCH",
        headers={"Authorization": "Bearer " + _token(),
                 "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
    urllib.request.urlopen(req, timeout=60)


def add_phones(pairs: list) -> int:
    """pairs=[(phone, reason), ...] 중 신규만 추가. 추가된 건수 반환. 캐시 무효화."""
    rows = read_rows()
    header = rows[0] if rows else ["번호", "사유", "추가일"]
    existing = {cr._norm_phone(r[0]) for r in rows[1:] if r}
    today = date.today().isoformat()
    new = []
    for phone, reason in pairs:
        d = cr._norm_phone(phone)
        if len(d) >= 10 and d not in existing:
            new.append([str(phone), str(reason or "비대상"), today])
            existing.add(d)
    if new:
        _overwrite(rows + new)
        cr._PHONE_CACHE.pop("excl", None)  # 다음 로드 시 재조회
    return len(new)
