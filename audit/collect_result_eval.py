# -*- coding: utf-8 -*-
"""급여제공결과평가(serviceEvaluating) 상세 HTML 전량 다운로드 (읽기 전용).

기초평가 탭 그리드(tab_num=3, 연도별 yy 로드)에서 결과평가 셀을 찾아
상세 팝업 HTML을 직접 fetch로 받아 수급자별 폴더에 저장한다.
저장 후 리스트 상단 인원수(전체:N명)와 수집 명단을 대조 점검한다.

실행: py -X utf8 -m audit.collect_result_eval [지점키=청주] [--limit N] [--from 2024] [--probe]
결과: audit_results/결과평가_<지점>/<수급자명_pammgno>/ 아래 HTML + index.json
"""
from __future__ import annotations
import sys, json, re, time, shutil, argparse
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

# 리스트 상단 '전체:N명 / 남자:N명 / 여자:N명' 표시 추출
HEADER_JS = r"""
(() => {
  const out = {};
  document.querySelectorAll('span').forEach(el => {
    const t = (el.textContent || '').replace(/\s+/g, '').trim();
    let m;
    if ((m = t.match(/^전체:(\d+)명$/))) out.total = +m[1];
    else if ((m = t.match(/^남자:(\d+)명$/))) out.male = +m[1];
    else if ((m = t.match(/^여자:(\d+)명$/))) out.female = +m[1];
  });
  return out;
})()
"""

# 그리드에서 결과평가(serviceEvaluating) 셀만 뽑아 상세 HTML까지 fetch.
# probe=true 면 그리드의 모든 openLayer 셀 attrs 도 함께 반환(구조 확인용).
EVAL_JS = r"""
async ([pammgno, years, probe]) => {
  const post = async (url, body) => (await fetch(url,{method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'},
    body, credentials:'include'})).text();
  const cells = [], rawViews = [];
  const seen = new Set();
  for (const yy of years) {
    const gh = await post('/share/patient/html/info.patient_case_tab.php', `pammgno=${pammgno}&tab_num=3&yy=${yy}`);
    const re = /<g-td([^>]*obj-type="openLayer"[^>]*)>([\s\S]*?)<\/g-td>/g;
    let m;
    while ((m = re.exec(gh))) {
      const attrs = m[1];
      const vm = attrs.match(/view':'([^']+)'/);
      const rawView = vm ? vm[1] : '';
      if (probe && !rawViews.some(v => v.view === rawView)) rawViews.push({ view: rawView, attrs: attrs.slice(0, 400) });
      if (!/serviceEvaluating/i.test(rawView)) continue;
      const idm = attrs.match(/'(c[a-z]{2,3}mgno)':'(\d+)'/);
      if (!idm) continue;
      const key = rawView + '|' + idm[2];
      if (seen.has(key)) continue;
      seen.add(key);
      const yym = attrs.match(/yy':'(\d+)'/);
      const txt = m[2].replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
      const dm = txt.match(/(\d{4}\.\d{2}\.\d{2})/);
      cells.push({ view: rawView, idkey: idm[1], id: idm[2],
                   yy: yym ? yym[1] : yy, date: dm ? dm[1] : '', text: txt.slice(0, 60) });
    }
  }
  const out = [];
  for (const c of cells) {
    const url = '/layer/modal//' + c.view + '.php';
    const body = `param=upd&pammgno=${pammgno}&${c.idkey}=${c.id}&yy=${c.yy}&cb=tab`;
    const html = await post(url, body);
    out.push({ ...c, html });
  }
  return { evals: out, rawViews };
}
"""

SAFE = re.compile(r'[\\/:*?"<>|]')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("branch", nargs="?", default="청주")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--from", dest="year_from", type=int, default=2024)
    ap.add_argument("--probe", action="store_true", help="셀 구조 덤프(첫 인원만 rawViews 출력)")
    args = ap.parse_args()

    cfg = Config.load(config_path())
    b = next(x for x in cfg.branches if args.branch in x.name)
    # 그리드는 1년 앞까지 로드 (이전 회차에 기록된 당해년 작성분 누락 방지, 날짜 필터는 year_from 기준)
    years = [str(y) for y in range(2026, args.year_from - 2, -1)]

    outdir = RES / f"결과평가_{b.name}"
    if not args.limit and outdir.exists():  # 전체 수집 시 이전 다운로드 캐시 재생성
        shutil.rmtree(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    index, t0 = [], time.time()
    with sync_playwright() as pw:
        browser, page = login(pw, b.ctmnumb)
        g = extract_g_pammgno(page)
        _navigate_spa(page, patient_manage_url(g))
        page.wait_for_timeout(2500)
        page.evaluate("""(() => { const l=[...document.querySelectorAll('label')].find(e=>e.textContent.includes('퇴소자 포함')); if(l){const i=l.querySelector('input'); if(i&&!i.checked)i.click();} })()""")
        page.wait_for_timeout(2500)
        header = page.evaluate(HEADER_JS)
        roster = page.evaluate(LIST_JS)
        lst = roster[: args.limit] if args.limit else roster
        print(f"{b.name} — 상단표시 전체:{header.get('total','?')}명, 목록 {len(roster)}명, 연도 {years}")

        for i, r in enumerate(lst, 1):
            res = page.evaluate(EVAL_JS, [r["pammgno"], years, args.probe and i == 1])
            if args.probe and i == 1:
                print("=== openLayer 셀 종류 (첫 인원) ===")
                for v in res["rawViews"]:
                    print(" ", v["view"])
                    print("   ", v["attrs"][:300])
            pdir = outdir / SAFE.sub("_", f"{r['name']}_{r['pammgno']}")
            for e in res["evals"]:
                # 2024년 이후 작성분만 (셀 날짜 기준; 날짜 없으면 그리드 연도 기준)
                y = int(e["date"][:4]) if e["date"] else int(e["yy"] or 0)
                if y and y < args.year_from:
                    continue
                pdir.mkdir(exist_ok=True)
                fname = SAFE.sub("_", f"결과평가_{e['date'] or e['yy']}_{e['id']}.html")
                (pdir / fname).write_text(e["html"], encoding="utf-8")
                index.append({"name": r["name"], "status": r["status"], "pammgno": r["pammgno"],
                              "date": e["date"], "yy": e["yy"], "id": e["id"], "idkey": e["idkey"],
                              "text": e["text"], "file": f"{pdir.name}/{fname}"})
            if i % 20 == 0 or i == len(lst):
                print(f"  [{i}/{len(lst)}] 누적 {len(index)}건, {time.time()-t0:.0f}s")
        browser.close()

    # 명단 대조 점검: 상단 표시 인원수 vs 수집 목록
    scanned = {p["pammgno"] for p in lst}
    with_eval = {e["pammgno"] for e in index}
    check = {
        "상단표시_전체": header.get("total"),
        "목록_인원": len(roster),
        "스캔_인원": len(lst),
        "평가보유_인원": len(with_eval),
        "일치": header.get("total") == len(roster) == len(lst),
    }
    (outdir / "index.json").write_text(json.dumps(
        {"branch": b.name, "header": header, "check": check,
         "roster": roster, "evals": index}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {outdir} ({len(index)}건, {time.time()-t0:.0f}초)")
    print(f"점검: 상단표시 {check['상단표시_전체']}명 / 목록 {check['목록_인원']}명 / 스캔 {check['스캔_인원']}명 "
          f"/ 평가보유 {check['평가보유_인원']}명 → {'일치' if check['일치'] else '불일치!'}")
    if not check["일치"]:
        missing = [p["name"] for p in roster if p["pammgno"] not in scanned]
        if missing:
            print(f"  미스캔: {missing}")


if __name__ == "__main__":
    main()
