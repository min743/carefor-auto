# -*- coding: utf-8 -*-
"""8-5 사례관리 회의록(항목 29) 수집·파싱 단독 테스트."""
import sys
from datetime import date

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

from src.config import Config, config_path
from src.carefor_client import extract_g_pammgno
from .explore_pages import login
from .branch_pages import _goto, CLOSE_MODAL_JS, GET_TEXT_JS, parse_case


def main():
    branch = sys.argv[1] if len(sys.argv) > 1 else "천안점"
    cfg = Config.load(config_path())
    b = next(x for x in cfg.branches if branch in x.name)
    with sync_playwright() as p:
        browser, page = login(p, b.ctmnumb)
        g = extract_g_pammgno(page)
        _goto(page, "case", g)
        page.evaluate(CLOSE_MODAL_JS)
        for y in range(2024, date.today().year + 1):
            page.evaluate(f"reloadPage({{'yy':'{y}'}})")
            txt = ""
            for _ in range(8):
                page.wait_for_timeout(800)
                page.evaluate(CLOSE_MODAL_JS)
                txt = page.evaluate(GET_TEXT_JS)
                if "실시 주기" in txt:
                    break
            c = parse_case(txt)
            print(f"{y}: 회의 {c['meeting']} · 반영 {c['reflect']} · 평가 {c['evaluate']} · 행 {len(c['rows'])}건")
            for r in c["rows"][:4]:
                print(f"    {r}")
        browser.close()


if __name__ == "__main__":
    main()
