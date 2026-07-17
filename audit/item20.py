# -*- coding: utf-8 -*-
"""항목 20① — 종합 욕구사정 판정 (매뉴얼 5요건).

소스: audit_results/needs_full_<지점>.json (audit.collect_needs_full 산출)
      = 수급자별 욕구사정 상세 폼 전문. 수급자명·총평이 들어간 개인정보라 커밋 금지
        ([[pii-commit-guard]], .gitignore). CI 는 러너 안에서 매 실행 수집한다.

매뉴얼 20① 요건과 이 모듈의 판정 범위 (사용자 확정 2026-07-17):
  1. 연 1회 이상 정기 실시 → ★미판정(주의). 분모가 불완전하다 — 아래 '커버리지' 참조
  2. 세부 9개 항목            → 판정(확정)
  3. 서술형 총평              → 공란은 확정, 길이 미달은 ★주의 (기준 글자수의 근거가 없다)
  4. 작성자명                 → 판정(확정)
  5. 단순 체크리스트 불인정   → 체크값 ↔ 판단근거 대조. ★신서식만 판정(확정)

  ※ '현 진단명 ↔ 체크박스/판단근거', '자원 미이용 진술' 은 이 판정에 넣지 않는다(사용자 확정).
    점검표(make_needs_check_xlsx)에는 그대로 남아 있다 — 수기 확인용.

★ 구서식은 판정에서 제외한다
  구서식은 판단근거 행 라벨이 '판단근거(주요 질병상태)' 식이라 신서식 기준(섹션별 '판단근거')과
  다르고, 신서식이 거친 오탐 튜닝(GENERIC_SEL·SEL_ALIAS·dz_* )을 거치지 않았다
  (구강 매핑 오탐 31건 실측). 억지로 대조하면 오탐이 확정 미흡으로 나간다.
  → 제외하되 반드시 커버리지로 드러낸다. 기존 코드는 basis_of() 의 '판단근거' 정확일치가
    구서식 라벨을 못 찾아 '행없음'으로 조용히 통과시켰다(= 0건이라 깨끗해 보였다).

★ 커버리지를 반드시 노출하는 이유 (27번 rehab 선례)
  '판정분만 깨끗'을 '양호'로 읽으면 실제로는 안 본 것을 봤다고 오해한다.
  - 구서식 613건(4지점 합계)은 판정 제외 → detail 에 '판정 N건 / 전체 M건' 표기
  - 연1회는 분모 자체가 불완전 → 커버리지 미달인 한 '양호' 를 주지 않는다
    (수집 소스가 base scan 의 '욕구사정 보유자' 만 긁어와, 사정이 0건인 수급자는
     needs_full 에 아예 없다 = '연1회 미실시' 를 원리적으로 탐지할 수 없다.
     collect_needs_full 의 want 프리필터 제거 후 목록 전원이 모이면 그때 확정 판정으로 승격.)

수집물이 없으면 None 이 아니라 '주의' 를 낸다 — 조용한 스킵 금지(사용자 확정).
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    from .needs_rules import BASIS_BLANK_ONLY, BASIS_ITEMS, basis_mentions, basis_of, pick
except ImportError:  # 스크립트로 직접 실행할 때
    from audit.needs_rules import BASIS_BLANK_ONLY, BASIS_ITEMS, basis_mentions, basis_of, pick

RES = Path(__file__).resolve().parent.parent / "audit_results"

# 매뉴얼이 요구하는 세부 9개 항목 → 신서식 섹션 제목 키워드
# (섹션 제목은 '4. 신체상태(일상생활 동작 수행능력)' 처럼 번호·괄호가 붙어 부분일치로 본다)
NINE = ["영양", "구강", "질병", "신체", "인지", "의사소통", "가족", "주관적", "자원"]

# 총평 서술형 최소 길이.
# ★ 이 숫자에 매뉴얼 근거가 없다 → 넘겨도 '주의' 까지만 내고 절대 '미흡' 으로 올리지 않는다.
#   실측 분포(4지점 신서식 320건)는 1자·1자·35자 3건 뒤로 392자까지 비어 있다.
#   36~392 사이 어떤 값을 넣어도 결과가 같아, 그 구간의 낮은 쪽을 택했다.
TOTAL_MIN = 50


def _fmt_names(xs: list[str], n: int = 5) -> str:
    return ", ".join(xs[:n]) + (f" 외 {len(xs)-n}건" if len(xs) > n else "")


def check_needs_c(a: dict) -> tuple[list[str], list[str], list[str], list[str]]:
    """욕구사정 1건 → (확정 위반 요건명, 주의 요건명, 판단근거 공란 섹션, 미언급 섹션). 신서식 전제.

    make_needs_check_xlsx.check_one() 과 같은 규칙(needs_rules)을 쓰되 20① C 범위만 본다.
    ★ 반환은 '요건명' 만이다 — 위반한 선택값·질환명 원문을 detail 로 흘리지 않는다.
      그 원문(예: '질병(근골격계=관절염, 근골격계=요통)')이 본부 공유 살균기의
      '이름, 이름, 이름' 게이트에 걸려 detail 전체가 [상세 명단은…] 으로 통째 대체된다
      (= 커버리지 문구까지 사라진다). 상세는 점검표 xlsx 에서 본다.
    """
    rows = a["rows"]
    hard: list[str] = []
    soft: list[str] = []

    # (4) 작성자명 — 매뉴얼 필수기재
    if not (pick(rows, "작성자") or {}).get("text", "").strip():
        hard.append("작성자명")

    # (2) 세부 9개 항목
    secs = {r["sec"].replace(" ", "") for r in rows if r["sec"][:1].isdigit()}
    if [k for k in NINE if not any(k in s for s in secs)]:
        hard.append("9항목")

    # (3) 서술형 총평
    tp = next((r for r in rows if r["label"] == "총평" and "총평" in r["sec"]), None)
    txt = (tp or {}).get("text", "").strip()
    if tp is None or not txt:
        hard.append("총평")
    elif len(txt) < TOTAL_MIN:
        # 길이 기준의 근거가 없어 단정하지 않는다 — 사람이 보고 판단할 몫
        soft.append(f"총평 {len(txt)}자")

    # (5) 단순 체크리스트 불인정 — 체크값이 그 섹션 판단근거에 서술돼 있어야 한다
    blank, miss_secs = [], []
    for sec_kw, labels in BASIS_ITEMS:
        stat, miss = basis_mentions(rows, sec_kw, labels)
        if stat == "공란":
            blank.append(sec_kw)
        if miss:
            miss_secs.append(sec_kw)
    for sec_kw in BASIS_BLANK_ONLY:  # 인지: 행 라벨이 '1'~'8' 이라 값 대조 불가 → 공란만
        bb = basis_of(rows, sec_kw)
        if bb is not None and not (bb.get("text") or "").strip():
            blank.append(sec_kw)
    if blank:
        hard.append("판단근거공란")
    if miss_secs:
        hard.append("판단근거미언급")

    return hard, soft, blank, miss_secs


def judge(branch_name: str, cutoff: str, n_disc: int = 0) -> dict:
    """항목 20① 판정. 수집물이 없어도 None 이 아니라 '주의' 를 낸다(조용한 스킵 금지).

    n_disc: 낙상↔욕구사정 불일치 건수 — 매뉴얼 20① 요건이 아니라 참고로만 병기한다
            (판정 점수엔 미반영. 사용자 확정 2026-07-17).
    """
    ref = f" · [참고] 낙상↔욕구사정 불일치 {n_disc}건(20① 요건 아님, 수기 확인)" if n_disc else ""
    src = RES / f"needs_full_{branch_name}.json"
    if not src.exists():
        return {
            "status": "주의",
            "sub_status": {"①": "주의"},
            "detail": "[①종합 욕구사정] 판정 0건 — 욕구사정 상세 미수집(needs_full_*.json 없음) "
                      "→ 판정 불가. 수집 스텝 실패 여부 확인 필요(0건이라 양호가 아님)" + ref,
        }
    try:
        d = json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {"status": "주의", "sub_status": {"①": "주의"},
                "detail": f"[①종합 욕구사정] 판정 0건 — 수집물 읽기 실패({e}) → 판정 불가" + ref}

    n_new = n_old = 0
    hard_rows: list[str] = []
    soft_rows: list[str] = []
    ppl_bad: set[str] = set()
    ppl_judged: set[str] = set()
    by_req: dict[str, int] = {}
    by_sec: dict[str, int] = {}

    for p in d.get("people") or []:
        grade = p.get("grade") or ""
        if not grade or "등급외" in grade:
            continue                      # 등급외·등급미확인은 급여 대상이 아님
        for a in p.get("assess") or []:
            if pick(a["rows"], "수급자 상태") is None:
                n_old += 1                # 구서식 → 판정 제외(위 docstring 참조)
                continue
            n_new += 1
            ppl_judged.add(p["name"])
            hard, soft, blank, miss_secs = check_needs_c(a)
            # 이름은 '이름(YY.MM.DD)' 형태로만 — 살균기가 아는 이름은 '강○희' 로 마스킹해 살린다
            who = f"{p['name']}({a['date'][2:]})"
            if hard:
                hard_rows.append(who)
                ppl_bad.add(p["name"])
                for k in hard:
                    by_req[k] = by_req.get(k, 0) + 1
                for s in miss_secs:
                    by_sec[s] = by_sec.get(s, 0) + 1
            if soft:
                soft_rows.append(f"{who} {'·'.join(soft)}")

    n_all = n_new + n_old
    if not n_all:
        return {"status": "주의", "sub_status": {"①": "주의"},
                "detail": "[①종합 욕구사정] 판정 0건 — 수집물에 대상 사정이 없음 → 판정 불가" + ref}

    # 수집 시작일이 평가기간 시작일보다 늦으면 그 사이 사정은 아예 안 긁혀 있다.
    # (실측 2026-07-17: 4지점 모두 기본값 2024.07.31 로 수집됐는데 천안점 평가기간은
    #  2024.05.31 부터라 2개월이 통째로 비어 있다.) 조용히 넘기지 않고 커버리지로 드러낸다.
    got = (d.get("cutoff") or "").strip()
    gap = f" ※수집 시작 {got} > 평가기간 시작 {cutoff} — 그 사이 사정 미수집(재수집 필요)" \
        if (got and cutoff and got > cutoff) else ""

    # 연1회는 분모 불완전 → 확정 미흡 금지, 커버리지 미달인 한 '양호' 도 주지 않는다
    if hard_rows:
        status = "미흡"
    else:
        status = "주의"   # 연1회 미판정 + (있다면) 총평 주의

    cov = f"판정 {n_new}건 / 전체 {n_all}건 (커버리지 {n_new/n_all:.0%})"
    detail = (f"[①종합 욕구사정] {cov} — 구서식 {n_old}건은 판정 제외"
              "(신서식 기준 판단근거 대조가 구서식엔 오탐, 수기 확인 필요)"
              f" · 판정대상 {len(ppl_judged)}명 중 지적 {len(ppl_bad)}명")
    if hard_rows:
        req = " · ".join(f"{k} {v}건" for k, v in sorted(by_req.items(), key=lambda x: -x[1]))
        sec = ("(" + "·".join(f"{k} {v}" for k, v in sorted(by_sec.items(), key=lambda x: -x[1])) + ")"
               if by_sec else "")
        detail += (f" · 요건 위반 {len(hard_rows)}건 [{req}{sec}]"
                   f" — {_fmt_names(hard_rows)} (상세는 욕구사정 점검표 xlsx)")
    else:
        detail += " · 형식·판단근거 위반 없음"
    if soft_rows:
        detail += f" · 확인요망 {len(soft_rows)}건(총평 길이 기준은 근거 없어 미흡 아님) — {_fmt_names(soft_rows)}"
    detail += (" · 연1회 실시는 미판정(주의) — 수집이 '욕구사정 보유자'만 대상이라 "
               "사정 0건 수급자가 분모에서 빠짐. want 프리필터 제거 후 확정 판정 가능")
    detail += gap + ref
    return {"status": status, "sub_status": {"①": status}, "detail": detail}
