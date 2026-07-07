# 직접호출 vs 기존 스캔 결과 대조 검증 (같은 수급자 N명)
# 실행: py -X utf8 -m audit.verify_direct [지점명] [N]
import sys, io, json, time
sys.stdout.reconfigure(encoding="utf-8")
from playwright.sync_api import sync_playwright
from src.config import Config, config_path
from src.carefor_client import extract_g_pammgno, _navigate_spa
from audit.explore_pages import login
from audit.collector import patient_manage_url, run_branch_audit
from pathlib import Path
from datetime import date

DIRECT_JS = (Path(__file__).resolve().parent / "direct_scan.js").read_text(encoding="utf-8")
LIST_JS = r"""(() => [...document.querySelectorAll('table.frame_list_tbl tr.cr')].map(tr => {
  const tds = tr.querySelectorAll('td'); const m=(tr.outerHTML.match(/pammgno['"=:\s]+(\d{5,})/)||[])[1];
  return {pammgno:m||null, status:tds[1]?tds[1].textContent.trim():'', name:tds[2]?tds[2].textContent.trim().replace(/\s+/g,' '):''};
}).filter(r=>r.pammgno&&r.name))()"""

def fall_map(recs): return {f["date"]: f.get("total") for f in recs}
def cog_map(recs): return {c["date"]: c.get("total") for c in recs}
def needs_map(recs): return {n["date"]: (n.get("toilet"), n.get("nutrition"), n.get("sit"), n.get("tr")) for n in recs}
def sore_set(recs): return set(s["date"] for s in recs)
def plan_set(recs): return set((p.get("wd"), bool(p.get("rehabTxt"))) for p in recs)

def main():
    key = sys.argv[1] if len(sys.argv) > 1 else "청주"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    cfg = Config.load(config_path())
    b = next(x for x in cfg.branches if key in x.name)

    print(f"=== 1) 기존 스캔 (limit={n}) ===")
    cur = run_branch_audit(b.ctmnumb, b.name, limit=n, save=False, progress_cb=lambda m: None)
    cur_recs = {r["name"]: r for r in cur["raw"]}
    print(f"   {len(cur_recs)}명 수집: {list(cur_recs)}")

    print(f"\n=== 2) 직접호출 (같은 {n}명) ===")
    years = [str(y) for y in range(date.today().year, 2023, -1)]
    with sync_playwright() as pw:
        browser, page = login(pw, b.ctmnumb)
        g = extract_g_pammgno(page)
        _navigate_spa(page, patient_manage_url(g))
        page.wait_for_timeout(2500)
        page.evaluate("""(() => { const l=[...document.querySelectorAll('label')].find(e=>e.textContent.includes('퇴소자 포함')); if(l){const i=l.querySelector('input'); if(i&&!i.checked)i.click();} })()""")
        page.wait_for_timeout(2500)
        page.evaluate(DIRECT_JS)
        lst = page.evaluate(LIST_JS)[:n]
        dir_recs = {}
        for r in lst:
            rec = page.evaluate("([pm,nm,yy]) => window.__directCollect(pm, nm, {years: yy})", [r["pammgno"], r["name"], years])
            dir_recs[r["name"]] = rec
        browser.close()

    print(f"\n=== 3) 대조 결과 ===")
    allok = True
    for name in cur_recs:
        c = cur_recs[name]; d = dir_recs.get(name)
        if not d:
            print(f"[{name}] 직접수집 없음 ❌"); allok = False; continue
        checks = {
            "falls(합계)": (fall_map(c.get("falls", [])), fall_map(d.get("falls", []))),
            "sores(날짜)": (sore_set(c.get("sores", [])), sore_set(d.get("sores", []))),
            "cogs(총점)": (cog_map(c.get("cogs", [])), cog_map(d.get("cogs", []))),
            "needs": (needs_map(c.get("needs", [])), needs_map(d.get("needs", []))),
            "plans": (plan_set(c.get("plans", [])), plan_set(d.get("plans", []))),
        }
        print(f"\n[{name}]")
        for k, (cv, dv) in checks.items():
            ok = cv == dv
            if not ok: allok = False
            print(f"   {k}: {'✅ 일치' if ok else '❌ 불일치'}")
            if not ok:
                print(f"      기존: {cv}")
                print(f"      직접: {dv}")
    print(f"\n{'✅ 전체 일치' if allok else '❌ 불일치 항목 있음 — 위 참조'}")

if __name__ == "__main__":
    main()
