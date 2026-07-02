"""지점별 케어포 자동 점검 수집기.

로그인(포털 자동) → 1-1 수급자 정보관리 → 퇴소자 포함 → in-page 스캔 주입
→ 진행 폴링 → 결과 회수 → 분석 → audit_results/ 에 JSON + dashboard_data.js 저장
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from src import credentials
from src.carefor_client import build_spa_hash, extract_g_pammgno, _navigate_spa
from .analyzer import analyze
from .items import ITEMS, BRANCH_CUTOFFS

PORTAL_URL = "https://eform.caring.co.kr/carefor"
DN_BASE = "https://dn.carefor.co.kr/"
AUDIT_DIR = Path(__file__).resolve().parent.parent / "audit_results"
SCAN_JS = (Path(__file__).resolve().parent / "scan_inpage.js").read_text(encoding="utf-8")


def patient_manage_url(g_pammgno: str) -> str:
    h = build_spa_hash("left_sub1", "/share/patient/view.patient_manage", "1-1.수급자 정보관리", g_pammgno)
    return f"{DN_BASE}#{h}"


def run_branch_audit(
    ctmnumb: str,
    branch_name: str,
    cutoff: str | None = None,
    limit: int = 0,
    headless: bool = True,
    progress_cb=print,
) -> dict:
    cutoff = cutoff or BRANCH_CUTOFFS.get(branch_name, "2024.01.01")
    cut_year = int(cutoff[:4])
    year_tabs = [f"{y}년" for y in range(date.today().year, cut_year - 1, -1)]

    portal_creds = credentials.get_portal_credentials()
    if not portal_creds:
        raise RuntimeError("케어포 portal 자격증명이 없습니다. setup_credentials.py 를 먼저 실행하세요.")
    portal_id, portal_pw = portal_creds

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(http_credentials={"username": portal_id, "password": portal_pw})
        portal_page = ctx.new_page()
        progress_cb(f"[{branch_name}] 포털 로그인 중...")
        portal_page.goto(PORTAL_URL, wait_until="domcontentloaded")
        portal_page.wait_for_function("typeof login2 === 'function'", timeout=15000)
        with ctx.expect_page(timeout=60000) as new_page_info:
            portal_page.evaluate(f"login2('{ctmnumb}')")
        page = new_page_info.value
        page.wait_for_load_state("domcontentloaded", timeout=30000)
        page.wait_for_load_state("networkidle", timeout=30000)

        g_pammgno = extract_g_pammgno(page)
        progress_cb(f"[{branch_name}] 1-1 수급자 정보관리 이동...")
        _navigate_spa(page, patient_manage_url(g_pammgno))
        page.wait_for_timeout(2500)

        # 퇴소자 포함 검색 체크
        page.evaluate(
            """
            (() => {
              const label = Array.from(document.querySelectorAll('label')).find(el => el.textContent.includes('퇴소자 포함'));
              if (label) { const input = label.querySelector('input'); if (input && !input.checked) input.click(); }
            })()
            """
        )
        page.wait_for_timeout(3000)
        n_rows = page.evaluate("document.querySelectorAll('table.frame_list_tbl tr.cr').length")
        progress_cb(f"[{branch_name}] 수급자 {n_rows}명 (퇴소자 포함)")
        if not n_rows:
            raise RuntimeError("수급자 리스트를 찾지 못했습니다.")

        # 스캔 주입
        page.evaluate(f"window.__AUDIT_OPT = {{ yearTabs: {json.dumps(year_tabs, ensure_ascii=False)}, limit: {limit} }};")
        page.evaluate(SCAN_JS)

        # 진행 폴링
        start = time.time()
        last = ""
        while True:
            st = page.evaluate("window.__AUDIT ? {p: window.__AUDIT.progress, done: window.__AUDIT.done, err: window.__AUDIT.error, n: window.__AUDIT.results.length} : null")
            if st is None:
                raise RuntimeError("스캔 상태를 읽을 수 없습니다.")
            if st["p"] != last:
                progress_cb(f"[{branch_name}] {st['p']} (수집 {st['n']}명)")
                last = st["p"]
            if st["done"]:
                if st["err"]:
                    progress_cb(f"[{branch_name}] 스캔 오류: {st['err']} — 수집분까지 분석 진행")
                break
            if time.time() - start > 3600 * 3:
                progress_cb(f"[{branch_name}] 3시간 초과 — 수집분까지 분석 진행")
                break
            time.sleep(5)

        results = page.evaluate("window.__AUDIT.results")
        browser.close()

    progress_cb(f"[{branch_name}] 분석 중... ({len(results)}명)")
    analysis = analyze(results, cutoff)

    out = {
        "branch": branch_name,
        "ctmnumb": ctmnumb,
        "cutoff": cutoff,
        "run_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "people": len(results),
        "raw": results,
        "analysis": {k: v for k, v in analysis.items() if k != "rows_match"},
        "rows_match": analysis["rows_match"],
        "items": ITEMS,
        "item_results": analysis["item_results"],
    }

    AUDIT_DIR.mkdir(exist_ok=True)
    json_path = AUDIT_DIR / f"{branch_name}.json"
    json_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    _write_dashboard_data()
    progress_cb(f"[{branch_name}] 저장 완료 → {json_path}")
    return out


def _write_dashboard_data() -> None:
    """audit_results/*.json 을 모아 dashboard_data.js 생성 (file:// 대시보드용)."""
    data = {}
    for f in AUDIT_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            slim = {k: d[k] for k in ("branch", "cutoff", "run_at", "people", "item_results", "rows_match") if k in d}
            slim["analysis"] = d.get("analysis", {})
            slim["analysis"].pop("rows_match", None)
            data[d["branch"]] = slim
        except Exception:
            continue
    js = "window.AUDIT_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n"
    js += "window.AUDIT_ITEMS = " + json.dumps(ITEMS, ensure_ascii=False) + ";\n"
    (AUDIT_DIR / "dashboard_data.js").write_text(js, encoding="utf-8")
    _sync_to_share()
    try:
        from .summary_page import generate as _gen_summary
        _gen_summary()
    except Exception as e:
        print(f"  ↳ 요약페이지 생성 실패: {e}")


def _sync_to_share() -> None:
    """share_path.txt 에 적힌 공유폴더로 대시보드+데이터 자동 복사 (본부 공유용).

    사용법: carefor-auto/share_path.txt 파일에 공유폴더 경로 한 줄 작성.
    예)  \\\\회사서버\\공유\\지점점검   또는   C:\\Users\\alsgm\\OneDrive\\지점점검
    """
    import shutil

    marker = AUDIT_DIR.parent / "share_path.txt"
    if not marker.exists():
        return
    dest = Path(marker.read_text(encoding="utf-8").strip().strip('"'))
    if not str(dest):
        return
    try:
        (dest / "audit_results").mkdir(parents=True, exist_ok=True)
        shutil.copy2(AUDIT_DIR.parent / "audit_dashboard.html", dest / "audit_dashboard.html")
        for f in AUDIT_DIR.glob("*"):
            shutil.copy2(f, dest / "audit_results" / f.name)
        print(f"  ↳ 공유폴더 동기화 완료: {dest}")
    except Exception as e:
        print(f"  ↳ 공유폴더 동기화 실패({dest}): {e}")
