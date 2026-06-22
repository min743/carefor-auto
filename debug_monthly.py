"""
월간 입소자 페이지 실제 내용 확인.
둔산점 로그인 → 월간 입소자 이동 → 표 내용 출력.
"""
from playwright.sync_api import sync_playwright

from src import credentials
from src.carefor_client import monthly_attend_url, extract_g_pammgno, _navigate_spa


CTMNUMB = "23017000602"  # 둔산점


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

        print("2) 둔산점 주간보호 진입...")
        with ctx.expect_page() as new_page_info:
            portal.evaluate(f"login2('{CTMNUMB}')")
        data = new_page_info.value
        data.wait_for_load_state("networkidle", timeout=30000)
        print(f"   진입 URL: {data.url}")

        print("3) g_pammgno 추출...")
        g = extract_g_pammgno(data)
        print(f"   g_pammgno: {g}")

        print("4) 월간 입소자 화면으로 이동...")
        target_url = monthly_attend_url(g)
        print(f"   타겟: {target_url}")
        _navigate_spa(data, target_url)
        data.wait_for_timeout(3000)  # SPA 추가 대기

        print(f"   현재 URL: {data.url}")
        print(f"   제목: {data.title()}")
        print()

        # iframe 확인
        frames_info = data.evaluate("""
            Array.from(document.querySelectorAll('iframe')).map(f => ({
                src: f.src, name: f.name, id: f.id
            }))
        """)
        print(f"iframe 개수: {len(frames_info)}")
        for f in frames_info:
            print(f"  - {f}")
        print()

        # 모든 테이블 정보
        tables_info = data.evaluate("""
            Array.from(document.querySelectorAll('table')).map((t, i) => ({
                idx: i,
                rows: t.querySelectorAll('tr').length,
                first_row_text: t.querySelector('tr') ? t.querySelector('tr').textContent.trim().substring(0, 100) : ''
            }))
        """)
        print(f"table 개수: {len(tables_info)}")
        for t in tables_info:
            print(f"  table[{t['idx']}]: {t['rows']}행, 첫 행: {t['first_row_text'][:80]}")
        print()

        # 모든 tr 행 (텍스트로)
        rows = data.evaluate("""
            Array.from(document.querySelectorAll('tr')).map(tr =>
                Array.from(tr.children).map(c => c.textContent.trim())
            ).filter(r => r.length > 0)
        """)
        print(f"전체 tr 개수: {len(rows)}")
        print()

        # 날짜 텍스트가 보이는 행만 출력
        import re
        date_rows = []
        for r in rows:
            joined = " | ".join(r)
            if re.search(r"20\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}", joined) or re.search(r"\d{1,2}월", joined):
                date_rows.append(joined)

        print(f"날짜 패턴이 포함된 행 ({len(date_rows)}개):")
        for r in date_rows[:25]:
            print(f"  {r[:200]}")
        print()

        # 페이지 텍스트 일부
        body_text = data.evaluate("document.body.innerText.substring(0, 800)")
        print("페이지 텍스트 (앞 800자):")
        print("---")
        print(body_text)
        print("---")

        # 스크린샷 + HTML 저장
        data.screenshot(path="monthly_screenshot.png", full_page=True)
        with open("monthly_dump.html", "w", encoding="utf-8") as f:
            f.write(data.content())
        print("\n📸 monthly_screenshot.png 저장")
        print("📄 monthly_dump.html 저장")

        input("\n엔터 = 종료... ")
        browser.close()


if __name__ == "__main__":
    main()
