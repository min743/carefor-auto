# -*- coding: utf-8 -*-
"""
센터별 상담 대기 명단 → 슬랙 공지 (아웃콜 차수 포함)

데이터: 구글시트 '주보_충청본부_센터 현황' > '센터별 상담 대기 명단'
  (예정일 지났으나 상담 결과 미입력 건. 결과 입력되면 목록에서 사라짐)

사용:
  py -X utf8 waitlist_report.py --dry-run      # 미리보기
  py -X utf8 waitlist_report.py                # #차량관리 전송
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import date

sys.stdout.reconfigure(encoding="utf-8")

try:
    import keyring
except ImportError:
    keyring = None

SERVICE = "carefor-auto"
SHEET_NAME = "센터별 상담 대기 명단"


def _secret(env_name: str, keyring_key: str) -> str | None:
    v = os.environ.get(env_name)
    if v:
        return v
    return keyring.get_password(SERVICE, keyring_key) if keyring else None


def load_rows() -> list[list[str]]:
    url = _secret("CONSULT_WEBHOOK_URL", "consult_webhook_url")
    if not url:
        raise SystemExit("consult_webhook_url 자격증명이 없습니다.")
    req = url + ("&" if "?" in url else "?") + "sheet=" + urllib.parse.quote(SHEET_NAME)
    data = json.loads(urllib.request.urlopen(req, timeout=90).read().decode("utf-8"))
    if not data.get("ok"):
        raise SystemExit(f"webhook 오류: {data.get('error')}")
    return data["values"]


CENTER_RE = re.compile(r"\[([^\]]+?)\s*센터장\]")
ROUND_RE = re.compile(r"(\d+)\s*차\s*아웃콜")


def parse_center(manager: str) -> str:
    m = CENTER_RE.search(manager)
    if not m:
        return manager.strip()
    return m.group(1).replace("대전 ", "").strip()  # "대전 서구점"→"서구점"


def parse_round(type2: str) -> str:
    m = ROUND_RE.search(type2)
    if m:
        return f"{m.group(1)}차 아웃콜"
    # 아웃콜 아닌 상태 (예: 대면상담 예정)
    return type2.replace("(예정)", "").strip() or "-"


def norm_phone(p: str) -> str:
    p = re.sub(r"[^0-9]", "", p)
    if len(p) == 10 and p.startswith("10"):
        p = "0" + p
    if len(p) == 11:
        return f"{p[:3]}-{p[3:7]}-{p[7:]}"
    return p


def parse_due(s: str) -> date | None:
    """'2026. 6. 12' / '2026-06-12' 형태 → date."""
    nums = re.findall(r"\d+", s)
    if len(nums) >= 3:
        try:
            return date(int(nums[0]), int(nums[1]), int(nums[2]))
        except ValueError:
            return None
    return None


def build_message(rows: list[list[str]], today: date) -> str:
    weekday = "월화수목금토일"[today.weekday()]
    title = f"📞 센터별 상담 대기 명단 (예정일 경과·결과 미입력) {today.strftime('%Y.%m.%d')}({weekday})"

    # 데이터 행은 헤더(2줄) 이후
    items = []
    for row in rows[2:]:
        if len(row) >= 9 and row[0].strip():
            due_d = parse_due(row[8])
            overdue = (today - due_d).days if due_d else None
            items.append({
                "center": parse_center(row[0]),
                "first": row[1].strip(),
                "round": parse_round(row[7]),
                "due": row[8].strip(),
                "due_d": due_d,
                "overdue": overdue,  # 양수면 기한 지남(일수)
                "phone": norm_phone(row[4]),
            })

    if not items:
        return f"{title}\n\n대기 중인 미처리 상담이 없습니다. 👍"

    n_overdue = sum(1 for it in items if it["overdue"] and it["overdue"] > 0)

    by_center: dict[str, list[dict]] = {}
    for it in items:
        by_center.setdefault(it["center"], []).append(it)

    lines = [title,
             f"\n총 {len(items)}건 (기한 지남 {n_overdue}건) · 상담 결과 입력하면 목록에서 자동 제외됩니다."]
    for center in sorted(by_center):
        # 기한 많이 지난 순 → 예정일 빠른 순 정렬
        grp = sorted(by_center[center],
                     key=lambda it: (-(it["overdue"] if it["overdue"] is not None else -999),))
        lines.append(f"\n*{center}* ({len(grp)}건)")
        for it in grp:
            if it["overdue"] is not None and it["overdue"] > 0:
                flag = f" ⚠️ 기한 {it['overdue']}일 지남"
            elif it["overdue"] == 0:
                flag = " 🔔 오늘"
            else:
                flag = ""
            lines.append(f"· {it['round']} | 예정 {it['due']}{flag} | {it['phone']}")

    return "\n".join(lines)


def send_via_webhook(webhook_url: str, text: str) -> None:
    body = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=body,
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    out = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
    if out.strip() != "ok":
        raise SystemExit(f"슬랙 전송 실패: {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = load_rows()
    msg = build_message(rows, date.today())

    if os.environ.get("GITHUB_ACTIONS"):
        print(f"메시지 생성 완료 ({len(msg)}자)")
    else:
        print("--- 미리보기 ---")
        print(msg)
        print("----------------")

    if args.dry_run:
        print("(dry-run: 전송 안 함)")
        return

    hook = _secret("SLACK_WEBHOOK_URL", "slack_webhook_url")
    if not hook:
        raise SystemExit("slack_webhook_url 자격증명이 없습니다.")
    send_via_webhook(hook, msg)
    print("전송 완료 → #차량관리 (webhook)")


if __name__ == "__main__":
    main()
