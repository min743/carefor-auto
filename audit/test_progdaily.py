# -*- coding: utf-8 -*-
"""5-1 프로그램 제공기록 수집·주간 집계 단독 테스트."""
import sys
from collections import Counter
from datetime import date, timedelta

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

from src.config import Config, config_path
from src.carefor_client import extract_g_pammgno
from .explore_pages import login
from .branch_pages import _goto, CLOSE_MODAL_JS, GET_TEXT_JS, parse_progdaily


def main():
    branch = sys.argv[1] if len(sys.argv) > 1 else "천안점"
    cfg = Config.load(config_path())
    b = next(x for x in cfg.branches if branch in x.name)
    records = []
    with sync_playwright() as p:
        browser, page = login(p, b.ctmnumb)
        g = extract_g_pammgno(page)
        _goto(page, "progdaily", g)
        page.evaluate(CLOSE_MODAL_JS)
        page.evaluate("change_view('monthly')")
        page.wait_for_timeout(2500)
        for m in range(1, date.today().month + 1):
            page.evaluate(f"reloadPage({{'yy':'2026','mm':'{m:02d}','dd':'01','view_flag':'monthly'}})")
            page.wait_for_timeout(2500)
            txt = page.evaluate(GET_TEXT_JS)
            recs = parse_progdaily(txt)
            records += recs
            print(f"2026-{m:02d}: {len(recs)}건")
        browser.close()

    print("\n유형 분포:", Counter(r["type"] for r in records))
    print("일지✓ 비율:", sum(1 for r in records if r["journal"]), "/", len(records))

    # 주간 집계 (신체·인지)
    for tk in ("신체", "인지"):
        recs = [r for r in records if r["type"].startswith(tk) and r["journal"]]
        misses = []
        wk = date(2026, 1, 1) - timedelta(days=date(2026, 1, 1).weekday())
        if wk < date(2026, 1, 1):
            wk += timedelta(days=7)  # 부분 주 제외 (판정 로직과 동일)
        while wk + timedelta(days=6) <= date.today():
            cnt = sum(1 for r in recs if wk <= r["date"] <= wk + timedelta(days=6))
            if cnt < 3:
                misses.append(f"{wk.month}/{wk.day}({cnt})")
            wk += timedelta(days=7)
        print(f"{tk} 주3회 미달 주: {len(misses)} → {misses[:10]}")
    social = [r for r in records if r["type"].startswith("사회") and r["journal"]]
    print("사회 월별:", Counter(f"{r['date'].month}월" for r in social))


if __name__ == "__main__":
    main()
