# -*- coding: utf-8 -*-
"""
신규상담 ↔ 상담시트 입력 여부 현황 → 슬랙 공지

데이터 소스: 구글시트 '주보_충청본부_센터 현황' > '신규상담 세부사항' 탭
  (본사에서 매일 전일자 기준 자동 갱신, 상담시트 입력 여부 Y/N 포함)

사용:
  py -X utf8 consult_report.py --tsv <파일경로>          # TSV 파일로 실행 (테스트용)
  py -X utf8 consult_report.py                           # Apps Script webhook에서 데이터 로드
  옵션: --channel <채널ID>  --dry-run(전송 없이 미리보기)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date

sys.stdout.reconfigure(encoding="utf-8")

try:
    import keyring
except ImportError:  # GitHub Actions에서는 env로 대체
    keyring = None


def _secret(env_name: str, keyring_key: str) -> str | None:
    v = os.environ.get(env_name)
    if v:
        return v
    return keyring.get_password(SERVICE, keyring_key) if keyring else None

SERVICE = "carefor-auto"
KEY_BOT_TOKEN = "slack_bot_token"
KEY_CONSULT_WEBHOOK = "consult_webhook_url"  # Apps Script 웹앱 URL (독립 스크립트)

TEST_CHANNEL = "C0BC37EB38C"  # #차량관리 (테스트 방)
SHEET_NAME = "신규상담 세부사항"

# 집계 기준 시작 연월 — 이전 자료는 찾기 어려워 제외 (사용자 확정 2026-07-07)
CUTOFF_YM = "2026년 05월"

# 표 표시 순서 (짧은 이름: 시트의 센터명 매칭용 접두)
CENTER_ORDER = [("둔산", "대전둔산점"), ("서구", "대전서구점"),
                ("천안", "천안점"), ("청주오창", "청주오창점")]


# ---------- 표 정렬 유틸 (한글 2칸 폭) ----------
def _w(s: str) -> int:
    return sum(2 if ord(c) > 0x1100 else 1 for c in s)


def _rpad(s: str, width: int) -> str:
    return s + " " * max(0, width - _w(s))


def _lpad(s: str, width: int) -> str:
    return " " * max(0, width - _w(s)) + s


# ---------- 데이터 로드 ----------
def load_rows_from_tsv(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            c = line.rstrip("\n").split("\t")
            if len(c) >= 14 and c[0][:4].isdigit() and "년" in c[0]:
                rows.append(_row(c))
    result = [r for r in rows if r["yearmonth"] >= CUTOFF_YM]
    for r in result:  # TSV(테스트)는 네트워크 없이 기존 방식
        r["missing"] = (r["sheet_entered"] == "N")
    return result


def load_rows_from_webhook() -> list[dict]:
    rows = []
    for c in _webapp_values("main", SHEET_NAME):
        c = [str(x) if x is not None else "" for x in c]
        if len(c) >= 14 and c[0][:4].isdigit() and "년" in c[0]:
            rows.append(_row(c))
    return annotate_missing([r for r in rows if r["yearmonth"] >= CUTOFF_YM])


def _row(c: list[str]) -> dict:
    return {
        "yearmonth": c[0].strip(),      # 연월
        "center": c[4].strip(),         # 센터명
        "week": c[5].strip(),           # 해당 주차
        "consult_date": c[6].strip(),   # 상담일자
        "start_date": c[7].strip(),     # 급여개시일자
        "phone": c[9].strip(),          # 고객 번호
        "sheet_entered": c[10].strip(), # 상담시트 입력 여부 Y/N
        "admitted": c[11].strip(),      # 수급자 입소 여부 Y/N
        "summary": c[13].strip() if len(c) > 13 else "",  # AI 요약
    }


# ---------- 상담시트 번호 대조 (미입력 판정) ----------
# 실제 입력 시트 = 충청본부_상담시트(ss=phone). 탭별 '연락처' 열 위치.
ENTRY_TABS = [("유선상담", 4), ("대면상담", 5), ("계약상담", 6), ("상담요청", 4), ("등급신청", 5)]
_PHONE_CACHE: dict = {}


def _norm_phone(s: str) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _webapp_values(ss: str, sheet: str) -> list:
    url = _secret("CONSULT_WEBHOOK_URL", KEY_CONSULT_WEBHOOK)
    if not url:
        raise SystemExit("consult_webhook_url 자격증명이 없습니다.")
    u = f"{url}{'&' if '?' in url else '?'}ss={ss}&sheet={urllib.parse.quote(sheet)}"
    last = None
    for attempt in range(4):  # Apps Script 간헐적 5xx 대비 재시도
        try:
            with urllib.request.urlopen(urllib.request.Request(u), timeout=60) as res:
                data = json.loads(res.read().decode("utf-8"))
            if not data.get("ok"):
                raise RuntimeError(f"ok=false: {data.get('error')}")
            return data.get("values", [])
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"webhook 실패({ss}/{sheet}) 4회: {last}")


def load_entered_phones() -> set:
    """실제 상담시트(충청본부_상담시트)에 입력된 연락처 전체."""
    if "entered" in _PHONE_CACHE:
        return _PHONE_CACHE["entered"]
    s = set()
    for sheet, col in ENTRY_TABS:
        for row in _webapp_values("phone", sheet)[1:]:  # 0행=헤더
            if len(row) > col:
                p = _norm_phone(row[col])
                if len(p) >= 10:
                    s.add(p)
    _PHONE_CACHE["entered"] = s
    return s


def load_excluded_phones() -> set:
    """제외번호(Lost Lead 등) — 미입력에서 제외."""
    if "excl" in _PHONE_CACHE:
        return _PHONE_CACHE["excl"]
    s = set()
    for row in _webapp_values("main", "제외번호"):  # 헤더 없음
        if row:
            p = _norm_phone(row[0])
            if len(p) >= 10:
                s.add(p)
    _PHONE_CACHE["excl"] = s
    return s


def annotate_missing(rows: list) -> list:
    """각 행에 r['missing'] 설정 = 번호가 상담시트에 없고 제외번호도 아님.
    상담시트/제외번호 로드 실패 시 기존 본사 Y/N(sheet_entered)로 안전 대체(공지 끊김 방지)."""
    try:
        entered = load_entered_phones()
        excl = load_excluded_phones()
    except Exception as e:
        print(f"[경고] 상담시트/제외번호 로드 실패 → 본사 Y/N로 대체: {e}")
        for r in rows:
            r["missing"] = (r["sheet_entered"] == "N")
        return rows
    for r in rows:
        p = _norm_phone(r["phone"])
        r["missing"] = (p not in entered and p not in excl) if p else (r["sheet_entered"] == "N")
    return rows


# ---------- 메시지 생성 ----------
def build_message(rows: list[dict], today: date) -> dict:
    """슬랙 Block Kit 페이로드 생성 (text는 알림용 폴백)."""
    weekday = "월화수목금토일"[today.weekday()]
    title = "☎️ 신규상담 시트 입력 현황(아롱이)"
    subtitle = f"{today.strftime('%Y.%m.%d')}({weekday}) · 전일자 기준 · 2026년 5월~ 누적"

    by_center = {}
    for r in rows:
        by_center.setdefault(r["center"], []).append(r)

    names, totals, misses, rates = [], [], [], []
    for short, full in CENTER_ORDER:
        grp = by_center.get(full, [])
        n_total = len(grp)
        n_miss = sum(1 for r in grp if r.get("missing"))
        names.append(short)
        totals.append(str(n_total))
        misses.append(str(n_miss))
        rates.append(f"{round(n_miss / n_total * 100)}%" if n_total else "-")

    LABEL_W = 16
    col_ws = [max(_w(n), 4) + 2 for n in names]
    header = _rpad("", LABEL_W) + "".join(_lpad(n, w) for n, w in zip(names, col_ws))
    sep = "─" * (LABEL_W + sum(col_ws))
    lines = [header, sep]
    for label, vals in [("신규상담(누적)", totals), ("시트 미입력", misses), ("미입력률", rates)]:
        lines.append(_rpad(label, LABEL_W) + "".join(_lpad(v, w) for v, w in zip(vals, col_ws)))
    table = "\n".join(lines)

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": title, "emoji": True}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": subtitle}]},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": "💛 상담 한 분 한 분이 소중한 인연입니다 — 오늘의 상담 한 통이 어르신과의 첫 만남이 됩니다."}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"```\n{table}\n```"}},
        {"type": "context", "elements": [{"type": "mrkdwn",
            "text": "📝 상담시트 입력 부탁드립니다. 상세 명단(연락처 포함)은 엑셀 링크 공지 참조."}]},
    ]
    fallback = f"{title} {subtitle}"
    return {"text": fallback, "blocks": blocks}


# ---------- 슬랙 전송 (Incoming Webhook — 봇 토큰/유료 불필요) ----------
def send_via_webhook(webhook_url: str, payload: dict | str) -> None:
    if isinstance(payload, str):
        payload = {"text": payload}
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        out = res.read().decode("utf-8")
    if out.strip() != "ok":
        raise SystemExit(f"슬랙 전송 실패: {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tsv", help="TSV 파일 경로 (지정 시 webhook 대신 사용)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = load_rows_from_tsv(args.tsv) if args.tsv else load_rows_from_webhook()
    print(f"데이터 {len(rows)}건 로드")

    msg = build_message(rows, date.today())
    if os.environ.get("GITHUB_ACTIONS"):
        # 공개 저장소 로그에 연락처가 남지 않도록 전문은 출력하지 않음
        print("메시지 생성 완료")
    else:
        print("--- 메시지 미리보기 (blocks) ---")
        print(json.dumps(msg, ensure_ascii=False, indent=1)[:2000])
        print("----------------------")

    if args.dry_run:
        print("(dry-run: 전송 안 함)")
        return

    # 아롱이 앱 웹훅 (없으면 기존 차량관리 웹훅으로 폴백)
    hook = _secret("ARONGI_WEBHOOK_URL", "arongi_webhook_url") or _secret("SLACK_WEBHOOK_URL", "slack_webhook_url")
    if not hook:
        raise SystemExit("arongi_webhook_url 자격증명이 없습니다.")
    send_via_webhook(hook, msg)
    print("전송 완료 → #차량관리 (webhook)")


if __name__ == "__main__":
    main()
