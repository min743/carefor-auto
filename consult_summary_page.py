# -*- coding: utf-8 -*-
"""본부 공유용 상담 현황 요약 웹페이지 생성 — 개인정보(이름·연락처) 완전 제외.

구글시트 → docs/consult_summary.html (센터 단위 집계만)
  · 신규상담 상담시트 입력 현황 (신규상담 누적 / 시트 미입력 / 미입력률)
  · 센터별 상담 대기 명단 (대기 건수 / 기한경과 / 아웃콜 차수 분포)
생성 후 git push 하면 https://min743.github.io/carefor-auto/consult_summary.html 에서 열람.
지점점검 요약페이지(summary_page.py)와 동일한 PIN 게이트·noindex 정책.

실행: py -X utf8 consult_summary_page.py
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import consult_report as cr
import waitlist_report as wr

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "docs" / "consult_summary.html"
PIN = "15771389"  # 지점점검 요약페이지와 동일 (본부 공유용 간단 잠금)


def consult_rows() -> list[dict]:
    """센터별 신규상담 누적·상담시트 미입력·미입력률 (이름·연락처 없음)."""
    rows = cr.load_rows_from_webhook()
    by_center: dict[str, list] = {}
    for r in rows:
        by_center.setdefault(r["center"], []).append(r)
    out = []
    for short, full in cr.CENTER_ORDER:
        grp = by_center.get(full, [])
        n_total = len(grp)
        n_miss = sum(1 for r in grp if r.get("missing"))
        out.append({
            "center": short,
            "total": n_total,
            "miss": n_miss,
            "rate": round(n_miss / n_total * 100) if n_total else 0,
        })
    return out


def waitlist_rows(today: date) -> dict:
    """센터별 대기 건수·기한경과·아웃콜 차수 분포 (이름·연락처 없음)."""
    rows = wr.load_rows()
    items = []
    for row in rows[2:]:
        if len(row) >= 9 and row[0].strip():
            due_d = wr.parse_due(row[8])
            overdue = (today - due_d).days if due_d else None
            items.append({
                "center": wr.parse_center(row[0]),
                "round": wr.parse_round(row[7]),
                "overdue": overdue,
            })
    by_center: dict[str, list] = {}
    for it in items:
        by_center.setdefault(it["center"], []).append(it)
    centers = []
    for center in sorted(by_center):
        grp = by_center[center]
        rounds: dict[str, int] = {}
        for it in grp:
            rounds[it["round"]] = rounds.get(it["round"], 0) + 1
        n_overdue = sum(1 for it in grp if it["overdue"] and it["overdue"] > 0)
        breakdown = " · ".join(f"{k} {v}" for k, v in sorted(rounds.items()))
        centers.append({
            "center": center,
            "count": len(grp),
            "overdue": n_overdue,
            "breakdown": breakdown or "-",
        })
    return {"centers": centers, "total": len(items)}


def _consult_table_html(rows: list[dict]) -> str:
    body = ""
    tot_all = miss_all = 0
    for r in rows:
        tot_all += r["total"]
        miss_all += r["miss"]
        miss_cls = "bad" if r["miss"] else "ok"
        rate_cls = "bad" if r["rate"] >= 30 else ("warn" if r["rate"] > 0 else "ok")
        body += (f'<tr><td class="name">{r["center"]}</td>'
                 f'<td>{r["total"]}</td>'
                 f'<td class="{miss_cls}">{r["miss"]}</td>'
                 f'<td class="{rate_cls}">{r["rate"]}%</td></tr>')
    rate_all = round(miss_all / tot_all * 100) if tot_all else 0
    body += (f'<tr class="sum"><td class="name">합계</td><td>{tot_all}</td>'
             f'<td class="{"bad" if miss_all else "ok"}">{miss_all}</td>'
             f'<td>{rate_all}%</td></tr>')
    return ('<table><tr><th>센터</th><th>신규상담(누적)</th>'
            '<th>시트 미입력</th><th>미입력률</th></tr>' + body + '</table>')


def _waitlist_table_html(w: dict) -> str:
    if not w["centers"]:
        return '<div class="empty">대기 중인 미처리 상담이 없습니다. 👍</div>'
    body = ""
    for c in w["centers"]:
        ov_cls = "bad" if c["overdue"] else "ok"
        body += (f'<tr><td class="name">{c["center"]}</td>'
                 f'<td>{c["count"]}</td>'
                 f'<td class="{ov_cls}">{c["overdue"]}</td>'
                 f'<td class="brk">{c["breakdown"]}</td></tr>')
    body += (f'<tr class="sum"><td class="name">합계</td><td>{w["total"]}</td>'
             f'<td colspan="2"></td></tr>')
    return ('<table><tr><th>센터</th><th>대기 건수</th>'
            '<th>기한경과</th><th>아웃콜 차수 분포</th></tr>' + body + '</table>')


TEMPLATE = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>상담 현황 요약</title>
<style>
body{font-family:'맑은 고딕',sans-serif;margin:0;background:#f4f6fa;color:#222}
header{background:#2f5496;color:#fff;padding:14px 20px}
header h1{font-size:18px;margin:0}
.sub{font-size:12px;opacity:.85;margin-top:3px}
.wrap{padding:16px;max-width:820px;margin:0 auto}
h2{font-size:15px;color:#2f5496;margin:20px 0 8px}
table{border-collapse:collapse;width:100%;background:#fff;font-size:13px;box-shadow:0 2px 6px rgba(0,0,0,.08)}
th,td{border:1px solid #dde3ee;padding:8px 10px;text-align:center}
th{background:#eef2f9}
td.name{text-align:left;font-weight:bold;white-space:nowrap}
td.brk{text-align:left;color:#555;font-size:12px}
tr.sum td{background:#f6f8fc;font-weight:bold}
.ok{color:#2c8a41}.bad{color:#c02020;font-weight:bold}.warn{color:#b57a00;font-weight:bold}
.empty{background:#fff;border-radius:8px;padding:24px;text-align:center;color:#888;box-shadow:0 2px 6px rgba(0,0,0,.08)}
.back{display:inline-block;margin:14px 0 0;color:#2f5496;text-decoration:none;font-size:13px}
.note{font-size:11px;color:#777;margin-top:16px;line-height:1.7}
#gate{position:fixed;inset:0;background:#2f5496;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px;color:#fff}
#gate input{font-size:22px;padding:8px 14px;border-radius:8px;border:0;width:130px;text-align:center;letter-spacing:6px}
</style></head><body>
<div id="gate"><h2 style="color:#fff">🔒 상담 현황 요약</h2><div>접속 번호를 입력하세요</div>
<input id="pin" type="password" maxlength="12" inputmode="numeric" autofocus></div>
<header><h1>☎️ 상담 현황 요약 (본부 공유용)</h1><div class="sub">생성: __GEN__ · __CUTOFF__~ 누적 · 전일자 기준</div></header>
<div class="wrap">
<h2>📋 신규상담 상담시트 입력 현황</h2>
__CONSULT_TABLE__
<h2>📞 센터별 상담 대기 명단</h2>
__WAITLIST_TABLE__
<a class="back" href="hq.html">← 본부 공유 허브로</a>
<div class="note">· 이 페이지에는 수급자 개인정보(이름·연락처)가 포함되어 있지 않습니다 — 센터 단위 집계만.<br>
· 건별 상세(첫상담일·예정일·연락처)는 슬랙 엑셀 링크 공지에서만 확인할 수 있습니다.<br>
· 미입력률 <span class="bad">30% 이상</span> 붉은색 · 대기 명단은 결과 입력 시 자동 제외.</div>
</div>
<script>
const PIN='__PIN__';
document.getElementById('pin').addEventListener('input',e=>{
  if(e.target.value===PIN){document.getElementById('gate').style.display='none';sessionStorage.setItem('ap','1');}
});
if(sessionStorage.getItem('ap')==='1')document.getElementById('gate').style.display='none';
</script>
</body></html>"""


def generate() -> Path:
    today = date.today()
    consult_html = _consult_table_html(consult_rows())
    try:
        waitlist_html = _waitlist_table_html(waitlist_rows(today))
    except Exception as e:  # 대기명단 시트 접근 실패해도 상담 현황은 게시
        waitlist_html = f'<div class="empty">대기 명단 로드 실패: {e}</div>'
    html = (TEMPLATE
            .replace("__CONSULT_TABLE__", consult_html)
            .replace("__WAITLIST_TABLE__", waitlist_html)
            .replace("__GEN__", today.strftime("%Y-%m-%d"))
            .replace("__CUTOFF__", cr.CUTOFF_YM)
            .replace("__PIN__", PIN))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    return OUT


if __name__ == "__main__":
    p = generate()
    print("생성 완료:", p)
