# -*- coding: utf-8 -*-
"""8-10 '입사전 건강검진 제출' 탭 구조 탐색."""
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

from src.config import Config, config_path
from src.carefor_client import extract_g_pammgno
from .explore_pages import login, OUT_DIR
from .branch_pages import _goto, CLOSE_MODAL_JS, GET_TEXT_JS


def main():
    branch = sys.argv[1] if len(sys.argv) > 1 else "천안점"
    cfg = Config.load(config_path())
    b = next(x for x in cfg.branches if branch in x.name)
    with sync_playwright() as p:
        browser, page = login(p, b.ctmnumb)
        g = extract_g_pammgno(page)
        _goto(page, "health", g)
        page.evaluate(CLOSE_MODAL_JS)
        page.wait_for_timeout(700)
        page.click("text=입사전 건강검진 제출", timeout=10000)
        page.wait_for_timeout(4000)
        txt = page.evaluate(GET_TEXT_JS)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "tab_810_prejoin.txt").write_text(txt, encoding="utf-8")
        print(f"텍스트 {len(txt)}자 저장")
        print(txt[:2000])
        browser.close()


if __name__ == "__main__":
    main()
