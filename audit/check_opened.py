# -*- coding: utf-8 -*-
"""전 지점 기관 지정일자 조회 (9-1 시설정보설정)."""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

from src.config import Config, config_path
from src.carefor_client import extract_g_pammgno
from .explore_pages import login
from .branch_pages import _goto, GET_TEXT_JS
from .items import BRANCH_CUTOFFS


def main():
    cfg = Config.load(config_path())
    print(f"{'지점':<10} {'케어포 지정일':<14} {'현재 기준일(cutoff)':<18}")
    with sync_playwright() as p:
        for b in cfg.branches:
            try:
                browser, page = login(p, b.ctmnumb)
                g = extract_g_pammgno(page)
                _goto(page, "master", g)
                txt = page.evaluate(GET_TEXT_JS)
                m = re.search(r"기관\s*지정일자[\s\S]{0,30}?(\d{4}\.\d{2}\.\d{2})", txt)
                opened = m.group(1) if m else "파싱실패"
                browser.close()
            except Exception as e:
                opened = f"오류: {e}"
            print(f"{b.name:<10} {opened:<14} {BRANCH_CUTOFFS.get(b.name, '-'):<18}")


if __name__ == "__main__":
    main()
