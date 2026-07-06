# -*- coding: utf-8 -*-
"""1-4 상담일지(항목 17①②) 수집·집계 단독 테스트."""
import sys
from datetime import date

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

from src.config import Config, config_path
from src.carefor_client import extract_g_pammgno
from .explore_pages import login
from .branch_pages import _goto, CLOSE_MODAL_JS, CONSULT_PARSE_JS


def main():
    branch = sys.argv[1] if len(sys.argv) > 1 else "천안점"
    cfg = Config.load(config_path())
    b = next(x for x in cfg.branches if branch in x.name)
    with sync_playwright() as p:
        browser, page = login(p, b.ctmnumb)
        g = extract_g_pammgno(page)
        _goto(page, "consult", g)
        page.evaluate(CLOSE_MODAL_JS)
        for y in range(2024, date.today().year + 1):
            page.evaluate(f"reloadPage({{'yy':'{y}','visit_type':'','include_serviceSupply':''}})")
            c = None
            for _ in range(8):
                page.wait_for_timeout(800)
                page.evaluate(CLOSE_MODAL_JS)
                c = page.evaluate(CONSULT_PARSE_JS)
                if c and c.get("rows"):
                    break
            if not c:
                print(f"{y}: 테이블 없음")
                continue
            csh = sum(r.get("csh") or 0 for r in c["rows"])
            pas = sum(r.get("pas") or 0 for r in c["rows"])
            print(f"{y}: 분기 {c['header']} · 수급자 {len(c['rows'])}행 · 상담 {pas}건 · 급여반영 {csh}건 · 배너 {c.get('banner')!r}")
            miss0 = [r["name"] for r in c["rows"] if r.get("q") and not r["q"][0]][:5]
            print(f"    1분기 미상담 후보(참고): {miss0}")
        browser.close()


if __name__ == "__main__":
    main()
