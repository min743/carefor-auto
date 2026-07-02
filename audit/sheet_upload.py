"""점검 결과를 구글시트 '지점점검' 탭으로 업로드 (기존 Apps Script webhook 재사용).

본부 공유: 구글시트 공유 권한 = 진짜 계정 인증. 수기 항목은 시트에서 직접 입력.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import requests

from src import credentials
from .items import ITEMS

ROOT = Path(__file__).resolve().parent.parent
AUDIT_DIR = ROOT / "audit_results"


def build_payload() -> dict:
    branches = {}
    for f in AUDIT_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        an = d.get("analysis", {})
        stats = an.get("stats", {}) or {}
        branches[d["branch"]] = {
            "run_at": d.get("run_at", ""),
            "cutoff": d.get("cutoff", ""),
            "people": d.get("people", 0),
            "counts": {
                "대조회차": stats.get("total_rounds", 0),
                "불일치": stats.get("disc", 0),
                "반기누락": len(an.get("halfyear_miss", []) or []),
                "계획문제": len(an.get("plan_issues", []) or []),
            },
            "item_results": d.get("item_results", {}),
        }
    return {
        "action": "audit",
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "items": [{"no": it["no"], "name": it["name"], "method": it["method"]} for it in ITEMS],
        "branches": branches,
    }


def upload() -> None:
    url = credentials.get_audit_webhook()
    if not url:
        print("지점점검용 구글시트 webhook URL이 저장되어 있지 않습니다.")
        print("점검 전용 시트의 Apps Script 배포 URL을 set_audit_webhook 으로 저장하세요.")
        return
    payload = build_payload()
    if not payload["branches"]:
        print("업로드할 점검 결과가 없습니다. run_audit.py 를 먼저 실행하세요.")
        return
    res = requests.post(url, json=payload, timeout=60)
    res.raise_for_status()
    out = res.json()
    if out.get("ok"):
        print(f"구글시트 업로드 완료 → '{out.get('tab', '지점점검')}' 탭 ({len(payload['branches'])}개 지점)")
    else:
        print(f"업로드 실패: {out.get('error')}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    upload()
