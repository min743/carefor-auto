"""본부 공유용 요약 웹페이지 생성 — 개인정보(수급자 이름 등) 완전 제외.

audit_results/*.json → docs/audit_summary.html (지점별 36항목 신호등 + 건수만)
생성 후 git push 하면 https://min743.github.io/carefor-auto/audit_summary.html 에서 열람.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path

from .items import ITEMS
from .names import collect_from_audit_results, detail_for_share, name_rx

ROOT = Path(__file__).resolve().parent.parent
AUDIT_DIR = ROOT / "audit_results"
OUT = ROOT / "docs" / "audit_summary.html"

PIN = "15771389"  # 본부 공유용 간단 잠금 (변경 가능)

BRANCH_ORDER = ["둔산점", "서구점", "천안점", "청주 오창점"]

# ---- 개인정보(수급자·직원 이름) 제거 ----
# 자동판정 detail 자유텍스트에는 이름이 여러 형태로 섞임 → 알려진 패턴 제거 후,
# 그래도 이름 흔적(날짜·괄호한글·쉼표나열)이 남으면 detail 전체를 안전 문구로 대체(방어적).
_NAME_GATE = [
    re.compile(r"\d{4}\s*[.\-]\s*\d{1,2}"),        # 구체적 날짜(YYYY.MM) — 이름과 동반
    re.compile(r"[가-힣]{2,4}\s*\(\s*[가-힣]"),      # 한글(한글  = 이름(각)/이름(여) 등
    re.compile(r"[가-힣]{2,4}\s*,\s*[가-힣]{2,4}"),  # 쉼표로 이어진 한글 2개 = 이름목록
    re.compile(r":\s*[가-힣]{2,4}\s*[,)]"),          # 마커: 이름) / 이름,
    re.compile(r"\d{4}[-.]\d{1,2}\s+[가-힣]"),       # YYYY-MM 이름
]


def clean_detail(text: str) -> str:
    """자동판정 상세에서 개인정보(이름) 제거. 건수·사유 요약은 최대한 보존."""
    if not text:
        return text
    s = text
    s = re.sub(r"[가-힣]{2,4}(?:\([가-힣]\))?\s*\((?:입사\s*)?\d{4}[.\-\s0-9]*\)", "", s)  # 이름[(각)](날짜)
    s = re.sub(r"(\d{4}[-.]\d{1,2})\s+[가-힣]{2,4}", r"\1", s)                              # 날짜 이름
    s = re.sub(r"(\d+\s*명)\s*\([가-힣\s,]+\)", r"\1", s)                                   # N명(이름들)
    s = re.sub(r"(입사전 미제출:\s*)(?:;\s*입사전 미제출:\s*)+", "입사전 미제출 명단 생략; ", s)
    s = re.sub(r"[;,]\s*(?=[;,)])", "", s)
    s = re.sub(r"(?:;\s*)+", "; ", s)
    s = re.sub(r"—\s*;", "—", s)
    s = re.sub(r"\s{2,}", " ", s).strip(" ;,")
    for rx in _NAME_GATE:  # 방어 게이트: 흔적 남으면 통째 대체
        if rx.search(s):
            return "[상세 명단은 지점 대시보드에서 확인]"
    return s


def _branch_summary(d: dict, rx) -> dict:
    """개인정보 없는 요약만 추출 (이름은 '강○희'로 마스킹)."""
    an = d.get("analysis", {})
    stats = an.get("stats", {}) or {}
    # item_results 의 detail 자유텍스트에서 이름 마스킹 (원본 훼손 없이 사본)
    safe_items = {}
    for no, r in (d.get("item_results") or {}).items():
        rr = dict(r) if isinstance(r, dict) else r
        if isinstance(rr, dict) and rr.get("detail"):
            # 마스킹 후 HTML 이스케이프(<, &, > 로 표 렌더 깨짐/주입 방지 — innerHTML 삽입됨)
            rr["detail"] = html.escape(detail_for_share(rr["detail"], rx))
        safe_items[no] = rr
    return {
        "run_at": d.get("run_at", ""),
        "cutoff": d.get("cutoff", ""),
        "people": d.get("people", 0),
        "item_results": safe_items,
        "counts": {
            "대조회차": stats.get("total_rounds", 0),
            "불일치": stats.get("disc", 0),
            "반기누락": len(an.get("halfyear_miss", []) or []),
            "계획문제": len(an.get("plan_issues", []) or []),
            "순서위반": len(an.get("order_issues", []) or []),
        },
    }


def generate() -> Path:
    rx = name_rx(collect_from_audit_results(AUDIT_DIR))  # 수급자+직원 이름 집합 (마스킹용)
    data = {}
    for f in AUDIT_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            data[d["branch"]] = _branch_summary(d, rx)
        except Exception:
            continue

    items_slim = [{"no": it["no"], "name": it["name"], "method": it["method"],
                   "total": it.get("total", 0),
                   "criteria": it.get("criteria", "")} for it in ITEMS]
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
.det{font-size:10.5px;color:#555;margin-top:3px;text-align:left;white-space:normal;line-height:1.4}
.ok{color:#2c8a41;font-weight:bold}.bad{color:#c02020;font-weight:bold}
.warn{color:#b57a00;font-weight:bold}
.na{color:#999}.man{color:#4a69b0}
.dot{display:inline-block;width:11px;height:11px;border-radius:50%;margin-right:4px;vertical-align:-1px}
.d-ok{background:#35a94e}.d-bad{background:#d93a3a}.d-na{background:#c2c2c2}.d-man{background:#6f8fd6}
.d-warn{background:#e8a01f}
#gate{position:fixed;inset:0;background:#2f5496;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px;color:#fff}
#gate input{font-size:22px;padding:8px 14px;border-radius:8px;border:0;width:130px;text-align:center;letter-spacing:6px}
.note{font-size:11px;color:#777;margin-top:10px;line-height:1.6}
.back{display:inline-block;margin-bottom:12px;background:#eef2f9;color:#2f5496;padding:6px 14px;border-radius:16px;text-decoration:none;font-size:13px;font-weight:bold}
</style></head><body>
<div id="gate"><h2>🔒 지점 점검 요약</h2><div>접속 번호를 입력하세요</div>
<input id="pin" type="password" maxlength="12" inputmode="numeric" autofocus></div>
<header><h1>📋 지점 점검 요약 (본부 공유용)</h1><div class="sub" id="gen"></div></header>
<div class="wrap">
<a class="back" href="hq.html">← 🏢 본부 허브</a>
<div class="cards" id="cards"></div>
<table id="tbl"></table>
<div class="note">· 이 페이지에는 수급자 개인정보가 포함되어 있지 않습니다 (지점 단위 집계만).<br>
· 상세 내역(수급자별)은 지점 점검 PC의 대시보드에서만 확인할 수 있습니다.<br>
· 상태 기준 — <span class="ok">양호</span>: 문제 0건 / <span class="warn">주의</span>: 자동확인 범위 밖 — 현장 확인요망(미흡 아님) / <span class="bad">미흡</span>: 문제 있음 / <span class="na">수집전</span>: 자동수집 미구현·미실행 / <span class="man">수기</span>: 현장 확인 항목</div>
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

// 지점별 채점 점수 (대시보드에서 "본부 공유" 버튼으로 업로드된 것)
const SCORES_HOOK = 'https://script.google.com/macros/s/AKfycbxCaMyM26xaLNlYUof-Jxac88jfWggzphLDvBEIRlDY-2Bn8S9wF5HWt52QupXWkxlO/exec?token=audit-scores-2026-cheongju';
let SCORES = {};
function drawCards(){
  const cards = document.getElementById('cards');
  cards.innerHTML = '';
  BR.forEach(b => {
    const d = DATA.branches[b];
    const s = SCORES[b];
    const el = document.createElement('div'); el.className='bcard';
    let scoreLine = '';
    if(s){
      const pct = s.total_earned;
      const cls = pct>=90?'ok':(pct>=70?'':'bad');
      scoreLine = `<div style="font-size:20px;margin:4px 0"><b class="${cls}">${s.total_earned}점</b>
        <span style="font-size:11px;color:#888">/ 100점 · 기준 ${s.filled}/${s.total_subs} 입력 · ${(s.saved_at||'').slice(0,10)} 채점</span></div>`;
    } else {
      scoreLine = `<div style="font-size:12px;color:#999;margin:4px 0">채점 미공유 (대시보드에서 📤 본부 공유 클릭)</div>`;
    }
    if(!d){ el.innerHTML = `<h3>${b}</h3>${scoreLine}<div class="meta">자동수집 전</div>`; cards.appendChild(el); return; }
    const c = d.counts;
    el.innerHTML = `<h3>${b}</h3>${scoreLine}<div class="meta">수집 ${d.run_at} · 기준일 ${d.cutoff} · ${d.people}명</div>
    <div class="nums">낙상↔욕구 불일치 <b class="bad">${c.불일치}</b>/${c.대조회차}회차<br>
    반기별 평가 누락 <b class="bad">${c.반기누락}</b>건 · 계획 문제 <b class="bad">${c.계획문제}</b>건</div>`;
    cards.appendChild(el);
  });
}
drawCards();
fetch(SCORES_HOOK).then(r=>r.json()).then(j=>{ if(j.ok){ SCORES=j.scores||{}; drawCards(); drawTable(); } }).catch(e=>{});
function cell(b, it){
  const d = DATA.branches[b];
  if(it.method==='manual') return '<span class="dot d-man"></span><span class="man">수기</span>';
  if(!d) return '<span class="dot d-na"></span><span class="na">-</span>';
  const r = d.item_results[String(it.no)];
  if(!r) return '<span class="dot d-na"></span><span class="na">수집전</span>';
  const det = r.detail ? `<div class="det">${r.detail}</div>` : '';
  if(r.status==='양호') return '<span class="dot d-ok"></span><span class="ok">양호</span>'+det;
  // '주의'(확인요망)를 미흡으로 칠하지 않는다 — 34③·18① 등은 '미흡 확정'이 아니라 수기 확인 대상이다.
  // (지점 대시보드 audit_dashboard.html 은 이미 주의를 노란불로 구분해 표시한다.)
  if(r.status==='주의') return '<span class="dot d-warn"></span><span class="warn">주의</span>'+det;
  return '<span class="dot d-bad"></span><span class="bad">미흡</span>'+det;
}
function scoreCell(b, it){
  const s = SCORES[b];
  if(!s || !s.per_item || !s.per_item[it.no]) return '';
  const p = s.per_item[it.no];
  if(p.filled === 0) return '';
  const cls = p.earned >= p.denom ? 'ok' : 'bad';
  return `<div style="font-size:11px;margin-top:2px"><b class="${cls}">${p.earned}</b><span style="color:#999">/${it.total||p.denom}점</span></div>`;
}
function drawTable(){
  let h = '<tr><th style="width:30px">#</th><th style="width:190px">항목</th><th style="width:44px">배점</th>' + BR.map(b=>`<th>${b}</th>`).join('') + '</tr>';
  DATA.items.forEach(it => {
    h += `<tr><td>${it.no}</td>
      <td class="name"><details><summary style="cursor:pointer"><b>${it.name}</b> <span style="color:#999;font-size:11px">${it.method==='manual'?'수기':'자동'}</span></summary>
        <div style="font-size:11px;color:#666;line-height:1.5;margin-top:4px;white-space:normal">${it.criteria}</div></details></td>
      <td>${it.total||'-'}</td>` + BR.map(b=>`<td>${cell(b,it)}${scoreCell(b,it)}</td>`).join('') + '</tr>';
  });
  document.getElementById('tbl').innerHTML = h;
}
drawTable();
</script></body></html>"""
    html = html.replace("__PAYLOAD__", payload).replace("__PIN__", PIN)
    OUT.write_text(html, encoding="utf-8")
    return OUT


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    p = generate()
    print("생성 완료:", p)
