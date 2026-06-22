"""
1-7.수급자 현황 리포트 페이지 구조 분석.
둔산점 로그인 → 1-7 진입 → 명단 + 급여개시일 컬럼 확인.
"""
import json

from playwright.sync_api import sync_playwright

from src import credentials
from src.carefor_client import build_spa_hash, extract_g_pammgno, _navigate_spa


CTMNUMB = "23017000602"


def recipient_url(g_pammgno: str) -> str:
    h = build_spa_hash(
        "left_sub1",
        "/share/patient/view.patient_report",
        "1-7.수급자 현황 리포트",
        g_pammgno,
    )
    return f"https://dn.carefor.co.kr/#{h}"


def main():
    creds = credentials.get_portal_credentials()
    if not creds:
        print("자격증명 미저장")
        return
    pid, pw = creds

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(http_credentials={"username": pid, "password": pw})
        portal = ctx.new_page()

        print("1) Portal 진입...")
        portal.goto("https://eform.caring.co.kr/carefor", wait_until="domcontentloaded")
        portal.wait_for_function("typeof login2 === 'function'", timeout=15000)

        print("2) 둔산점 진입...")
        with ctx.expect_page() as new_page_info:
            portal.evaluate(f"login2('{CTMNUMB}')")
        data = new_page_info.value
        data.wait_for_load_state("networkidle", timeout=30000)

        print("3) g_pammgno 추출...")
        g = extract_g_pammgno(data)
        print(f"   g_pammgno: {g}")

        print("4) 1-7.수급자 현황 리포트 화면 이동...")
        _navigate_spa(data, recipient_url(g))
        data.wait_for_timeout(3000)
        print(f"   URL: {data.url}")
        print(f"   제목: {data.title()}")
        print()

        # 헤더 텍스트 찾기 — 급여개시일 컬럼 위치 파악
        print("=== 페이지 텍스트 (앞 1500자) ===")
        body_text = data.evaluate("document.body.innerText.substring(0, 1500)")
        print(body_text)
        print()

        # g-td 컬럼 매핑 (월간 입소자처럼 g-td 사용할 가능성)
        print("=== g-td 구조 분석 ===")
        gtd_info = data.evaluate("""
            (() => {
                const cells = document.querySelectorAll('g-td');
                if (cells.length === 0) return {count: 0};
                // 첫 5행만 추출 (각 행의 모든 컬럼)
                const byRow = {};
                cells.forEach(c => {
                    const r = c.getAttribute('data-gt-row');
                    const col = c.getAttribute('data-gt-col');
                    if (parseInt(r) < 5) {
                        if (!byRow[r]) byRow[r] = {};
                        byRow[r][col] = c.textContent.trim();
                    }
                });
                return {count: cells.length, rows: byRow};
            })()
        """)
        print(json.dumps(gtd_info, indent=2, ensure_ascii=False))
        print()

        # 일반 <table> 구조도 확인
        print("=== <table> 구조 ===")
        table_info = data.evaluate("""
            Array.from(document.querySelectorAll('table')).slice(0, 5).map((t, i) => ({
                idx: i,
                rows: t.querySelectorAll('tr').length,
                first_3_rows: Array.from(t.querySelectorAll('tr')).slice(0, 3).map(tr =>
                    Array.from(tr.querySelectorAll('td,th')).map(td => td.textContent.trim())
                )
            }))
        """)
        print(json.dumps(table_info, indent=2, ensure_ascii=False))
        print()

        # "급여" 또는 "개시" 가 포함된 요소 검색
        print("=== '급여개시' 관련 텍스트 ===")
        related = data.evaluate("""
            (() => {
                const all = document.querySelectorAll('*');
                const matches = [];
                all.forEach(el => {
                    const txt = el.textContent || '';
                    if (el.children.length === 0 && (txt.includes('급여개시') || txt.includes('급여시작') || txt.includes('수급개시'))) {
                        matches.push({tag: el.tagName, text: txt.trim().substring(0, 50)});
                    }
                });
                return matches.slice(0, 10);
            })()
        """)
        print(json.dumps(related, indent=2, ensure_ascii=False))
        print()

        # 날짜 패턴 검색 (YYYY-MM-DD 또는 YYYY.MM.DD)
        print("=== 페이지 내 날짜 패턴 (앞 20개) ===")
        dates = data.evaluate("""
            (() => {
                const txt = document.body.innerText;
                const matches = txt.match(/20\\d{2}[.\\-/]\\d{1,2}[.\\-/]\\d{1,2}/g) || [];
                return [...new Set(matches)].slice(0, 20);
            })()
        """)
        print(dates)

        data.screenshot(path="recipient_screenshot.png", full_page=True)
        with open("recipient_dump.html", "w", encoding="utf-8") as f:
            f.write(data.content())
        print()
        print("📸 recipient_screenshot.png 저장")
        print("📄 recipient_dump.html 저장")

        input("\n엔터 = 종료... ")
        browser.close()


if __name__ == "__main__":
    main()
