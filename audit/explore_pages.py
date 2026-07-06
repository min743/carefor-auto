# -*- coding: utf-8 -*-
"""케어포 페이지 구조 탐색 도구 (그룹 A·D 구현용).

로그인 → 좌측 메뉴 전체 덤프(뷰 경로 확인) → 지정 페이지 이동 → DOM/XHR 구조 저장.

사용:
  py -X utf8 -m audit.explore_pages --branch 천안점 --menu     # 메뉴 트리만 덤프
  py -X utf8 -m audit.explore_pages --branch 천안점 --page 8-7 # 해당 페이지 구조 덤프
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

from src import credentials
from src.config import Config, config_path

PORTAL_URL = "https://eform.caring.co.kr/carefor"
OUT_DIR = Path(__file__).resolve().parent.parent / "audit_results" / "explore"


def login(p, ctmnumb: str, headless: bool = True):
    portal_id, portal_pw = credentials.get_portal_credentials()
    browser = p.chromium.launch(headless=headless)
    ctx = browser.new_context(http_credentials={"username": portal_id, "password": portal_pw})
    portal_page = ctx.new_page()
    print("포털 로그인 중...")
    portal_page.goto(PORTAL_URL, wait_until="domcontentloaded")
    portal_page.wait_for_function("typeof login2 === 'function'", timeout=15000)
    with ctx.expect_page(timeout=60000) as new_page_info:
        portal_page.evaluate(f"login2('{ctmnumb}')")
    page = new_page_info.value
    page.wait_for_load_state("domcontentloaded", timeout=30000)
    page.wait_for_load_state("networkidle", timeout=30000)
    return browser, page


def dump_menu(page) -> list[dict]:
    """메뉴로 보이는 요소('N-N.' 텍스트 패턴)의 outerHTML 덤프 + 전역 메뉴 데이터 탐색."""
    return page.evaluate(
        """
        (() => {
          const out = { items: [], globals: [], iframes: [] };
          const re = /^\\d{1,2}-\\d{1,2}/;
          const seen = new Set();
          document.querySelectorAll('a, li, div, span, td').forEach(el => {
            const txt = (el.textContent || '').trim().replace(/\\s+/g, ' ');
            if (re.test(txt) && txt.length < 40 && !seen.has(txt)) {
              // 가장 안쪽 요소만 (자식에 같은 텍스트 없는 것)
              const hasChildSame = Array.from(el.children).some(c => (c.textContent||'').trim().replace(/\\s+/g,' ') === txt);
              if (hasChildSame) return;
              seen.add(txt);
              out.items.push({ text: txt, html: el.outerHTML.slice(0, 500) });
            }
          });
          // 전역 변수 중 menu 관련
          for (const k of Object.keys(window)) {
            if (/menu|Menu|nav/.test(k)) {
              try {
                const v = window[k];
                const s = typeof v === 'string' ? v : JSON.stringify(v);
                if (s && s.length > 10) out.globals.push({ key: k, sample: s.slice(0, 400) });
              } catch (e) {}
            }
          }
          document.querySelectorAll('iframe').forEach(f => out.iframes.push(f.src || f.id || 'inline'));
          return out;
        })()
        """
    )


PAGES = {
    "8-7":   ("left_sub8", "/share/staff/view.staff_education", "8-7.교육일지"),
    "8-7-1": ("left_sub8", "/share/staff/view.staff_refresher_training", "8-7-1.요양보호사 보수교육"),
    "6-3":   ("left_sub6", "/share/safe/view.regularly_check", "6-3.정기점검"),
    "6-2":   ("left_sub6", "/share/safe/view.daily_check", "6-2.일일점검"),
    "1-4":   ("left_sub1", "/share/patient/view.patient_consult", "1-4.상담일지"),
    "1-6":   ("left_sub1", "/patient/view.patient_guide", "1-6.수급자 안내사항/예방접종"),
    "5-1":   ("left_sub5", "/share/program/view.program_service_daily", "5-1.프로그램 제공기록"),
    "5-5":   ("left_sub5", "/share/program/view.program_evaluation", "5-5.프로그램 의견수렴 및 반영"),
    "5-6":   ("left_sub5", "/share/program/view.program_annual_plan_sep", "5-6.프로그램 계획"),
    "5-8":   ("left_sub5", "/share/program/view.program_service_yearly", "5-8.프로그램 제공기록 리포트(운영기록지)"),
    "8-10":  ("left_sub8", "/share/staff/view.staff_yearly_report", "8-10.건강검진관리"),
    "2-4":   ("left_sub2", "/transport/view.transport_car_manage", "2-4.차량관리"),
    "9-1":   ("left_sub9", "/basic/view.center_master", "9-1.시설정보설정"),
    "8-1-1": ("left_sub8", "/share/staff/view.welfare_reward_manage", "8-1-1.복지(포상) 제공대장 관리"),
    "8-5":   ("left_sub8", "/share/patient/view.patient_case_meeting_tab", "8-5.사례관리 회의록"),
    "1-10":  ("left_sub1", "/share/patient/view.patient_connection_send_report", "1-10.연계기록지 발송 리포트"),
    "3-1":   ("left_sub3", "/share/care/view.care_service_weekly", "3-1.요양급여 제공 기록"),
    "3-1-3": ("left_sub3", "/share/care/view.care_service_bigo_all", "3-1-3.요양급여 특이사항 관리"),
    "3-2":   ("left_sub3", "/share/care/view.status_change_report", "3-2.상태변화 기록"),
    "1-2":   ("left_sub1", "/patient/view.patient_case_total", "1-2.전체 기초평가 현황"),
    "1-3":   ("left_sub1", "/share/patient/view.patient_case", "1-3.기초평가 관리"),
}


def dump_page(page, key: str, g_pammgno: str) -> None:
    from src.carefor_client import build_spa_hash, _navigate_spa

    type_, view, title = PAGES[key]
    h = build_spa_hash(type_, view, title, g_pammgno)
    print(f"[{key}] {title} 이동...")
    _navigate_spa(page, f"https://dn.carefor.co.kr/#{h}")
    page.wait_for_timeout(4000)

    # 본문 HTML 저장 (분석용)
    html = page.evaluate("document.body.innerHTML")
    out_html = OUT_DIR / f"page_{key}.html"
    out_html.write_text(html, encoding="utf-8")

    # 화면 요약: 보이는 텍스트 구조
    summary = page.evaluate(
        """
        (() => {
          const vis = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
          const out = { title: document.title, buttons: [], selects: [], tabs: [], gridHead: [], gridSample: [] };
          document.querySelectorAll('button, input[type=button], .btn, [class*=btn]').forEach(el => {
            const t = (el.textContent || el.value || '').trim();
            if (t && t.length < 20 && vis(el) && !out.buttons.includes(t)) out.buttons.push(t);
          });
          document.querySelectorAll('select').forEach(el => {
            if (!vis(el)) return;
            out.selects.push(Array.from(el.options).slice(0, 15).map(o => o.textContent.trim()).join('|'));
          });
          document.querySelectorAll('[class*=month], [class*=tab], [class*=year]').forEach(el => {
            const t = (el.textContent || '').trim().replace(/\\s+/g, ' ');
            if (t && t.length < 30 && vis(el) && !out.tabs.includes(t)) out.tabs.push(t);
          });
          // g-b 그리드 (기존 패턴) + 일반 테이블 헤더
          document.querySelectorAll('table').forEach(tb => {
            if (!vis(tb)) return;
            const ths = Array.from(tb.querySelectorAll('th')).map(x => x.textContent.trim()).filter(Boolean);
            if (ths.length) out.gridHead.push(ths.join(' | '));
            const tr = tb.querySelector('tbody tr');
            if (tr) out.gridSample.push(Array.from(tr.children).map(x => x.textContent.trim().slice(0, 25)).join(' | '));
          });
          document.querySelectorAll('.G-B, .g-b').forEach((gb, i) => {
            if (i > 2 || !vis(gb)) return;
            out.gridSample.push('G-B: ' + Array.from(gb.children).slice(0, 20).map(x => (x.className || '') + ':' + x.textContent.trim().slice(0, 15)).join(' / '));
          });
          return out;
        })()
        """
    )
    out_json = OUT_DIR / f"page_{key}_summary.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{key}] HTML {len(html)}자 → {out_html.name}, 요약 → {out_json.name}")
    print(json.dumps(summary, ensure_ascii=False, indent=1)[:1800])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", default="천안점")
    ap.add_argument("--menu", action="store_true")
    ap.add_argument("--page", help="8-7 | 8-7-1 | 6-3 | 6-2")
    ap.add_argument("--scrape", action="store_true", help="그룹 A·D 수집+판정 테스트")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    cfg = Config.load(config_path())
    b = next(x for x in cfg.branches if args.branch in x.name)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser, page = login(p, b.ctmnumb, headless=not args.headed)

        if args.scrape:
            from datetime import date
            from src.carefor_client import extract_g_pammgno
            from .items import BRANCH_CUTOFFS
            from .branch_pages import scrape_branch_pages, analyze_branch_pages
            g = extract_g_pammgno(page)
            cutoff = BRANCH_CUTOFFS.get(b.name, "2024.01.01")
            years = list(range(int(cutoff[:4]), date.today().year + 1))
            data = scrape_branch_pages(page, g, years, cutoff=cutoff)
            (OUT_DIR / "branch_pages_raw.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            res = analyze_branch_pages(data, cutoff)
            print(json.dumps(res["item_results"], ensure_ascii=False, indent=1))
            (OUT_DIR / "branch_pages_analysis.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

        if args.page:
            from src.carefor_client import extract_g_pammgno
            g = extract_g_pammgno(page)
            for key in args.page.split(","):
                dump_page(page, key.strip(), g)

        if args.menu:
            menu = dump_menu(page)
            out = OUT_DIR / "menu_dump.json"
            out.write_text(json.dumps(menu, ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"메뉴 항목 {len(menu['items'])}개, 전역 {len(menu['globals'])}개, iframe {len(menu['iframes'])}개 → {out}")
            for m in menu["items"]:
                if any(k in m["text"] for k in ("8-7", "6-3", "교육", "점검", "소독")):
                    print(" *", m["text"])
                    print("   ", m["html"][:300])

        browser.close()


if __name__ == "__main__":
    main()
