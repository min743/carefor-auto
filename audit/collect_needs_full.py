# -*- coding: utf-8 -*-
"""욕구사정(case_YK) 전 항목 수집 — 체크 누락 점검용 (읽기 전용).

기존 스캔(audit_results/<지점>.json)에서 기간 내 욕구사정 보유자만 추려 상세 폼을 받아 파싱한다.
실행: py -X utf8 -m audit.collect_needs_full [지점키] [cutoff]
결과: audit_results/needs_full_<지점>.json
"""
from __future__ import annotations
import sys, json, time, re
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")
from playwright.sync_api import sync_playwright
from src.config import Config, config_path
from src.carefor_client import extract_g_pammgno, _navigate_spa
from audit.explore_pages import login
from audit.collector import patient_manage_url
from audit.needs_form import parse_needs_form

RES = Path(__file__).resolve().parent.parent / "audit_results"

LIST_JS = r"""
(() => [...document.querySelectorAll('table.frame_list_tbl tr.cr')].map(tr => {
  const tds = tr.querySelectorAll('td');
  const m = (tr.outerHTML.match(/pammgno['"=:\s]+(\d{5,})/) || [])[1];
  return { pammgno: m || null, status: tds[1] ? tds[1].textContent.trim() : '',
           name: tds[2] ? tds[2].textContent.trim().replace(/\s+/g,' ') : '' };
}).filter(r => r.pammgno && r.name))()
"""

FETCH_JS = r"""
async ([pammgno, years, cutoff]) => {
  const post = async (url, body) => (await fetch(url, {method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8','X-Requested-With':'XMLHttpRequest'},
    body, credentials:'include'})).text();

  const info = await post('/share/patient/html/view.patient_info.php', `pammgno=${pammgno}&inc_exit=1&tab_num=1`);
  const infoTxt = info.replace(/<[^>]+>/g,' ').replace(/\s+/g,' ');
  // "인정등급 변경/갱신 5등급 (2025.01.26 ~ 2027.01.25)"
  // 사이에 '만료임박'·'현재등급' 등 토큰이 끼어든다. 갱신등급이 뒤따르면 첫 매치(현재등급)를 취한다.
  const gm = infoTxt.match(/인정등급[\s\S]{0,40}?([1-5]등급|인지지원등급|등급외[가-힣A-Z]*)\s*\(\s*([\d.]+)\s*~\s*([\d.]+)\s*\)/);

  const cells = [];
  for (const yy of years) {
    const gh = await post('/share/patient/html/info.patient_case_tab.php', `pammgno=${pammgno}&tab_num=3&yy=${yy}`);
    const re = /<g-td([^>]*obj-type="openLayer"[^>]*)>([\s\S]*?)<\/g-td>/g;
    let m;
    while ((m = re.exec(gh))) {
      if (!/show\.case_YK/.test(m[1])) continue;
      const id = (m[1].match(/cykmgno':'(\d+)'/) || [])[1];
      const yym = (m[1].match(/yy':'(\d+)'/) || [])[1] || yy;
      const txt = m[2].replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').trim();
      const dm = txt.match(/(\d{4}\.\d{2}\.\d{2})/);
      if (id && dm && dm[1] >= cutoff) cells.push({id, yy: yym, date: dm[1], reason: (txt.match(/재사정|신규/)||[''])[0]});
    }
  }
  const seen = new Set();
  const uniq = cells.filter(c => !seen.has(c.id) && seen.add(c.id));
  const out = [];
  for (const c of uniq) {
    const html = await post('/layer/modal//share_layer/case/show.case_YK.php',
      `param=upd&pammgno=${pammgno}&cykmgno=${c.id}&yy=${c.yy}&cb=tab`);
    out.push({...c, html});
  }
  return { grade: gm ? gm[1] : '', gfrom: gm ? gm[2] : '', gto: gm ? gm[3] : '', assess: out };
}
"""


def flatten(secs: list[dict]) -> list[dict]:
    """[{sec, label, sel, text}] — 섹션 보존.

    신서식은 섹션마다 행 라벨이 똑같이 '판단근거'라서 섹션을 버리면 자원이용 판단근거를
    영양상태 판단근거로 잘못 읽는다.
    """
    return [
        {"sec": s["sec"], "label": r["label"], "sel": r["sel"],
         "free": r.get("free", []), "text": r["text"]}
        for s in secs for r in s["rows"]
    ]


def main():
    key = sys.argv[1] if len(sys.argv) > 1 else "청주"
    cutoff = sys.argv[2] if len(sys.argv) > 2 else "2024.07.31"
    cfg = Config.load(config_path())
    b = next(x for x in cfg.branches if key in x.name)

    print(f"지점 {b.name} | cutoff {cutoff} | 목록 전원 수집")

    years = [str(y) for y in range(2026, int(cutoff[:4]) - 1, -1)]
    people, t0 = [], time.time()
    with sync_playwright() as pw:
        browser, page = login(pw, b.ctmnumb)
        g = extract_g_pammgno(page)
        _navigate_spa(page, patient_manage_url(g))
        page.wait_for_timeout(2500)

        # "퇴소자 포함" 클릭이 타이밍상 실패하면 퇴소자가 통째로 누락된다(청주 95→50 사고).
        # 예전엔 base scan 의 want 명단과 90% 이상 매칭되는지로 확인했지만, want 프리필터를
        # 없애면서(아래 참조) 그 검증 기준도 사라졌다. → 체크박스가 실제 'on' 이고 목록이
        # 클릭 전보다 줄지 않았는지로 대신 확인하고, 실패하면 예외로 멈춘다(조용한 누락 금지).
        def click_include_exited():
            return page.evaluate("""(() => { const l=[...document.querySelectorAll('label')].find(e=>e.textContent.includes('퇴소자 포함')); if(!l) return 'no-label'; const i=l.querySelector('input'); if(!i) return 'no-input'; if(!i.checked) i.click(); return i.checked ? 'on' : 'off'; })()""")

        # ★ want(base scan 의 '기간내 욕구사정 보유자') 프리필터 제거 — 사용자 확정 2026-07-17
        #   ① base scan(audit_results/<지점>.json) 의존이 없어져 본 점검 '전' 에 돌 수 있다
        #      → CI 지점 job 안에서 34②③ 수집과 같은 자리에 넣을 수 있다.
        #   ② 욕구사정이 0건인 수급자가 분모에 들어온다 → 20① '연 1회 이상 실시' 판정 가능.
        #      want 는 사정 보유자만 뽑아서 '미실시자' 를 원리적으로 볼 수 없었다.
        n_before = len(page.evaluate(LIST_JS))
        lst = []
        for attempt in range(4):
            state = click_include_exited()
            page.wait_for_timeout(2500)
            lst = page.evaluate(LIST_JS)
            if state == "on" and len(lst) >= n_before:
                break
            print(f"  [재시도 {attempt+1}] 퇴소자 포함={state}, 목록 {len(lst)}명(클릭 전 {n_before}명) → 다시 시도")
            page.wait_for_timeout(1500)
        else:
            browser.close()
            raise RuntimeError(
                f"{b.name}: '퇴소자 포함' 반영 실패(상태={state}, 목록 {len(lst)}명/클릭 전 {n_before}명). 수집 중단.")
        n_exit = sum(1 for r in lst if "퇴소" in (r.get("status") or ""))
        print(f"목록 전원 {len(lst)}명 (퇴소 {n_exit}명, 클릭 전 {n_before}명)")
        if not n_exit:
            # 퇴소자가 정말 0명일 수도 있어 예외로 막진 않되, 조용히 넘어가진 않는다
            print("  ⚠️ 퇴소자 0명 — '퇴소자 포함' 이 실제 반영됐는지 확인 필요")

        for i, r in enumerate(lst, 1):
            res = page.evaluate(FETCH_JS, [r["pammgno"], years, cutoff])
            recs = []
            for c in res["assess"]:
                recs.append({"date": c["date"], "reason": c["reason"], "rows": flatten(parse_needs_form(c["html"]))})
            recs.sort(key=lambda x: x["date"])
            people.append({"name": r["name"], "pammgno": r["pammgno"], "status": r["status"], "grade": res["grade"],
                           "grade_from": res["gfrom"], "grade_to": res["gto"], "assess": recs})
            print(f"  [{i}/{len(lst)}] {r['name']} {res['grade']}({res['gfrom']}~{res['gto']}) — 사정 {len(recs)}건 ({time.time()-t0:.0f}s)")
        browser.close()

    out = {"branch": b.name, "cutoff": cutoff, "people": people}
    p = RES / f"needs_full_{b.name}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n저장: {p}  ({len(people)}명 / 사정 {sum(len(x['assess']) for x in people)}건, {time.time()-t0:.0f}초)")


if __name__ == "__main__":
    main()
