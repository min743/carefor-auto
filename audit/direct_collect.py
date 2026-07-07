# 직접호출 수집 드라이버 + 소요시간. (검증용 — 기존 스캔 대체 안 함)
# 실행: py -X utf8 -m audit.direct_collect [지점명] [N명]
import sys, io, json, time
sys.stdout.reconfigure(encoding="utf-8")
from playwright.sync_api import sync_playwright
from src.config import Config, config_path
from src.carefor_client import extract_g_pammgno, _navigate_spa
from audit.explore_pages import login
from audit.collector import patient_manage_url

DIRECT_JS = (__import__("pathlib").Path(__file__).resolve().parent / "direct_scan.js").read_text(encoding="utf-8")

LIST_JS = r"""
(() => {
  const rows = [...document.querySelectorAll('table.frame_list_tbl tr.cr')];
  return rows.map(tr => {
    const tds = tr.querySelectorAll('td');
    const m = (tr.outerHTML.match(/pammgno['"=:\s]+(\d{5,})/) || [])[1];
    return { pammgno: m || null, status: tds[1] ? tds[1].textContent.trim() : '', name: tds[2] ? tds[2].textContent.trim().replace(/\s+/g,' ') : '' };
  }).filter(r => r.pammgno && r.name);
})()
"""

def main():
    key = sys.argv[1] if len(sys.argv) > 1 else "청주"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    cfg = Config.load(config_path())
    b = next(x for x in cfg.branches if key in x.name)
    from datetime import date
    years = [str(y) for y in range(date.today().year, 2023, -1)]  # 2026,2025,2024
    with sync_playwright() as pw:
        browser, page = login(pw, b.ctmnumb)
        g = extract_g_pammgno(page)
        _navigate_spa(page, patient_manage_url(g))
        page.wait_for_timeout(2500)
        page.evaluate("""(() => { const l=[...document.querySelectorAll('label')].find(e=>e.textContent.includes('퇴소자 포함')); if(l){const i=l.querySelector('input'); if(i&&!i.checked)i.click();} })()""")
        page.wait_for_timeout(2500)
        page.evaluate(DIRECT_JS)
        lst = page.evaluate(LIST_JS)
        print(f"수급자 {len(lst)}명 중 상위 {n}명 직접수집\n")
        out = []
        t0 = time.time()
        for r in lst[:n]:
            rt0 = time.time()
            rec = page.evaluate("([pm,nm,yy]) => window.__directCollect(pm, nm, {years: yy})", [r["pammgno"], r["name"], years])
            rec["status"] = r["status"]
            rec["_sec"] = round(time.time() - rt0, 2)
            out.append(rec)
        dt = time.time() - t0
        browser.close()
    io.open("audit_results/direct_sample.json", "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"=== {n}명 직접수집 완료 — 총 {dt:.1f}초 (명당 평균 {dt/n:.1f}초) ===\n")
    for rec in out:
        print(f"[{rec['name']}] {rec.get('status')}  셀{rec.get('_cells')}·대상{rec.get('_targets')}·{rec['_sec']}초")
        print(f"   enroll: {rec['enroll']}")
        print(f"   falls({len(rec['falls'])}): {[{'d':f['date'],'합계':f.get('total')} for f in rec['falls']]}")
        print(f"   sores({len(rec['sores'])}): {[s['date'] for s in rec['sores']]}")
        print(f"   cogs({len(rec['cogs'])}): {[{'d':c['date'],'총점':c.get('total')} for c in rec['cogs']]}")
        print(f"   needs({len(rec['needs'])}): {[{'d':nd['date'],'화장실':nd['toilet'],'영양':nd['nutrition']} for nd in rec['needs']]}")
        print(f"   plans({len(rec['plans'])}): {[{'작성':p['wd'],'기능회복':bool(p['rehabTxt'])} for p in rec['plans']]}")
        print()

if __name__ == "__main__":
    main()
