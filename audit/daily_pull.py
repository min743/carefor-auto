# -*- coding: utf-8 -*-
"""매일 정기 실행 — 매출 점검 수집 → 공유 허브 배포까지 한 번에.

왜 Python 인가:
  `.bat` 에 한글 경로(`..\\매출점검`)를 쓰면 cmd 가 cp949 로 잘못 디코딩해 **pushd 가 실패**한다
  (함정노트-PowerShell.md 함정 1). 실측: UTF-8 저장 bat → PUSHD_FAIL, cp949 저장 → PUSHD_OK.
  이 때문에 예약작업이 **매일 exit 255 로 조용히 죽고 있었다**(로그 파일조차 안 생김).
  → **bat 는 ASCII 만 두고, 한글 경로는 전부 이 모듈 안에서 다룬다.**

왜 한 파일에 순차로:
  케어포는 **동시 로그인 1개**만 허용한다. 수집과 배포를 따로 예약하면 겹칠 때
  빈 데이터를 조용히 담을 수 있다. 여기서 순차로 돌리면 그 일이 구조적으로 불가능하다.

하는 일 (09:30 시작, 순차):
  [1/3] 매출 점검 — 케어포 접속, 4지점 이번달 수집 → 지점별·합본 HTML 재생성(로컬)
  [2/3] 공유 허브 '매출 점검' 페이지 갱신 — 위 합본을 올린다(이름 마스킹은 배포기가 수행)
  [3/3] 차량 월별 수리비 — 정비이력 재수집·OCR·HTML → 허브 '차량 월별 수리비' 페이지까지 갱신

왜 이 순서·이 시각인가 (케어포 점유가 겹치면 안 된다):
  07:00~08:00 지점점검(평일) · 09:00~09:08 차량 시트갱신 · **09:30~09:37 매출(여기)** ·
  10:45~10:48 출석 · 11:10~11:18 차량 보고(월). → **09:40~10:45 가 비어 있어** 차량 수리비를
  매출 뒤에 붙였다. 매출을 먼저 끝내고 허브에 올려야 사용자가 기다리지 않는다.
  (상담 작업들은 케어포에 접속하지 않아 무관 — consult_report.yml 에 로그인 없음)

실행: py -X utf8 -m audit.daily_pull        (carefor-auto 에서)
"""
from __future__ import annotations

import datetime
import io
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent      # carefor-auto
CC = ROOT.parent                                            # 클로드코드
REV_DIR = CC / "매출점검"
LOG = ROOT / "audit_results" / "daily_pull.log"


def log(msg: str) -> None:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(desc: str, args: list[str], cwd: pathlib.Path) -> int:
    """자식 프로세스 실행 — 출력을 로그에 그대로 남긴다(실패 원인을 나중에 볼 수 있게)."""
    log(f"--- {desc} 시작 ---")
    p = subprocess.run(args, cwd=str(cwd), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True,
                       encoding="utf-8", errors="replace")
    for ln in (p.stdout or "").splitlines():
        if ln.strip():
            log("   " + ln.rstrip())
    log(f"--- {desc} 종료 (exit={p.returncode}) ---")
    return p.returncode


def latest_combined() -> pathlib.Path | None:
    """가장 최근 합본 HTML. '최신 고정' 파일을 우선 쓴다(월이 바뀌어도 이름이 같다)."""
    fixed = REV_DIR / "매출점검_합본_최신.html"
    if fixed.exists():
        return fixed
    cands = sorted(REV_DIR.glob("매출점검_합본_*.html"))
    return cands[-1] if cands else None


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    log("===== DAILY PULL START =====")

    # [1/3] 매출 점검 (케어포 접속) — 인자 없으면 전 지점 수집 후 합본까지 만든다
    rc = run("[1/3] 매출 점검 수집", [sys.executable, "-X", "utf8", "revenue_check.py"], REV_DIR)
    if rc != 0:
        log(f"❌ 매출 점검 실패(exit={rc}) — 허브 배포를 건너뛴다(낡은/빈 데이터 배포 방지)")
        log("===== DAILY PULL END =====")
        return rc

    # [2/3] 공유 허브 갱신 — 방금 만든 합본을 올린다
    src = latest_combined()
    if not src:
        log("❌ 합본 HTML 을 찾지 못함 — 허브 배포 생략")
        log("===== DAILY PULL END =====")
        return 1
    log(f"허브에 올릴 합본: {src.name}")
    rc2 = run("[2/3] 공유 허브 배포(매출)",
              [sys.executable, "-X", "utf8", "-m", "audit.deploy_hub_ci",
               "--revenue-from", str(src)], ROOT)
    if rc2 != 0:
        log(f"❌ 허브 배포 실패(exit={rc2}) — 로컬 HTML 은 갱신됨")

    # [3/3] 차량 월별 수리비 — 매출이 끝난 뒤에 돈다(케어포 단일 로그인이라 순차 필수).
    #   실패해도 매출 결과는 이미 올라가 있으므로 여기서 멈추지 않고 기록만 남긴다.
    rc3 = run("[3/3] 차량 월별 수리비 수집",
              [sys.executable, "-X", "utf8", "-m", "audit.refresh_car_cost"], ROOT)
    if rc3 == 0:
        rc4 = run("[3/3] 공유 허브 배포(차량 수리비)",
                  [sys.executable, "-X", "utf8", "-m", "audit.deploy_hub_ci", "--carcost"], ROOT)
        if rc4 != 0:
            log(f"❌ 차량 허브 배포 실패(exit={rc4})")
    else:
        log(f"❌ 차량 수리비 수집 실패(exit={rc3}) — 허브의 차량 페이지는 종전 것이 유지된다")

    log("===== DAILY PULL END =====")
    return rc2


if __name__ == "__main__":
    raise SystemExit(main())
