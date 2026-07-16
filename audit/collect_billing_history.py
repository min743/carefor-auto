# -*- coding: utf-8 -*-
"""7-1 본인부담금 청구서 발송이력 수집 — 항목 34③(급여제공기록지 월1회 제공) 판정용.

케어포 7-1 → '청구서 발송' 다이얼로그 → '청구서 발송이력'(서브레이어 cost_send_history,
month 파라미터). 월별로 로드해서 발송이력 표를 파싱해 전 행을 JSON 으로 저장한다.

이력표 열(g-td, 행당 12셀): 0연번 1수급자명 2등급 3금액 4발송일시 5발송방법
  6발송대상(보호자) 7수신정보 8미납액 [9]급여제공기록지 10전월입금내역 11청구상세내역
  → 9번 열 '포함'/'제외' = 급여제공기록지 발송 여부.

수집 로직은 audit/scan_billing_history.py(엑셀 발췌용)와 동일하다. 다만 이 모듈은
audit/item34.py 의 judge3() 가 읽는 JSON 을 만들고, CI 진입점에서 import 되는
경로에 미추적 모듈을 끌어들이지 않기 위해 별도 파일로 둔다.

실행: py -X utf8 -m audit.collect_billing_history 청주 [--from 202407 --to 202606]
출력: audit_results/청구발송_<지점명>.json
"""
import json
import sys
from datetime import date
from pathlib import Path

RES = Path(__file__).resolve().parent.parent / "audit_results"

# 이력표 한 달 파싱 → [{수급자,등급,발송일시,방법,수신자,기록지}]
PARSE_JS = r"""
(() => {
  const dt = /\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}/;
  // 발송이력 레이어 = 급여제공기록지 헤더 + 발송일시 데이터를 가진 보이는 컨테이너(가장 안쪽)
  let layers = Array.from(document.querySelectorAll('.modal,[class*=layer],[class*=popup],[class*=dialog]'))
    .filter(e => { const r=e.getBoundingClientRect(); return r.width>100&&r.height>100
        && (e.textContent||'').includes('급여제공기록지') && dt.test(e.textContent||''); });
  layers.sort((a,b)=>(a.textContent||'').length-(b.textContent||'').length);
  const scope = layers[0] || document;
  const cells = Array.from(scope.querySelectorAll('g-td[data-gt-row]'));
  const byRow = {};
  cells.forEach(c => { const r=c.getAttribute('data-gt-row'); (byRow[r]=byRow[r]||[]).push(c); });
  const rows = [];
  for (const r of Object.keys(byRow)) {
    const cs = byRow[r].sort((a,b)=>(+a.getAttribute('data-gt-col'))-(+b.getAttribute('data-gt-col')));
    const v = cs.map(c => (c.textContent||'').trim().replace(/\s+/g,' '));
    // 행 검증: 발송일시(col4) 날짜형 + 기록지(col9) 포함/제외
    if (v.length>=10 && dt.test(v[4]||'') && /(포함|제외)/.test(v[9]||''))
      rows.push({수급자:v[1], 등급:v[2], 발송일시:v[4], 방법:v[5], 수신자:v[6], 기록지:v[9]});
  }
  return {ok:rows.length>0, rows};
})()
"""


def month_range(frm: str, to: str) -> list[str]:
    y, m = int(frm[:4]), int(frm[4:])
    ey, em = int(to[:4]), int(to[4:])
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _close_layers(page):
    page.evaluate(r"""
      (() => {
        const btns = Array.from(document.querySelectorAll('.close, [class*=close], .btn_close, [obj-type=removeLayer], [onclick*=remove]'));
        const vis = e => { const r=e.getBoundingClientRect(); return r.width>0&&r.height>0; };
        const top = btns.filter(vis).pop();
        if (top) top.click();
      })()
    """)


def collect(page, months: list[str], progress=print) -> list[dict]:
    """열려있는 케어포 page 에서 월별 발송이력 전 행 수집. 7-1 이동은 호출측 책임."""
    from src.carefor_client import build_spa_hash, _navigate_spa, extract_g_pammgno

    g = extract_g_pammgno(page)
    h = build_spa_hash("left_sub7", "/share/cost/view.cost_master", "7-1.본인부담금 청구관리", g)
    _navigate_spa(page, f"https://dn.carefor.co.kr/#{h}")
    page.wait_for_timeout(4000)
    # '청구서 발송' 다이얼로그 열기
    page.evaluate("""(() => { const t=Array.from(document.querySelectorAll('button,.btn,a,span,div,li'))
        .find(e=>(e.textContent||'').trim()==='청구서 발송'); if(t)t.click(); })()""")
    page.wait_for_timeout(3500)

    hits = []
    for ym in months:
        loaded = page.evaluate(f"""
          (() => {{
            const el = Array.from(document.querySelectorAll('[obj-type=openLayer]'))
              .find(e => (e.getAttribute('page-info')||'').includes('cost_send_history'));
            if (!el) return false;
            el.setAttribute('param-info', "{{'type':'sub','month':'{ym}'}}");
            el.click();
            return true;
          }})()
        """)
        if not loaded:
            progress(f"  {ym}: 발송이력 트리거 없음 — 건너뜀")
            continue
        page.wait_for_timeout(3500)
        res = page.evaluate(PARSE_JS)
        rows = res.get("rows", []) if res.get("ok") else []
        for r in rows:
            r["청구년월"] = ym[:4] + "." + ym[4:]
            hits.append(r)
        excl = sum(1 for r in rows if "제외" in (r.get("기록지") or ""))
        progress(f"  {ym}: 이력 {len(rows)}행 (기록지 제외 {excl}건)")
        _close_layers(page)
        page.wait_for_timeout(1000)
    return hits


def save(branch_name: str, rows: list[dict], months: list[str]) -> Path:
    RES.mkdir(parents=True, exist_ok=True)
    out = RES / f"청구발송_{branch_name}.json"
    out.write_text(json.dumps(
        {"branch": branch_name, "collected": date.today().isoformat(),
         "months": months, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    from playwright.sync_api import sync_playwright
    from src.config import Config, config_path
    from audit.explore_pages import login

    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    key = argv[0] if argv else "청주"

    def opt(name, default):
        for i, a in enumerate(sys.argv):
            if a == name and i + 1 < len(sys.argv):
                return sys.argv[i + 1]
        return default

    frm = opt("--from", "202407")
    t = date.today()
    to = opt("--to", f"{t.year:04d}{t.month:02d}")
    months = month_range(frm, to)
    if "--test" in sys.argv:
        months = months[-2:]

    cfg = Config.load(config_path())
    b = next(x for x in cfg.branches if key in x.name)
    print(f"[{b.name}] 청구년월 {len(months)}개: {months[0]}~{months[-1]}")

    with sync_playwright() as pw:
        browser, page = login(pw, b.ctmnumb)
        rows = collect(page, months)
        browser.close()
    out = save(b.name, rows, months)
    print(f"저장: {out}  (총 {len(rows)}행)")


if __name__ == "__main__":
    main()
