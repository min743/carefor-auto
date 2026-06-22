"""
Portal 페이지 실제 내용 확인.
브라우저 창 띄워서 페이지 상태와 사용 가능한 JS 함수 출력.
"""
from playwright.sync_api import sync_playwright

from src import credentials


def main():
    creds = credentials.get_portal_credentials()
    if not creds:
        print("❌ 자격증명 미저장. setup_credentials.py 먼저 실행")
        return
    portal_id, portal_pw = creds

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(
            http_credentials={"username": portal_id, "password": portal_pw}
        )
        page = ctx.new_page()

        print("Portal 진입 중...")
        page.goto("https://eform.caring.co.kr/carefor", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        print(f"URL: {page.url}")
        print(f"제목: {page.title()}")
        print()

        # 페이지 텍스트 일부
        body_text = page.evaluate("document.body ? document.body.innerText.substring(0, 500) : '(no body)'")
        print("페이지 텍스트 (앞 500자):")
        print("---")
        print(body_text)
        print("---")
        print()

        # iframe 있는지
        frames_info = page.evaluate("""
            Array.from(document.querySelectorAll('iframe')).map(f => ({
                src: f.src, name: f.name, id: f.id
            }))
        """)
        print(f"iframe 개수: {len(frames_info)}")
        for f in frames_info:
            print(f"  - {f}")
        print()

        # JS 함수 존재 확인
        funcs = page.evaluate("""
            ({
                login: typeof login,
                login2: typeof login2,
                postToNewWindow: typeof postToNewWindow,
                jQuery: typeof jQuery
            })
        """)
        print(f"JS 함수: {funcs}")
        print()

        # 모든 button 텍스트
        buttons = page.evaluate("""
            Array.from(document.querySelectorAll('button, input[type=button], input[type=submit]'))
                .slice(0, 10)
                .map(b => (b.textContent || b.value || '').trim())
        """)
        print(f"버튼 (앞 10개): {buttons}")
        print()

        # 스크린샷 저장
        page.screenshot(path="portal_screenshot.png", full_page=True)
        print("📸 portal_screenshot.png 저장됨")

        # HTML 저장
        html = page.content()
        with open("portal_dump.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("📄 portal_dump.html 저장됨")
        print()

        input("창 확인하고 엔터 치면 종료합니다... ")
        browser.close()


if __name__ == "__main__":
    main()
