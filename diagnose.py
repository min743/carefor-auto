"""Playwright Chromium 경로/접근 진단."""
import os
from pathlib import Path

EXPECTED = r"C:\Users\alsgm\AppData\Local\ms-playwright\chromium-1223\chrome-win64\chrome.exe"

print("=" * 60)
print("Playwright Chromium 진단")
print("=" * 60)
print(f"확인할 경로: {EXPECTED}")
print()

print(f"Path.exists():       {Path(EXPECTED).exists()}")
print(f"os.path.exists():    {os.path.exists(EXPECTED)}")
print(f"os.path.isfile():    {os.path.isfile(EXPECTED)}")

if Path(EXPECTED).exists():
    print(f"파일 크기:           {Path(EXPECTED).stat().st_size:,} bytes")
    print(f"읽기 권한:           {os.access(EXPECTED, os.R_OK)}")
    print(f"실행 권한:           {os.access(EXPECTED, os.X_OK)}")
print()

# 환경변수 체크
print("환경변수:")
for var in ["PLAYWRIGHT_BROWSERS_PATH", "PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"]:
    print(f"  {var}: {os.environ.get(var, '(설정 안 됨)')}")
print()

# Playwright가 실제로 사용하는 경로
print("Playwright 내부 경로:")
try:
    from playwright._impl._registry import Registry, BrowsersJsonAccessor
    from playwright._impl._driver import compute_driver_executable
    driver = compute_driver_executable()
    print(f"  driver_executable: {driver}")
except Exception as e:
    print(f"  driver lookup 실패: {e}")
print()

# 직접 chrome.exe 실행 시도
print("chrome.exe 직접 실행 테스트:")
import subprocess
try:
    res = subprocess.run([EXPECTED, "--version"], capture_output=True, text=True, timeout=10)
    print(f"  exit code: {res.returncode}")
    print(f"  stdout: {res.stdout.strip()}")
    print(f"  stderr: {res.stderr.strip()[:200]}")
except Exception as e:
    print(f"  실행 실패: {type(e).__name__}: {e}")
print()

# 폴더 목록
print("chromium-1223 폴더 내용:")
parent = Path(r"C:\Users\alsgm\AppData\Local\ms-playwright\chromium-1223")
if parent.exists():
    for item in sorted(parent.iterdir()):
        print(f"  {item.name}")
