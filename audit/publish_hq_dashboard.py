# -*- coding: utf-8 -*-
"""본부 공유용 지점점검 대시보드 데이터 발행 — 이름(개인정보)만 제거, 내용은 그대로.

로컬 audit_results/dashboard_data.js (실명 포함) → docs/dashboard_data.js (이름 제거본)
  · 명단표(rows_match/halfyear_miss/order_issues/plan_issues/rows_check/rehab_miss):
    수급자 이름 칸(0번 컬럼)을 '○○○'로 마스킹
  · item_results[*].detail: 이름만 제거(건수·사유 유지), 안전 백스톱 포함
  · 이중 안전장치: 원본 명단에서 뽑은 '수급자 이름 집합'이 결과 어디에도 남지 않도록 검사

docs/branch_dashboard.html 이 이 파일을 읽어 본부 공유 대시보드로 표시.
실행: py -X utf8 -m audit.publish_hq_dashboard
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from .summary_page import clean_detail  # 이름 패턴 제거 + 흔적 남으면 통째 대체

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "audit_results" / "dashboard_data.js"
OUT = ROOT / "docs" / "dashboard_data.js"
DASH_SRC = ROOT / "audit_dashboard.html"
DASH_OUT = ROOT / "docs" / "branch_dashboard.html"
PIN = "15771389"

_GATE_HEAD = """<meta name="robots" content="noindex, nofollow">
<style>
#hqgate{position:fixed;inset:0;background:#2f5496;z-index:9999;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px;color:#fff}
#hqgate input{font-size:22px;padding:8px 14px;border-radius:8px;border:0;width:130px;text-align:center;letter-spacing:6px}
button[onclick^="uploadScores"]{display:none!important}
</style>
"""
_GATE_BODY = """<div id="hqgate"><h2 style="color:#fff">🔒 지점 점검 대시보드 (본부 공유)</h2>
<div>접속 번호를 입력하세요</div>
<input id="hqpin" type="password" maxlength="12" inputmode="numeric" autofocus></div>
<a href="hq.html" title="본부 공유 허브로" style="position:fixed;left:12px;bottom:12px;z-index:500;background:#2f5496;color:#fff;padding:9px 15px;border-radius:22px;text-decoration:none;font-size:13px;font-weight:bold;box-shadow:0 2px 10px rgba(0,0,0,.25)">← 허브</a>
"""
# 본부 공유 모드 스크립트: 게이트 + 업로드 비활성화 + 업로드된 점수 자동 로드(읽기 전용)
_HQ_SCRIPT = """<script>
(function(){
  var PIN='%s';
  var g=document.getElementById('hqgate');
  document.getElementById('hqpin').addEventListener('input',function(e){
    if(e.target.value===PIN){g.style.display='none';sessionStorage.setItem('ap','1');}
  });
  if(sessionStorage.getItem('ap')==='1') g.style.display='none';
  // 업로드 비활성화(본부가 눌러도 공유 점수 훼손 방지)
  window.uploadScores=function(){alert('본부 공유 페이지에서는 점수 업로드가 비활성화되어 있습니다.');};
  // 업로드된 점수/메모를 hook에서 불러와 표시(읽기 전용)
  try{
    fetch(SCORES_HOOK+(SCORES_HOOK.indexOf('?')>=0?'&':'?')+'token='+encodeURIComponent(SCORES_TOKEN))
      .then(function(r){return r.json();})
      .then(function(j){
        if(!j||!j.ok||!j.scores) return;
        Object.keys(j.scores).forEach(function(branch){
          var sc=j.scores[branch]; if(!sc) return; var m={};
          if(sc.sub_values){Object.keys(sc.sub_values).forEach(function(no){
            m[no]=m[no]||{}; m[no].subs={};
            Object.keys(sc.sub_values[no]).forEach(function(lab){m[no].subs[lab]=sc.sub_values[no][lab].v;});
          });}
          if(sc.memos){Object.keys(sc.memos).forEach(function(no){m[no]=m[no]||{}; m[no].memo=sc.memos[no];});}
          try{localStorage.setItem(manualKey(branch),JSON.stringify(m));}catch(e){}
        });
        render();
      }).catch(function(e){});
  }catch(e){}
})();
</script>
""" % PIN


def publish_dashboard_html():
    """원본 audit_dashboard.html → docs/branch_dashboard.html (본부 공유용) 변환 발행."""
    html = DASH_SRC.read_text(encoding="utf-8")
    html = html.replace('src="audit_results/dashboard_data.js"', 'src="dashboard_data.js"')
    html = html.replace("</head>", _GATE_HEAD + "</head>", 1)
    html = html.replace("<body>", "<body>" + _GATE_BODY, 1)
    # 주의: exportPDF() 문자열 안에도 </body>가 있으므로 반드시 '마지막' </body>에 주입
    idx = html.rfind("</body>")
    html = html[:idx] + _HQ_SCRIPT + html[idx:]
    DASH_OUT.write_text(html, encoding="utf-8")
    return DASH_OUT

MASK = "○○○"
# 명단표: 0번 컬럼이 수급자 이름 (구조 고정)
NAME_COL_ARRAYS_TOP = ["rows_match"]
NAME_COL_ARRAYS_AN = ["halfyear_miss", "order_issues", "plan_issues", "rows_check", "rehab_miss"]


def _load() -> dict:
    t = SRC.read_text(encoding="utf-8")
    m = re.search(r"window\.AUDIT_DATA = (\{.*?\});\s*\nwindow\.AUDIT_ITEMS = (\[.*\]);", t, re.S)
    if not m:
        raise SystemExit("dashboard_data.js 파싱 실패 (형식 확인)")
    return {"data": json.loads(m.group(1)), "items": json.loads(m.group(2))}


def _collect_names(data: dict) -> set[str]:
    """명단표 0번 컬럼 = 수급자 이름 집합 (2~4자 한글만 채택)."""
    names: set[str] = set()
    for b in data.values():
        for arr in NAME_COL_ARRAYS_TOP:
            for row in b.get(arr, []) or []:
                if row and isinstance(row[0], str) and re.fullmatch(r"[가-힣]{2,4}", row[0].strip()):
                    names.add(row[0].strip())
        an = b.get("analysis", {}) or {}
        for arr in NAME_COL_ARRAYS_AN:
            for row in an.get(arr, []) or []:
                if row and isinstance(row[0], str) and re.fullmatch(r"[가-힣]{2,4}", row[0].strip()):
                    names.add(row[0].strip())
    return names


_SKIP_KEYS = {"items", "item_results"}  # 항목 정의/판정 — 항목명(욕구사정 등)은 사람이름 아님


def _names_from_json(obj, acc: set[str]) -> None:
    """audit_results/*.json 을 재귀 순회하며 사람 'name' 필드만 수집(수급자+직원 완전 커버)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _SKIP_KEYS:
                continue
            if k == "name" and isinstance(v, str) and re.fullmatch(r"[가-힣]{2,4}", v.strip()):
                acc.add(v.strip())
            else:
                _names_from_json(v, acc)
    elif isinstance(obj, list):
        for x in obj:
            _names_from_json(x, acc)


def _collect_all_person_names(data: dict) -> set[str]:
    """명단표(수급자) + audit_results/*.json의 모든 name필드(직원 포함) 합집합."""
    names = _collect_names(data)
    for f in (ROOT / "audit_results").glob("*.json"):
        try:
            _names_from_json(json.loads(f.read_text(encoding="utf-8")), names)
        except Exception:
            continue
    return names


def _name_rx(names: set[str]):
    """알려진 이름들을 하나의 결합 정규식으로 (긴 이름 우선, 한글 경계)."""
    if not names:
        return None
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![가-힣])(?:{alt})(?![가-힣])")


def _mask_known(s: str, rx) -> str:
    """문자열에 남아있는 알려진 수급자 이름을 마스킹 (이수→이수율 오치환 방지)."""
    if not s or rx is None:
        return s
    return rx.sub(MASK, s)


def _mask_array(rows, rx):
    out = []
    for row in rows or []:
        r = list(row)
        if r and isinstance(r[0], str) and re.fullmatch(r"[가-힣]{2,4}", r[0].strip()):
            r[0] = MASK
        # 다른 컬럼에 남은 알려진 이름도 마스킹 + HTML 이스케이프(대시보드가 innerHTML로 삽입)
        r = [html.escape(_mask_known(c, rx)) if isinstance(c, str) else c for c in r]
        out.append(r)
    return out


def sanitize(payload: dict):
    data = payload["data"]
    names = _collect_all_person_names(data)  # 수급자 + 직원 이름 완전 집합
    rx = _name_rx(names)
    for b in data.values():
        # 명단표 마스킹
        for arr in NAME_COL_ARRAYS_TOP:
            if b.get(arr):
                b[arr] = _mask_array(b[arr], rx)
        an = b.get("analysis", {}) or {}
        for arr in NAME_COL_ARRAYS_AN:
            if an.get(arr):
                an[arr] = _mask_array(an[arr], rx)
        # item_results detail: 이름 제거 + (알려진 수급자명 남으면 통째 대체)
        for r in (b.get("item_results") or {}).values():
            if isinstance(r, dict) and r.get("detail"):
                d = clean_detail(r["detail"])
                if rx and rx.search(d):
                    d = "[상세 명단은 지점 대시보드에서 확인]"
                r["detail"] = html.escape(d)
        # analysis.item_results 도 동일 처리(있으면)
        for r in (an.get("item_results") or {}).values():
            if isinstance(r, dict) and r.get("detail"):
                r["detail"] = html.escape(clean_detail(r["detail"]))
    return payload, names


def verify(payload: dict, names: set[str]) -> list[str]:
    """결과 전체 문자열에서 알려진 수급자 이름이 남아있는지 검사."""
    blob = json.dumps(payload["data"], ensure_ascii=False)
    rx = _name_rx(names)
    return sorted(set(rx.findall(blob))) if rx else []


def main():
    payload = _load()
    payload, names = sanitize(payload)
    hits = verify(payload, names)
    if hits:
        raise SystemExit(f"❌ 살균 실패 — 잔여 이름 {len(hits)}건: {hits[:10]}")
    js = ("// 본부 공유용 — 이름(개인정보) 제거본. 원본은 지점 PC audit_results/ 에만 존재.\n"
          "window.AUDIT_DATA = " + json.dumps(payload["data"], ensure_ascii=False) + ";\n"
          "window.AUDIT_ITEMS = " + json.dumps(payload["items"], ensure_ascii=False) + ";\n")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(js, encoding="utf-8")
    dash = publish_dashboard_html()
    print(f"발행 완료: {OUT}")
    print(f"발행 완료: {dash}")
    print(f"수급자 이름 {len(names)}명 마스킹 · 잔여 이름 0건 ✅")


if __name__ == "__main__":
    main()
