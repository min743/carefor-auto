# -*- coding: utf-8 -*-
"""scrape_rights 단독 라이브 테스트."""
import sys

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

from src.config import Config, config_path
from src.carefor_client import extract_g_pammgno
from .explore_pages import login
from .branch_pages import scrape_rights, parse_rights


def main():
    branch = sys.argv[1] if len(sys.argv) > 1 else "천안점"
    cfg = Config.load(config_path())
    b = next(x for x in cfg.branches if branch in x.name)
    with sync_playwright() as p:
        browser, page = login(p, b.ctmnumb)
        g = extract_g_pammgno(page)
        txt = scrape_rights(page, g)
        browser.close()
    print("rights 길이:", len(txt))
    if txt:
        r = parse_rights(txt)
        print(f"완료/대상: {r['done']}/{r['total']}, 행: {len(r['rows'])}")


if __name__ == "__main__":
    main()
