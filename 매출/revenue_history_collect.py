# -*- coding: utf-8 -*-
"""월별 매출 이력 수집 — 개소월부터 지금까지 한 번만 긁어 파일로 남긴다.

왜 별도인가:
  `revenue_check.py` 는 '이번달 극대화 점검'용이라 대상월/전월 두 달만 본다.
  연도별 추이는 개소월부터 전부 필요한데 매번 다시 긁을 이유가 없다
  → **한 번 긁어 `revenue_monthly.json` 에 누적**하고, 이후엔 빠진 달만 채운다.

무엇을 저장하나 (월·지점별):
  급여일수·총매출(급여수가 합)·8h이상/미만 건수와 금액.
  비급여(식사재료비·간식비·이미용비·진료약제비·기타·등급외한도초과)도 함께 저장한다.
  ★7-1 과거월 이동법: 화면의 `reloadPage({yy,mm,inc_exit:'1'})` 호출.
    (csmyymm 값만 바꿔 POST 하면 안 먹는다 — 서버가 무시하고 현재월을 준다. 실측 2026-07-22)

실행:
  py -X utf8 revenue_history_collect.py                # 빠진 달 전부(개소월~지난달)
  py -X utf8 revenue_history_collect.py 천안            # 한 지점만
  py -X utf8 revenue_history_collect.py 천안 2025-01 2025-03   # 구간 지정
  py -X utf8 revenue_history_collect.py --list          # 수집 현황만 보기

케어포 단일 계정이라 지점 순차. 이미 저장된 달은 건너뛴다(--force 로 재수집).
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from revenue_check import (  # noqa: E402
    _REPO, OUT_ROOT, branch_key, _open_2_8, _click_tab, _grab_month,
    parse_transport, FULL_8H, NEAR_MISS_LO, scrape_7_1_nonpay, DN_BASE,
)

if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from playwright.sync_api import sync_playwright  # noqa: E402
from src.config import Config, config_path, app_data_dir  # noqa: E402
from src.carefor_client import extract_g_pammgno  # noqa: E402
from audit.explore_pages import login  # noqa: E402

# 지점 개소월 — 이보다 앞은 데이터가 없다(audit/items.py BRANCH_CUTOFFS 와 같은 근거)
OPEN_YM = {"천안": (2024, 5), "청주": (2024, 7), "둔산": (2024, 8), "서구": (2025, 3)}

HIST = app_data_dir() / "revenue_history" / "revenue_monthly.json"


def load_hist() -> dict:
    if HIST.exists():
        try:
            return json.loads(HIST.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_hist(d: dict) -> None:
    HIST.parent.mkdir(parents=True, exist_ok=True)
    HIST.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def months(y0: int, m0: int, y1: int, m1: int):
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _open_7_1(page, g_pammgno: str) -> None:
    """7-1 본인부담금 청구관리로 이동."""
    from src.carefor_client import build_spa_hash, _navigate_spa
    h = build_spa_hash("left_sub7", "/share/cost/view.cost_master",
                       "7-1.본인부담금 청구관리", g_pammgno)
    _navigate_spa(page, f"{DN_BASE}#{h}")
    page.wait_for_timeout(5000)


def _goto_71_month(page, y: int, m: int) -> None:
    """7-1 청구월 이동 — 화면의 reloadPage({yy,mm}) 를 부른다.

    ★csmyymm 입력값만 바꿔 POST 하면 서버가 무시하고 현재월을 준다(실측).
      화면에 붙은 reloadPage 만이 실제로 월을 바꾼다.
    표시 월이 목표와 같아질 때까지 기다린다(로딩 중 옛 데이터를 읽지 않게).
    """
    page.evaluate("([yy,mm])=>reloadPage({yy:yy, mm:mm, inc_exit:'1'})",
                  [str(y), f"{m:02d}"])
    want = f"{y}년 {m:02d}월"
    for _ in range(24):
        page.wait_for_timeout(500)
        cur = page.evaluate("()=>{const e=document.querySelector('.datearea');"
                            "return e?e.innerText.trim().split('\\n')[0]:'';}")
        if cur == want:
            page.wait_for_timeout(800)   # 표 렌더 안정화
            return
    raise RuntimeError(f"청구월 {want} 로 안 바뀜")


def summarize(recs: list[dict]) -> dict:
    """월 단위 집계 — 화면에서 쓸 최소 항목만."""
    o8 = [r for r in recs if r["min"] >= FULL_8H]
    u8 = [r for r in recs if r["min"] < FULL_8H]
    near = [r for r in u8 if r["min"] >= NEAR_MISS_LO]
    return {
        "pay_days": len(recs),
        "rev_total": sum(r["amt"] for r in recs),
        "over8": len(o8), "rev_over8": sum(r["amt"] for r in o8),
        "u8": len(u8), "rev_under8": sum(r["amt"] for r in u8),
        "near": len(near),
        "people": len({r["name"] for r in recs}),
    }


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    hist = load_hist()

    cfg = Config.load(config_path())
    branches = cfg.branches
    if args and not args[0][:4].isdigit():
        branches = [b for b in branches if args[0] in b.name] or branches
        args = args[1:]

    today = date.today()
    ly, lm = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)

    if "--list" in flags:
        print("=== 수집 현황 ===")
        for b in branches:
            k = branch_key(b.name)
            have = sorted(hist.get(k, {}))
            oy, om = OPEN_YM.get(k, (2024, 1))
            need = [f"{y}{m:02d}" for y, m in months(oy, om, ly, lm)]
            miss = [x for x in need if x not in have]
            print(f"  {k:<5} 보유 {len(have):>2}개월 / 필요 {len(need):>2}개월 · 빠진 달 {len(miss)}")
            if miss:
                print(f"        → {', '.join(miss[:12])}{' …' if len(miss) > 12 else ''}")
        return

    with sync_playwright() as pw:
        for b in branches:
            k = branch_key(b.name)
            oy, om = OPEN_YM.get(k, (2024, 1))
            if len(args) >= 2:   # 구간 지정
                y0, m0 = map(int, args[0].split("-"))
                y1, m1 = map(int, args[1].split("-"))
            else:
                y0, m0, y1, m1 = oy, om, ly, lm
            # 개소 전은 건너뛴다
            if (y0, m0) < (oy, om):
                y0, m0 = oy, om

            todo = [(y, m) for y, m in months(y0, m0, y1, m1)
                    if "--force" in flags or f"{y}{m:02d}" not in hist.get(k, {})]
            if not todo:
                print(f"[{b.name}] 빠진 달 없음 — 건너뜀")
                continue

            print(f"\n===== {b.name} — {len(todo)}개월 수집 "
                  f"({todo[0][0]}-{todo[0][1]:02d} ~ {todo[-1][0]}-{todo[-1][1]:02d}) =====", flush=True)
            browser = None
            try:
                browser, page = login(pw, b.ctmnumb)
                g = extract_g_pammgno(page)
                _open_2_8(page, g)
                _click_tab(page, "월간 이동서비스 현황")
                got = hist.setdefault(k, {})
                # 1) 2-8 에서 월별 매출(급여수가) — 한 세션에서 월만 바꿔가며
                for y, m in todo:
                    try:
                        cells = _grab_month(page, y, m)
                        recs = parse_transport(cells)
                        got[f"{y}{m:02d}"] = summarize(recs)
                        s = got[f"{y}{m:02d}"]
                        print(f"  {y}-{m:02d}: 급여일 {s['pay_days']:>4} · "
                              f"매출 {s['rev_total']:>12,}원 · 8h이상 {s['over8']:>4} / 미만 {s['u8']:>3}",
                              flush=True)
                        save_hist(hist)      # 매달 저장 — 중간에 끊겨도 그때까지는 남는다
                    except Exception as ex:
                        print(f"  {y}-{m:02d}: ❌ {ex}", flush=True)

                # 2) 7-1 에서 월별 비급여 — reloadPage({yy,mm}) 로 청구월 이동
                #    ⚠️ csmyymm 값만 바꿔 POST 하면 서버가 무시한다. 반드시 reloadPage 로.
                try:
                    _open_7_1(page, g)
                    for y, m in todo:
                        kk = f"{y}{m:02d}"
                        if kk not in got:
                            continue
                        try:
                            _goto_71_month(page, y, m)
                            np = scrape_7_1_nonpay(page, progress=lambda s: None)
                            if np:
                                got[kk]["nonpay"] = np
                                print(f"  {y}-{m:02d} 비급여: {np['비급여계']:>11,}원", flush=True)
                                save_hist(hist)
                        except Exception as ex:
                            print(f"  {y}-{m:02d} 비급여 ❌ {ex}", flush=True)
                except Exception as ex:
                    print(f"  7-1 진입 실패(비급여 건너뜀): {ex}", flush=True)
            except Exception as ex:
                print(f"  ❌ {b.name} 실패: {ex}", flush=True)
            finally:
                if browser:
                    browser.close()

    save_hist(hist)
    print(f"\n저장: {HIST}")


if __name__ == "__main__":
    main()
