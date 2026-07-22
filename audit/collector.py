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
    save: bool = True,
    names: list | None = None,
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
        page.evaluate(f"window.__AUDIT_OPT = {{ yearTabs: {json.dumps(year_tabs, ensure_ascii=False)}, limit: {limit}, names: {json.dumps(names or [], ensure_ascii=False)} }};")
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

        # 지점 단위 페이지 수집·판정 (그룹 A·D: 교육일지·보수교육·정기점검)
        branch_pages = None
        try:
            from .branch_pages import scrape_branch_pages, analyze_branch_pages, CARING_BLOG
            years = list(range(cut_year, date.today().year + 1))
            progress_cb(f"[{branch_name}] 지점 페이지 수집 (8-7 교육, 8-7-1 보수교육, 6-3 점검)...")
            bp_raw = scrape_branch_pages(page, g_pammgno, years, progress_cb, cutoff=cutoff)
            # 17③ 월간 3종(식단표·프로그램표·가정통신문) 전월 게시 자동 확인(네이버 블로그 RSS)
            try:
                from .collect_blog import check_blog
                _t = date.today()
                _ry, _rm = (_t.year - 1, 12) if _t.month == 1 else (_t.year, _t.month - 1)
                progress_cb(f"[{branch_name}] 17③ 블로그 월간 3종 전월({_ry}.{_rm:02d}) 게시 확인...")
                bp_raw["blog_check"] = check_blog(page, CARING_BLOG.get(branch_name), _ry, _rm, progress_cb)
            except Exception as e:
                progress_cb(f"[{branch_name}] 블로그 확인 건너뜀: {e}")
            # 30① 의료기관 동행 진료 작성자 자격 (4-4 병의원 진료내역 + 8-1 직원 직종)
            try:
                from .collect_medical import scrape_staff_jobs, scrape_hospital, scrape_program
                progress_cb(f"[{branch_name}] 30① 병의원 진료내역·작성자 자격·프로그램 겹침 확인...")
                _s, _e = cutoff.replace(".", "")[:8], date.today().strftime("%Y%m%d")
                jobs = scrape_staff_jobs(page, g_pammgno, progress_cb)
                recs = scrape_hospital(page, g_pammgno, _s, _e, progress_cb)
                prog = scrape_program(page, g_pammgno, _s, _e, progress_cb)
                bp_raw["medical"] = {"records": recs, "staff_jobs": jobs, "prog": prog}
                # 연계기록지(1-10) 작성일 ↔ 상담일지(1-4) same-day 대조(수기확인 지원)
                try:
                    from .collect_medical import scrape_consult_samedays
                    from .branch_pages import parse_connect
                    _conn = parse_connect(bp_raw.get("connect") or "")
                    bp_raw["consult_samedays"] = scrape_consult_samedays(page, g_pammgno, _conn["rows"], progress_cb)
                except Exception as e:
                    progress_cb(f"[{branch_name}] 연계↔상담 대조 건너뜀: {e}")
            except Exception as e:
                progress_cb(f"[{branch_name}] 30① 의료연계 수집 건너뜀: {e}")
            branch_pages = analyze_branch_pages(bp_raw, cutoff, branch_name=branch_name)
        except Exception as e:
            progress_cb(f"[{branch_name}] 지점 페이지 수집 실패(수급자 분석은 계속): {e}")

        # 항목 33 식사(간식)제공결과: 3-1-4 만족도조사 + 6-1 주간식단표 (시설 단위, ②③⑤ 자동)
        item33 = None
        try:
            from .collect_item33 import collect_branch as _collect33, judge_item33
            progress_cb(f"[{branch_name}] 33번 식사제공결과 (3-1-4 만족도·6-1 식단표)...")
            item33 = judge_item33(_collect33(page, g_pammgno), datetime.now())
        except Exception as e:
            progress_cb(f"[{branch_name}] 33번 수집 실패(계속): {e}")
        browser.close()

    progress_cb(f"[{branch_name}] 분석 중... ({len(results)}명)")
    # branch_name: 항목 20① 이 audit_results/needs_full_<지점>.json 을 읽는다(없으면 '주의')
    analysis = analyze(results, cutoff, branch_name)
    if branch_pages:
        analysis["item_results"].update(branch_pages["item_results"])

        # 항목 28①②: 이동서비스 안전수칙·차량운행표 (1-6 탭, 퇴소자 포함 수집)
        # 평가기간 전 퇴소자는 제공 대상이 아니므로 1-1 스캔 enroll 로 in_scope 필터
        try:
            from .analyzer import enroll_periods, _d
            from .branch_pages import judge_transport
            tr_rows = bp_raw.get("transport") or []
            if tr_rows:
                cut_d = _d(cutoff)
                today_s = date.today().strftime("%Y.%m.%d")
                # 이름별로 '모든' 스캔 레코드가 평가기간 전 퇴소일 때만 제외(동명이인 안전)
                # ★ 재적종료일 > cutoff (초과) 여야 재적으로 본다. '== cutoff' 는 제외 —
                #   cutoff 는 평가기간 시작(≈개소)일이라 그날 퇴소면 개소 후 서비스 이력이 없다.
                #   실측: 천안 김병열 퇴소 2024.05.31 = cutoff(개소 6/1 전) → 제외(사용자 확정 2026-07-20).
                scoped: dict[str, bool] = {}
                for p in results:
                    ok = any(_d(e) > cut_d for _, e in enroll_periods(p.get("enroll"), today_s))
                    scoped[p["name"]] = scoped.get(p["name"], False) or ok
                out_scope = {n for n, ok in scoped.items() if not ok}
                r28 = judge_transport(tr_rows, cutoff, out_scope)
                if r28:
                    analysis["item_results"]["28"] = r28
                    progress_cb(f"[{branch_name}] 항목 28: {r28['status']}")
        except Exception as e:
            progress_cb(f"[{branch_name}] 항목 28 판정 건너뜀: {e}")

        # 항목 34② 보강: 결과평가 c3/c4 ↔ 30일 내 계획 재작성 (사전 수집물 있을 때만)
        # branch_pages 의 34 는 1-2 집계 숫자만 봐서 ①④ 부분판정 → ② 를 얹는다.
        try:
            from .item34 import judge as judge34_2
            r34_2 = judge34_2(branch_name, results, cutoff)
            if r34_2:
                cur = analysis["item_results"].get("34")
                if cur:
                    cur["sub_status"] = {**(cur.get("sub_status") or {}), **r34_2["sub_status"]}
                    cur["detail"] = (cur.get("detail") or "") + " / " + r34_2["detail"]
                    if r34_2["status"] == "미흡":
                        cur["status"] = "미흡"
                else:
                    analysis["item_results"]["34"] = r34_2
                progress_cb(f"[{branch_name}] 항목 34②: {r34_2['status']}")
        except Exception as e:
            progress_cb(f"[{branch_name}] 항목 34② 판정 건너뜀: {e}")

        # 항목 34③ 보강: 7-1 청구서 발송이력의 급여제공기록지 '포함' 여부 (사전 수집물 있을 때만)
        # '제외'만인 수급자도 수기 서명부 보관 시 충족 → 미흡 아닌 '주의(확인요망)' 만 낸다.
        try:
            from .item34 import judge3 as judge34_3
            r34_3 = judge34_3(branch_name, cutoff)
            if r34_3:
                cur = analysis["item_results"].get("34")
                if cur:
                    cur["sub_status"] = {**(cur.get("sub_status") or {}), **r34_3["sub_status"]}
                    cur["detail"] = (cur.get("detail") or "") + " / " + r34_3["detail"]
                    if r34_3["status"] == "주의" and cur.get("status") != "미흡":
                        cur["status"] = "주의"
                else:
                    analysis["item_results"]["34"] = r34_3
                progress_cb(f"[{branch_name}] 항목 34③: {r34_3['status']}")
        except Exception as e:
            progress_cb(f"[{branch_name}] 항목 34③ 판정 건너뜀: {e}")

        # 항목 18①: 롱텀 공개조회 '정보 게시율' (사전 수집물 있을 때만 — audit.collect_ltc_public)
        # 게시율은 매뉴얼 항목집합의 부분집합(지자체 신고항목 제외)이라 100% 여도 '주의' 까지만 낸다.
        try:
            from .collect_ltc_public import judge18
            r18 = judge18(branch_name)
            if r18:
                analysis["item_results"]["18"] = r18
                progress_cb(f"[{branch_name}] 항목 18①: {r18['status']}")
        except Exception as e:
            progress_cb(f"[{branch_name}] 항목 18① 판정 건너뜀: {e}")

        # 항목 8③ 보강: 노션 생일쿠폰 대조 (토큰 있을 때만 — 클라우드 전용)
        try:
            from .notion_birthday import compare as notion_compare
            r8 = analysis["item_results"].get("8")
            blog = (branch_pages.get("detail") or {}).get("birthday_log", {})
            if r8:
                # 개소 전·평가기간 전 달은 대조 대상이 아니다 — 안 넘기면 서구점(2025.03 개소)에
                # 2024-02 미지급이 뜬다(실측 165건 중 76건이 개소 전이었다).
                # 주간보호 수급자 명단(1-1 스캔)을 넘겨 방문요양 등 다른 급여종류를 미지급 의심에서 제외
                res = notion_compare(branch_name, blog, progress_cb=progress_cb,
                                     opened=branch_pages.get("opened"), cutoff=cutoff,
                                     daycare_names={p.get("name") for p in results if p.get("name")})
                if res is not None:
                    missing, months = res
                    if missing:
                        # 노션 생일자 성명 ↔ 케어포 대장 수령인의 '문자열' 대조라
                        # 표기차·동명이인·퇴사자로 쉽게 뒤집힌다(2026-07-16 대장 제목 파싱 문제로
                        # 노션 생일자 전원이 미지급으로 뒤집힌 오탐 선례). → 단정(미흡) 금지, 주의까지만.
                        # 이미 대장 자체가 미흡이면 그 판정을 낮추지 않는다.
                        if r8.get("status") == "양호":
                            r8["status"] = "주의"
                        if r8.get("sub_status", {}).get("③") == "양호":
                            r8["sub_status"]["③"] = "주의"
                        r8["detail"] += (f" · 생일쿠폰 미지급 의심 {len(missing)}건(확인요망, "
                                         f"{months[0]}~{months[-1]} 대조): {', '.join(missing)}")
                    elif months:
                        # '몇 개월 봤다'가 아니라 '어느 구간을 봤다'가 드러나야 한다.
                        # 종전 문구는 개소 전까지 세어 커버리지를 부풀렸다(천안·청주 "29개월 일치(2024-02~)").
                        r8["detail"] += (f" · 생일쿠폰 노션 대조 {months[0]}~{months[-1]} "
                                         f"{len(months)}개월 일치(생일자 없는 달 포함)")
                    else:
                        # 대조한 달이 0개 — 개소가 노션 최신월보다 늦으면 여기 온다(예: 봉명동
                        # 2026-10 개소). 판정은 옳지만(볼 게 없음) 조용히 넘어가면 '대조해서
                        # 깨끗한 것'과 구분이 안 된다 → 안 봤다는 걸 드러낸다.
                        r8["detail"] += " · 생일쿠폰 노션 대조 대상 월 없음(개소·평가기간 이후 자료 없음)"
        except Exception as e:
            progress_cb(f"[{branch_name}] 생일쿠폰 대조 건너뜀: {e}")

        # 항목 28③ 자동차종합보험 가입기간 유효 — 노션 차량현황 기준 자동판정
        # ★기본값을 try 진입 '전'에 무조건 박는다. items.py auto_subs 에 ③을 넣었기 때문에
        #   sub_status 에 ③이 비어 있으면 대시보드 autoVal 이 '항목 status'로 폴백해
        #   **안 본 보험에 만점이 자동 기입**된다(양호 지점 3곳에 1.0점 — 검수 실증).
        #   import 실패(모듈 미커밋)·judge 내부 예외 등 어디서 터져도 이 기본값이 남아야 한다.
        #   status 는 올리지 않는다 — 로컬 실행마다 28이 주의로 뒤집히는 노이즈 방지.
        r28 = analysis["item_results"].get("28")
        if r28:
            r28.setdefault("sub_status", {})["③"] = "주의"   # 기본 = 판정 불가(수기)
        try:
            from .notion_insurance import judge as ins_judge
            if r28:
                ins = ins_judge(branch_name, all_branches=list(BRANCH_CUTOFFS), progress_cb=progress_cb)
                if ins:
                    r28.setdefault("sub_status", {})["③"] = ins["status"]
                    # ③이 자동판정됐으니 '수기 확인' 안내에서 ③을 뺀다(모순 문구 방지)
                    det = (r28.get("detail") or "").replace(
                        "(③자동차종합보험 유효기간·④직원 수칙 준수 면담은 수기 확인)",
                        "(④직원 수칙 준수 면담은 수기 확인)")
                    r28["detail"] = det + " / " + ins["detail"]
                    # 항목 상태는 하위 판정 중 가장 나쁜 것으로 맞춘다(③이 미흡인데 항목이 양호로 남지 않도록)
                    rank = {"양호": 0, "주의": 1, "미흡": 2}
                    if rank.get(ins["status"], 0) > rank.get(r28.get("status", "양호"), 0):
                        r28["status"] = ins["status"]
        except Exception as e:
            progress_cb(f"[{branch_name}] 28③ 자동차보험 판정 건너뜀: {e}")

    if item33:
        analysis["item_results"]["33"] = item33
        # 항목 33①: 신규 수급자 기피식품 기재(욕구사정 영양 판단근거) 자동판정
        try:
            from .collect_item33 import judge_avoid_food
            st1, note1 = judge_avoid_food(results)
            if st1:
                item33["sub_status"]["①"] = st1
                item33["detail"] += " · " + note1
                if st1 == "미흡":
                    item33["status"] = "미흡"
        except Exception as e:
            progress_cb(f"[{branch_name}] 33① 기피식품 판정 건너뜀: {e}")

    # 항목 32 백신접종률: 주간보호=재가급여, 2026·2027 정기평가는 특례로 충족(Y) 자동 처리 (기준 명시)
    _yr = datetime.now().year
    if _yr in (2026, 2027):
        analysis["item_results"]["32"] = {
            "status": "양호", "sub_status": {"①": "양호"},
            "detail": f"[자동] {_yr} 재가급여(주간보호) 정기평가 특례 — 독감접종률 충족(Y) 자동 처리. 2028~는 실제 접종률 수기 확인",
        }

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
        "branch_pages": branch_pages["detail"] if branch_pages else None,
        "opened": branch_pages.get("opened") if branch_pages else None,
    }

    if save:
        AUDIT_DIR.mkdir(exist_ok=True)
        json_path = AUDIT_DIR / f"{branch_name}.json"
        json_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        _write_dashboard_data()
        progress_cb(f"[{branch_name}] 저장 완료 → {json_path}")
    else:
        progress_cb(f"[{branch_name}] 테스트 모드 — 결과를 저장하지 않음 (대시보드 데이터 보존)")
    return out


def _write_dashboard_data() -> None:
    """audit_results/*.json 중 '본 점검 결과'만 모아 dashboard_data.js 생성 (file:// 대시보드용)."""
    data = {}
    for f in AUDIT_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(d, dict) or "item_results" not in d:
                continue  # 수집 중간산출물(청구발송_·contract_·needs_full_ 등)도 branch 키가 있어 덮어쓴다
            slim = {k: d[k] for k in ("branch", "cutoff", "run_at", "people", "item_results", "rows_match", "opened") if k in d}
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
