# -*- coding: utf-8 -*-
"""1-2 결과평가 집계(34①) + 3-1-3 안전관리 검색(19③) 단독 테스트."""
import sys
from datetime import date

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

from src.config import Config, config_path
from src.carefor_client import extract_g_pammgno
from .explore_pages import login
from .branch_pages import _goto, CLOSE_MODAL_JS, GET_TEXT_JS, EVAL12_JS, parse_bigo


def main():
    branch = sys.argv[1] if len(sys.argv) > 1 else "천안점"
    cfg = Config.load(config_path())
    b = next(x for x in cfg.branches if branch in x.name)
    today = date.today()
    with sync_playwright() as p:
        browser, page = login(p, b.ctmnumb)
        g = extract_g_pammgno(page)

        _goto(page, "casetotal", g)
        page.evaluate(CLOSE_MODAL_JS)
        for y in range(2024, today.year + 1):
            d = f"{y}1231" if y < today.year else today.strftime("%Y%m%d")
            page.evaluate(f"reloadPage({{'date':'{d}'}})")
            page.wait_for_timeout(2500)
            page.evaluate(CLOSE_MODAL_JS)
            ev = page.evaluate(EVAL12_JS)
            print(f"{y} 결과평가: {ev}")

        _goto(page, "bigo", g)
        page.evaluate(CLOSE_MODAL_JS)
        for y in range(2024, today.year + 1):
            e_d = f"{y}1231" if y < today.year else today.strftime("%Y%m%d")
            page.evaluate(f"document.querySelector('#id_sdate').value='{y}0101';"
                          f"document.querySelector('#id_edate').value='{e_d}';"
                          "document.querySelector('input[name=cdssnch]').value='안전관리';"
                          "load_contents_form('carebigoInquiry')")
            page.wait_for_timeout(3000)
            rows = parse_bigo(page.evaluate(GET_TEXT_JS))
            names = sorted({r["name"] for r in rows})
            print(f"{y} 안전관리 입력: 기록 {len(rows)}건 · 수급자 {len(names)}명 → {names[:8]}")
        browser.close()


if __name__ == "__main__":
    main()
