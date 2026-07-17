# -*- coding: utf-8 -*-
"""수집한 정비이력 + 타이어 사이즈 → 차량관리 시트(_정비이력·_타이어 탭) 업로드.

흐름: collect_car_maintenance(케어포 수집) → ocr_car_invoice(OCR) → **이 스크립트** → 차량관리 앱.

실행: py -X utf8 -m audit.push_maintenance_to_sheet [--dry]
전제: audit_results/정비이력_<지점>/index.json 이 있어야 함(먼저 수집·OCR 돌릴 것).

⚠️ 조인키는 **차량번호**다. 앱·시트는 차량번호 기준인데 케어포 정비이력은 차량명 기준이라
   차량번호가 유일한 연결고리다(수집기가 carnumb 를 같이 받아둔 이유).
⚠️ **수리비는 올리지 않는다** — OCR 추정치인데다 첨부 없는 정비건은 0 으로 보여 실제보다 적게
   나온다(둔산 첨부율 10%). 금액은 엑셀 리포트에서만 본다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

from audit.ocr_car_invoice import find_sizes
from audit.parts_history import find_parts, is_repair

sys.stdout.reconfigure(encoding="utf-8")
RES = Path(__file__).resolve().parent.parent / "audit_results"
# 정비이력 전용 웹앱(같은 시트에 bound). 차량 데이터용 웹앱과 **별도**다 —
# 기존 차량관리 스크립트는 시트에 bound 라 scriptId 를 API 로 알 수 없어(Drive 목록에 안 뜬다)
# 코드를 못 고친다. 그래서 정비이력만 담당하는 스크립트를 따로 띄웠다. 기존 것은 손대지 않는다.
API = ("https://script.google.com/macros/s/"
       "AKfycbwtdykb6e2NGRoP6pBJVCYoh2xP5uA3ZpOMoogUIAZ0hlCjP4P48-dvq4kAmGS1uwXqFw/exec")


def norm_num(s: str) -> str:
    """차량번호 표기 흔들림 흡수 — 공백 제거만(하이픈은 실제 번호에 없다)."""
    return re.sub(r"\s+", "", str(s or ""))


def fmt_tire(size: str) -> str:
    """'215/70R16' → '215 / 70R / 16' (사용자 요청 형식). 없으면 호출하지 않는다(공란)."""
    m = re.fullmatch(r"(\d{3})/(\d{2})R(\d{2})", size or "")
    return f"{m.group(1)} / {m.group(2)}R / {m.group(3)}" if m else (size or "")


def build():
    history, tires = [], []
    for d in sorted(RES.glob("정비이력_*")):
        data = json.loads((d / "index.json").read_text(encoding="utf-8"))
        branch = data["branch"]
        # ★ 차량명으로 번호를 되찾으면 안 된다 — **같은 이름의 차가 2대 있다**(둔산 '레이' 46다7239·134노2060).
        #   이름을 키로 dict 를 만들면 하나가 덮어써져서 55건이 통째로 **다른 차 이력으로 붙는다**.
        #   레코드마다 numb 가 이미 들어 있으니(수집 시 차량별로 돌며 넣었다) 그걸 그대로 쓴다.
        best: dict[str, tuple[str, str, str]] = {}   # 차량번호 → (사이즈, 근거정비일, 차량명)
        for r in data["records"]:
            car = r["car"]
            num = norm_num(r.get("numb"))
            if not num:
                print(f"  ⚠️ 차량번호 없음 → 건너뜀: {branch} {car}")
                continue
            # \uC218\uB9AC\uB294 \uC804\uBD80, \uC810\uAC80\uC740 **\uC810\uAC80\uD45C(\uCCA8\uBD80)\uAC00 \uC788\uB294 \uAC83\uB9CC** \uC62C\uB9B0\uB2E4(\uC0AC\uC6A9\uC790 \uBC29\uCE68).
            # \uC810\uAC80 \uC0C1\uC6A9\uAD6C 273\uAC74\uC744 \uADF8\uB300\uB85C \uB123\uC73C\uBA74 \uC2E4\uC81C \uC218\uB9AC\uAC00 \uBB3B\uD78C\uB2E4.
            nfile = len(r.get("files") or [])
            if not is_repair(r.get("desc", "")) and not nfile:
                continue
            parts = [h["part"] for h in find_parts(r.get("desc", ""))]
            history.append([branch, num, car, r["date"], r.get("type", ""),
                            (r.get("desc") or "").replace("\u200E", "").strip()[:500],
                            ",".join(parts), nfile,
                            "\n".join(r.get("files_url") or []),   # 드라이브 링크(caring.co.kr 한정 공개)
                            "\n".join(r.get("files") or [])])      # 원본 파일명 — 앱에서 링크 텍스트로 쓴다
            # 타이어 사이즈는 OCR(첨부) + 케어포 정비내역 텍스트 양쪽에서 — 한쪽만 있는 차량이 있다
            found = ((r.get("ocr") or {}).get("sizes_all") or []) + find_sizes(r.get("desc", ""))
            for s in found:
                # 같은 차량에 여러 사이즈가 잡히면 **가장 최근 정비일**의 것을 쓴다(교체됐을 수 있으므로)
                if num not in best or r["date"] > best[num][1]:
                    best[num] = (s, r["date"], car)
        for num, (size, date, car) in sorted(best.items()):
            tires.append([branch, num, car, fmt_tire(size), date])
    return history, tires


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="업로드 없이 미리보기만")
    args = ap.parse_args()

    history, tires = build()
    by_b = defaultdict(int)
    for h in history:
        by_b[h[0]] += 1
    print("업로드 대상")
    for b, n in sorted(by_b.items()):
        nt = sum(1 for t in tires if t[0] == b)
        print(f"  {b:<10} 정비 {n:>3}건 · 타이어 사이즈 {nt}대")
    print(f"  {'합계':<9} 정비 {len(history)}건 · 타이어 {len(tires)}대")
    print("\n타이어 사이즈")
    for t in tires:
        print(f"  {t[0][:2]} {t[2][:12]:<12} {t[1]:<10} {t[3]}  (근거 {t[4]})")

    if args.dry:
        print("\n--dry: 업로드 안 함")
        return
    body = json.dumps({"action": "syncMaintenance", "history": history, "tires": tires}).encode()
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=180))
    if not r.get("ok"):
        print(f"\n실패: {r.get('error')}")
        sys.exit(1)
    print(f"\n업로드 완료: {r['data']}")


if __name__ == "__main__":
    main()
