# -*- coding: utf-8 -*-
"""기초평가 탭 그리드에서 회차별 낙상·욕창·인지·욕구 작성일 수집 (읽기 전용).

규칙: 낙상·욕창·인지를 먼저 작성해야 욕구사정을 쓸 수 있다.
     → 같은 회차에서 NS/YC/CM 작성일이 YK보다 늦으면 문제.

행 경계는 <g-th>(회차 번호). 그 안의 <g-td obj-type="openLayer">가 컬럼별 셀.
실행: py -X utf8 -m audit.collect_case_grid [지점키] [cutoff]
결과: audit_results/case_grid_<지점>.json
"""
from __future__ import annotations
import sys, json, re, time
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
from playwright.sync_api import sync_playwright
from src.config import Config, config_path
from src.carefor_client import extract_g_pammgno, _navigate_spa
from audit.explore_pages import login
from audit.collector import patient_manage_url

RES = Path(__file__).resolve().parent.parent / "audit_results"

LIST_JS = r"""
(() => [...document.querySelectorAll('table.frame_list_tbl tr.cr')].map(tr => {
  const tds = tr.querySelectorAll('td');
  const m = (tr.outerHTML.match(/pammgno['"=:\s]+(\d{5,})/) || [])[1];
  return { pammgno: m || null, status: tds[1]?tds[1].textContent.trim():'',
           name: tds[2] ? tds[2].textContent.trim().replace(/\s+/g,' ') : '' };
}).filter(r => r.pammgno && r.name))()
"""

GRID_JS = r"""
async ([pammgno, years]) => {
  const post = async (url, body) => (await fetch(url,{method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'},
    body, credentials:'include'})).text();
  const out = [];
  for (const yy of years) {
    const h = await post('/share/patient/html/info.patient_case_tab.php', `pammgno=${pammgno}&tab_num=3&yy=${yy}`);
    const bi = h.indexOf('<g-b'); if (bi < 0) continue;
    const body = h.slice(bi);
    // <g-th>회차</g-th> 로 행을 자른다
    const parts = body.split(/<g-th[^>]*>/).slice(1);
    for (const part of parts) {
      const seq = (part.match(/^([\s\S]*?)<\/g-th>/) || ['',''])[1].replace(/<[^>]+>/g,'').trim();
      const cells = [];
      const re = /<g-td([^>]*)>([\s\S]*?)<\/g-td>/g;
      let m;
      while ((m = re.exec(part))) {
        const view = (m[1].match(/view':'([^']+)'/) || [null,''])[1].split('/').pop();
        const txt = m[2].replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').trim();
        // 문서 고유번호(작성됐으면 값 있음, 미작성이면 ''). NS/YC/CM/YK 모두 뽑는다.
        const idm = m[1].match(/c[a-z]{2}mgno':'(\d*)'/);
        cells.push({ view, txt: txt.slice(0,60), open: /obj-type="openLayer"/.test(m[1]),
                     docid: idm ? idm[1] : null });
      }
      out.push({ yy, seq, cells });
    }
  }
  return out;
}
"""

KIND = {"show.case_NS": "낙상", "show.case_YC": "욕창", "show.case_CM": "인지", "show.case_YK": "욕구"}
DATE = re.compile(r"(\d{4}\.\d{2}\.\d{2})")


def summarize(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        rec = {"yy": r["yy"], "seq": r["seq"]}
        for c in r["cells"]:
            k = KIND.get(c["view"] or "")
            if not k:
                continue
            m = DATE.search(c["txt"])
            rec[k] = m.group(1) if m else ""
            rec[k + "_hasdoc"] = bool(c.get("docid"))  # 문서 고유번호 존재 = 실제 작성됨
            rec[k + "_txt"] = c["txt"]
        for k in KIND.values():
            rec.setdefault(k, "없음")
            rec.setdefault(k + "_hasdoc", False)
        out.append(rec)
    return out


def main():
    key = sys.argv[1] if len(sys.argv) > 1 else "청주"
    cutoff = sys.argv[2] if len(sys.argv) > 2 else "2024.07.31"
    cfg = Config.load(config_path())
    b = next(x for x in cfg.branches if key in x.name)

    prev = json.loads((RES / f"needs_full_{b.name}.json").read_text(encoding="utf-8"))
    want = {p["name"] for p in prev["people"]}
    years = [str(y) for y in range(2026, int(cutoff[:4]) - 1, -1)]

    people, t0 = [], time.time()
    with sync_playwright() as pw:
        browser, page = login(pw, b.ctmnumb)
        g = extract_g_pammgno(page)
        _navigate_spa(page, patient_manage_url(g))
        page.wait_for_timeout(2500)
        page.evaluate("""(() => { const l=[...document.querySelectorAll('label')].find(e=>e.textContent.includes('퇴소자 포함')); if(l){const i=l.querySelector('input'); if(i&&!i.checked)i.click();} })()""")
        page.wait_for_timeout(2500)
        lst = [r for r in page.evaluate(LIST_JS) if r["name"] in want]
        print(f"{b.name} — 대상 {len(lst)}명")
        for i, r in enumerate(lst, 1):
            raw = page.evaluate(GRID_JS, [r["pammgno"], years])
            people.append({"name": r["name"], "status": r["status"], "pammgno": r["pammgno"],
                           "rounds": summarize(raw)})
            if i % 20 == 0:
                print(f"  [{i}/{len(lst)}] {time.time()-t0:.0f}s")
        browser.close()

    p = RES / f"case_grid_{b.name}.json"
    p.write_text(json.dumps({"branch": b.name, "people": people}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {p} ({len(people)}명, {time.time()-t0:.0f}초)")


if __name__ == "__main__":
    main()
