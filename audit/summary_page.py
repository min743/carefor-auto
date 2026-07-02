"""본부 공유용 요약 웹페이지 생성 — 개인정보(수급자 이름 등) 완전 제외.

audit_results/*.json → docs/audit_summary.html (지점별 36항목 신호등 + 건수만)
생성 후 git push 하면 https://min743.github.io/carefor-auto/audit_summary.html 에서 열람.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .items import ITEMS

ROOT = Path(__file__).resolve().parent.parent
AUDIT_DIR = ROOT / "audit_results"
OUT = ROOT / "docs" / "audit_summary.html"

PIN = "7436"  # 본부 공유용 간단 잠금 (변경 가능)

BRANCH_ORDER = ["둔산점", "서구점", "천안점", "청주 오창점"]


def _branch_summary(d: dict) -> dict:
    """개인정보 없는 요약만 추출."""
    an = d.get("analysis", {})
    stats = an.get("stats", {}) or {}
    return {
        "run_at": d.get("run_at", ""),
        "cutoff": d.get("cutoff", ""),
        "people": d.get("people", 0),
        "item_results": d.get("item_results", {}),
        "counts": {
            "대조회차": stats.get("total_rounds", 0),
            "불일치": stats.get("disc", 0),
            "반기누락": len(an.get("halfyear_miss", []) or []),
            "계획문제": len(an.get("plan_issues", []) or []),
            "순서위반": len(an.get("order_issues", []) or []),
        },
    }


def generate() -> Path:
    data = {}
    for f in AUDIT_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            data[d["branch"]] = _branch_summary(d)
        except Exception:
            continue

    items_slim = [{"no": it["no"], "name": it["name"], "method": it["method"]} for it in ITEMS]
    payload = json.dumps({"branches": data, "items": items_slim,
                          "generated": datetime.now().strftime("%Y-%m-%d %H:%M")}, ensure_ascii=False)

    html = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>지점 점검 요약</title>
<style>
body{font-family:'맑은 고딕',sans-serif;margin:0;background:#f4f6fa;color:#222}
header{background:#2f5496;color:#fff;padding:14px 20px}
header h1{font-size:18px;margin:0}
.sub{font-size:12px;opacity:.85;margin-top:3px}
.wrap{padding:16px;max-width:1100px;margin:0 auto}
.cards{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.bcard{background:#fff;border-radius:10px;padding:12px 16px;box-shadow:0 2px 6px rgba(0,0,0,.08);min-width:150px;flex:1}
.bcard h3{margin:0 0 6px;font-size:15px;color:#2f5496}
.bcard .meta{font-size:11px;color:#888}
.bcard .nums{font-size:12px;margin-top:6px;line-height:1.7}
table{border-collapse:collapse;width:100%;background:#fff;font-size:12px;box-shadow:0 2px 6px rgba(0,0,0,.08)}
th,td{border:1px solid #dde3ee;padding:6px 8px;text-align:center}
th{background:#eef2f9}
td.name{text-align:left;white-space:nowrap}
.ok{color:#2c8a41;font-weight:bold}.bad{color:#c02020;font-weight:bold}
.na{color:#999}.man{color:#4a69b0}
.dot{display:inline-block;width:11px;height:11px;border-radius:50%;margin-right:4px;vertical-align:-1px}
.d-ok{background:#35a94e}.d-bad{background:#d93a3a}.d-na{background:#c2c2c2}.d-man{background:#6f8fd6}
#gate{position:fixed;inset:0;background:#2f5496;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px;color:#fff}
#gate input{font-size:22px;padding:8px 14px;border-radius:8px;border:0;width:130px;text-align:center;letter-spacing:6px}
.note{font-size:11px;color:#777;margin-top:10px;line-height:1.6}
</style></head><body>
<div id="gate"><h2>🔒 지점 점검 요약</h2><div>접속 번호 4자리를 입력하세요</div>
<input id="pin" type="password" maxlength="4" inputmode="numeric" autofocus></div>
<header><h1>📋 지점 점검 요약 (본부 공유용)</h1><div class="sub" id="gen"></div></header>
<div class="wrap">
<div class="cards" id="cards"></div>
<table id="tbl"></table>
<div class="note">· 이 페이지에는 수급자 개인정보가 포함되어 있지 않습니다 (지점 단위 집계만).<br>
· 상세 내역(수급자별)은 지점 점검 PC의 대시보드에서만 확인할 수 있습니다.<br>
· 상태 기준 — <span class="ok">양호</span>: 문제 0건 / <span class="bad">미흡</span>: 문제 있음 / <span class="na">수집전</span>: 자동수집 미구현·미실행 / <span class="man">수기</span>: 현장 확인 항목</div>
</div>
<script>
const DATA = __PAYLOAD__;
const PIN = '__PIN__';
const BR = ['둔산점','서구점','천안점','청주 오창점'];
document.getElementById('pin').addEventListener('input', e => {
  if(e.target.value === PIN){ document.getElementById('gate').style.display='none'; sessionStorage.setItem('ap','1'); }
});
if(sessionStorage.getItem('ap')==='1') document.getElementById('gate').style.display='none';
document.getElementById('gen').textContent = '생성: ' + DATA.generated;
const cards = document.getElementById('cards');
BR.forEach(b => {
  const d = DATA.branches[b];
  const el = document.createElement('div'); el.className='bcard';
  if(!d){ el.innerHTML = `<h3>${b}</h3><div class="meta">수집 전</div>`; cards.appendChild(el); return; }
  const c = d.counts;
  el.innerHTML = `<h3>${b}</h3><div class="meta">수집 ${d.run_at} · 기준일 ${d.cutoff} · ${d.people}명</div>
  <div class="nums">낙상↔욕구 불일치 <b class="bad">${c.불일치}</b>/${c.대조회차}회차<br>
  반기별 평가 누락 <b class="bad">${c.반기누락}</b>건 · 계획 문제 <b class="bad">${c.계획문제}</b>건</div>`;
  cards.appendChild(el);
});
function cell(b, it){
  const d = DATA.branches[b];
  if(it.method==='manual') return '<span class="dot d-man"></span><span class="man">수기</span>';
  if(!d) return '<span class="dot d-na"></span><span class="na">-</span>';
  const r = d.item_results[String(it.no)];
  if(!r) return '<span class="dot d-na"></span><span class="na">수집전</span>';
  if(r.status==='양호') return '<span class="dot d-ok"></span><span class="ok">양호</span>';
  return `<span class="dot d-bad"></span><span class="bad" title="${r.detail}">미흡</span>`;
}
let h = '<tr><th style="width:30px">#</th><th style="width:170px">항목</th>' + BR.map(b=>`<th>${b}</th>`).join('') + '</tr>';
DATA.items.forEach(it => {
  h += `<tr><td>${it.no}</td><td class="name">${it.name}</td>` + BR.map(b=>`<td>${cell(b,it)}</td>`).join('') + '</tr>';
});
document.getElementById('tbl').innerHTML = h;
</script></body></html>"""
    html = html.replace("__PAYLOAD__", payload).replace("__PIN__", PIN)
    OUT.write_text(html, encoding="utf-8")
    return OUT


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    p = generate()
    print("생성 완료:", p)
