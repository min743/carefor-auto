# -*- coding: utf-8 -*-
"""항목 18(정보제공) — 노인장기요양보험 공개조회로 '정보 게시율' 수집·판정.

소스: longtermcare.or.kr 공개조회 (로그인·인증서 불필요, 케어포와 무관)
  1) selectLtcoSrchDetail.web?ltcAdminSym=<기관기호>&adminPttnCd=B03
       → 상단에 "게시율: 재가노인복지시설 주야간보호 (100%)" 를 포털이 직접 노출한다.
  2) selectOrgRatDetail?ltcAdminSym=...&adminPttnCd=B03  ('항목보기' 링크)
       → 게시항목별 게시여부(Y/N)·최종변경일 표. 게시율의 산정 근거.

기관기호 = config.yaml 의 branches[].ctmnumb 를 그대로 쓴다.
  케어포 센터코드와 장기요양기관기호가 동일한 값임을 4지점 전부 실측 확인(2026-07-17):
    청주 오창점 24311001003 / 둔산점 23017000602 / 서구점 23017000617 / 천안점 24413000644
  → 조회 결과의 기관명·주소가 각 지점과 일치. 별도 하드코딩 없이 config 단일 출처를 쓴다.
  (기관기호·기관명·게시율은 공개정보 — 개인정보 아님)

adminPttnCd: 급여종류별로 게시율이 따로 매겨진다(B01 방문요양 / B02 방문목욕 / B03 주야간보호 /
  B04 단기보호). 우리 지점은 전부 주야간보호(B03) 평가 대상이라 B03 만 본다.
  ⚠️ 미제공 급여종류는 게시율이 0% 로 나온다(서구점은 주야간보호만 제공 → B01/B02 가 0%).
     0% 를 '미게시'로 오독하지 않도록 '제공서비스'에 주야간보호가 있을 때만 판정한다.

산출: audit_results/롱텀공개_<지점>.json
  ⚠️ "branch"·"item_results" 키를 쓰지 않는다 — 최상위 *.json 을 글롭해 지점 결과로 읽는
     collector._write_dashboard_data() / sheet_upload.build_payload() 가 이 파일을 지점 결과로
     오인해 덮어쓰는 사고(bafe3b3)를 피하기 위해서다. 두 곳 모두 "item_results" 유무로 거르지만,
     여기서도 애초에 그 키를 만들지 않아 두 겹으로 막는다.

사용: py -X utf8 -m audit.collect_ltc_public [지점...]   (인자 없으면 4지점 전체)
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

RES = Path(__file__).resolve().parent.parent / "audit_results"
BASE = "https://www.longtermcare.or.kr/npbs/r/a/201"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

PTTN_DAYCARE = "B03"          # 재가노인복지시설 주야간보호
SVC_DAYCARE = "주야간보호"
REQ_DELAY = 2.0               # 공개 포털 예의 — 조회 사이 간격

# 게시항목 중 매뉴얼(no=18 ①)이 이름으로 명시한 '홈페이지 등록항목'.
# 이 항목이 'N' 이면 매뉴얼 문언상 명백한 미게시 → 미흡.
# 반대로 '현원정보'·'프로그램 운영' 은 게시율에는 들어가지만 매뉴얼이 명시하지 않았고,
# 3~6개월 미갱신만으로도 떨어진다 → 이것만 빠졌으면 미흡으로 찍지 않고 '주의'.
MANUAL_ITEMS = {
    "홈페이지 주소",
    "교통편",
    "주차시설",
    "전문인배상책임보험",
    "손해배상책임보험",              # 매뉴얼의 '화재/영업 배상책임보험'
    "장기요양급여 이용계약에 관한 사항",
    "비급여 항목",                   # 매뉴얼의 '비급여대상 항목별 비용'
    "사진",
}


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _parse_items(html: str) -> list[dict]:
    """'정보게시항목' 표 → [{name, posted, changed}].

    표 구조가 <tr><td>게시항목</td><td>Y|N</td><td>YYYY-MM-DD</td></tr> 로 단순해 행 단위로 뜬다.
    (평문화 후 정규식으로 긁으면 표 캡션·헤더까지 항목명에 섞여 들어와
     '홈페이지 주소' 가 '…게시여부 최종변경일 홈페이지 주소' 가 된다 → MANUAL_ITEMS 매칭 실패.)
    """
    seg = html[html.find("정보게시항목"):]
    seg = seg[:seg.find("</table>")] if "</table>" in seg else seg
    items = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", seg, re.S):
        tds = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip()
               for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(tds) == 3 and tds[1] in ("Y", "N") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", tds[2]):
            items.append({"name": tds[0], "posted": tds[1] == "Y", "changed": tds[2]})
    return items


def _get(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA, "Referer": f"{BASE}/selectLtcoSrch.web"},
                     timeout=30)
    r.raise_for_status()
    return r.text


def fetch_branch(sym: str, pttn: str = PTTN_DAYCARE) -> dict:
    """기관기호 → 게시율·게시항목. 네트워크 예외는 호출자가 처리."""
    detail_url = (f"{BASE}/selectLtcoSrchDetail.web?ltcAdminSym={sym}&adminPttnCd={pttn}"
                  "&paymtVltClsfcTypeCd=&paymtVltClsfcTypeCdSusi=&paymtVltMgmtNo="
                  "&vltMgmtYyyy=&aTab=11&paymtVltMgmtNoOld=&paymtVltMgmtNo2=")
    t = _text(_get(detail_url))

    m = re.search(r"게시율\s*:\s*([^(]{0,40})\((\d{1,3})\s*%\)", t)
    inst = re.search(r"장기요양기관\s+(.{0,60}?)\s+주소\s+(.{0,80}?)\s+전화번호", t)
    svc = re.search(r"제공서비스\s+(.{0,150}?)\s+통합재가급여", t)

    time.sleep(REQ_DELAY)
    items = _parse_items(_get(f"{BASE}/selectOrgRatDetail?ltcAdminSym={sym}&adminPttnCd={pttn}"))

    return {
        "ltc_admin_sym": sym,
        "admin_pttn_cd": pttn,
        "inst_name": inst.group(1).strip() if inst else None,
        "inst_addr": inst.group(2).strip() if inst else None,
        "services": svc.group(1).strip() if svc else None,
        "rate_label": m.group(1).strip() if m else None,
        "rate": int(m.group(2)) if m else None,
        "items": items,
        "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def collect(branch_name: str, sym: str) -> dict:
    d = fetch_branch(sym)
    d["branch_name"] = branch_name          # ⚠️ "branch" 아님 (위 주석 참고)
    RES.mkdir(parents=True, exist_ok=True)
    (RES / f"롱텀공개_{branch_name}.json").write_text(
        json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return d


# ─────────────────────────────────────────────────────────────────────────────
# 판정 — 항목 18 ①
#
# ★ 게시율 100% 를 '양호' 로 두지 않는 이유 (실측 확인, 2026-07-17):
#   매뉴얼 ①은 (가)지자체 신고항목(주소·전화번호·인력현황·시설현황·급여종류) 과
#   (나)홈페이지 등록항목(이용계약·사진·홈페이지주소·비급여비용·교통편·주차시설·보험) 을
#   '모두' 게시해야 인정한다. 그런데 포털의 게시율 산정 항목은 실제로 아래 10개뿐이고
#     홈페이지주소·교통편·주차시설·전문인배상책임보험·손해배상책임보험·
#     장기요양급여 이용계약에 관한 사항·현원정보·비급여 항목·프로그램 운영·사진
#   (가)의 인력현황·시설현황·주소·전화번호는 게시율에 들어가지 않는다(별도 탭 표시).
#   즉 게시율은 매뉴얼 항목집합의 부분집합 → 100% 라도 ① 충족을 자동으로 단정할 수 없고,
#   '변경 시 수정'(현행성)은 애초에 자동 확인이 불가능하다.
#   → 100% 는 '주의(자동확인 범위는 전부 충족 · 나머지는 수기)' 로 둔다. 선례: 33③·34③.
# ─────────────────────────────────────────────────────────────────────────────
def _uncollected(why: str) -> dict:
    return {"status": "주의", "sub_status": {"①": "주의"},
            "detail": f"[①정보게시] 판정 보류 — {why}. 롱텀 공개조회 게시율 확인 후 수기 판단요망"}


def judge18(branch_name: str) -> dict | None:
    """항목 18① 판정.

    수집 실패·파싱 실패는 None(조용한 스킵)이 아니라 '주의' 를 낸다 — 18 은 원래 method:"manual"
    (항상 사람이 보는 항목)이었다. None 을 내면 대시보드가 '수집전' 으로만 표시해
    '사람이 봐야 한다'는 신호가 오히려 약해진다. 선례: item20.judge()(조용한 스킵 금지, 사용자 확정).
    단 '주야간보호 미제공 기관' 은 진짜 해당없음이라 None(스킵).
    """
    src = RES / f"롱텀공개_{branch_name}.json"
    if not src.exists():
        return _uncollected("롱텀 공개조회 미수집(롱텀공개_*.json 없음 — 수집 스텝 실패 가능성)")
    try:
        d = json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _uncollected("수집물을 읽을 수 없음")

    rate, items = d.get("rate"), d.get("items") or []
    services = d.get("services")
    if rate is None:
        return _uncollected("게시율을 읽지 못함(포털 화면 변경 가능성)")
    if not services:
        # '제공서비스' 파싱 실패를 '주야간보호 미제공' 으로 오독해 조용히 넘기지 않는다
        return _uncollected("제공서비스를 읽지 못해 주야간보호 대상 여부 확인 불가")
    # 주야간보호 미제공 기관이면 B03 게시율 0% 가 '미게시'가 아니라 '해당없음' → 판정하지 않는다
    if SVC_DAYCARE not in services:
        return None

    head = f"[①정보게시] 롱텀 공개조회 게시율 {rate}% ({d.get('rate_label') or '주야간보호'}, {len(items)}개 항목)"

    # 자체 검증 — 파싱한 항목 Y/N 이 포털이 제시한 게시율과 맞아야 한다.
    # 포털 표 구조가 바뀌어 항목을 놓치면(=조용한 오판) 여기서 걸려 '주의' 로 빠진다.
    if not items or abs(round(sum(1 for i in items if i.get("posted")) / len(items) * 100) - rate) > 1:
        return {"status": "주의", "sub_status": {"①": "주의"},
                "detail": head + " — 게시항목 표 파싱이 게시율과 불일치(포털 화면 변경 가능성)"
                                 " → 자동판정 보류, 수기 확인요망"}

    missing = [i["name"] for i in items if not i.get("posted")]
    hard = [n for n in missing if n in MANUAL_ITEMS]      # 매뉴얼 명시 항목 미게시
    soft = [n for n in missing if n not in MANUAL_ITEMS]  # 현원정보·프로그램운영 등
    if hard:
        return {"status": "미흡", "sub_status": {"①": "미흡"},
                "detail": head + f" — 매뉴얼 명시항목 미게시 {len(hard)}건: {', '.join(hard)}"
                                 + (f" · 그 외 미게시: {', '.join(soft)}" if soft else "")}
    if soft:
        return {"status": "주의", "sub_status": {"①": "주의"},
                "detail": head + f" — 미게시 {len(soft)}건({', '.join(soft)}). 매뉴얼 명시항목은 아니나"
                                 " 게시율 하락 요인이라 갱신 확인요망(자동 미흡 아님)"}
    return {"status": "주의", "sub_status": {"①": "주의"},
            "detail": head + " — 홈페이지 등록항목 전건 게시. 다만 게시율 산정에서 빠지는"
                             " 지자체 신고항목(인력현황·시설현황)과 '변경 시 수정'(현행성)은"
                             " 자동확인 불가 → 현장 확인요망"}


def main() -> int:
    import os

    try:
        from src.config import Config, config_path
    except ImportError:
        print("ERROR: src.config 를 불러올 수 없습니다.")
        return 1

    # CI 에서는 config.yaml 이 Secrets 로만 들어온다. 이 스크립트는 로그인이 없어(공개조회)
    # 34②③·20① 같은 headless 래퍼가 필요 없고, 기관기호를 얻을 config 만 있으면 된다
    # → 래퍼 대신 여기서 직접 CONFIG_YAML 을 파일로 떨군다(다른 스텝 실행 순서에 의존하지 않게).
    cfg_yaml = os.environ.get("CONFIG_YAML")
    if cfg_yaml:
        p = config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(cfg_yaml, encoding="utf-8")

    if not config_path().exists():
        print("ERROR: config.yaml 이 없습니다 (CONFIG_YAML 환경변수 미설정).")
        return 1
    cfg = Config.load(config_path())

    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    targets = [b for b in cfg.branches if not args or any(a in b.name for a in args)]
    if not targets:
        print(f"ERROR: 알 수 없는 지점 {args}")
        return 1

    failed = []
    for i, b in enumerate(targets):
        if i:
            time.sleep(REQ_DELAY)
        try:
            d = collect(b.name, b.ctmnumb)
        except Exception as e:
            print(f"  {b.name} 수집 실패: {e}", flush=True)
            failed.append(b.name)
            continue
        n_bad = sum(1 for x in d["items"] if not x.get("posted"))
        print(f"  {b.name}({b.ctmnumb}): 게시율 {d['rate']}% · 항목 {len(d['items'])}개 "
              f"· 미게시 {n_bad}개 → {d['rate_label']}", flush=True)

    if failed:
        # 실패했는데 0 으로 끝내면 CI 스텝이 초록으로 떠 실패가 로그 한 줄에 묻힌다
        # → 0 이 아닌 코드로 끝내고, 본 점검은 워크플로의 continue-on-error 가 계속 진행시킨다.
        #   (18① 은 수집물이 없으면 judge18 이 '주의(미수집)' 를 내므로 조용히 넘어가지도 않는다.)
        print(f"\n⚠️ 수집 실패: {failed} — 해당 지점 18①은 '주의(미수집)' 로 표시됩니다(점검은 계속).")
        return 1
    print("\n✅ 롱텀 공개조회 수집 완료 (18번 판정 가능)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
