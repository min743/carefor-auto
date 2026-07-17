# -*- coding: utf-8 -*-
"""욕구사정 점검 규칙 — 순수 로직 (엑셀·파일경로 의존 없음).

`make_needs_check_xlsx.py`(로컬 점검표 생성)와 `item20.py`(CI 자동판정)가 공유한다.
분리 이유: 점검표 쪽은 openpyxl·audit.deskpath 를 import 하는데, CI 러너의 판정 경로가
그걸 끌어오면 미커밋·미설치 모듈 하나로 통째로 죽는다(실제 사고 3회). 여기엔 `re` 만 쓴다.

규칙 자체의 확정 경위·오탐 튜닝 근거는 각 상수 옆 주석에 그대로 남겼다.
"""
from __future__ import annotations
import re

ADL = ["옷 벗고 입기", "세수하기", "양치질하기", "식사하기", "목욕하기",
       "체위변경 하기", "일어나 앉기", "옮겨 앉기", "화장실 사용하기", "몸단장하기"]
FAM = ["동거인", "자녀수", "관계", "경제 상태", "수발 부담", "거주 환경"]

# 섹션별 '체크박스 ↔ 판단근거' 대조 (2026-07-16 사용자 지정)
#   매뉴얼: "단순 체크리스트는 판단근거 작성 시만 인정" → 체크한 항목은 판단근거에 서술돼야 한다.
#   ★ 대조는 '행 라벨'이 아니라 '선택값'으로 한다.
#     지점은 라벨을 안 쓰고 값을 문장에 녹인다: 식사형태=일반식 → "현재 일반식 섭취가 가능하며",
#     치아상태=틀니 → "구강상태는 전체 틀니를 착용", 동거인=손자녀 → "손자녀와 함께 생활".
#     라벨로 찾으면 전건이 오탐이 된다(2026-07-16 실측 확인).
#   5.인지상태는 행 라벨이 '1'~'8'이라 대조 불가 → 판단근거 공란 여부만(BASIS_BLANK_ONLY).
#   ★ 대상은 '구체적 명사값'을 고르는 항목만 (정의는 DIS 아래 BASIS_ITEMS).
#     등급형(생활자립/완전자립/부분도움), 문장형(의사소통 '대부분 이해하고 의사를 표현한다'),
#     정도형(수발 부담 '아주 가끔 부담됨')은 지점이 요약·의역해 적어 원문 매칭이 불가능하다.
#     → 대조에 넣으면 전건 오탐(2026-07-16 실측 확인). 그래서 4.신체·6.의사소통 전체와
#       '수발 부담'·'경제 상태' 등은 제외한다.
BASIS_BLANK_ONLY = ["인지"]

# 판단근거 대조에서 제외할 선택값
#  - 일반값: '양호/유/무/없음'은 자유롭게 풀어써(예: '특이사항 없이 잘 지내심') 매칭이 무의미
#  - 12자 초과 = 문장형 선택지 → 요약해 적으므로 대조 불가
#  - 기본값: '일반식'은 수급자 대부분의 기본 상태라 판단근거에 따로 안 쓴다(죽식·다진식 같은
#    예외만 서술). 사용자 확정 2026-07-16 — 넣으면 33건 오탐.
GENERIC_SEL = {"양호", "유", "무", "없음", "있음", "해당없음", "해당 없음", "정상", "기타", "미상", "불명",
               "일반식"}
SEL_MAXLEN = 12

# 상위개념·축약 별칭 — 체크값 대신 이렇게 서술해도 기재된 것으로 인정(2026-07-16 실측 오탐 대응)
#   동거인 '자녀' → 근거엔 '아드님'/'따님'으로 구체화해 씀
#   '혈액투석' → 근거엔 '투석'으로 줄여 씀
SEL_ALIAS: dict[str, list[str]] = {
    "자녀": ["자녀", "아들", "딸", "아드님", "따님"],
    "손자녀": ["손자녀", "손자", "손녀"],
    "배우자": ["배우자", "남편", "아내", "부인", "할아버지", "할머니"],
    "자부": ["자부", "며느리"],
    "사위": ["사위"],
    "독거": ["독거", "혼자", "홀로", "1인"],
    "혈액투석": ["혈액투석", "투석"],
    "복막투석": ["복막투석", "투석"],
    # 질병·자원 표기 흔들림 (2026-07-16 실측 검증으로 확인된 오탐)
    "치매": ["치매", "알츠하이머"],                       # 근거엔 '알츠하이머'로 적기도
    "고혈압": ["고혈압", "혈압"],                          # '혈압약 복용 중'
    "재가복지": ["재가복지", "방문요양", "재가"],           # '케어링 방문요양 이용'
    "저작곤란": ["저작곤란", "저작", "씹"],                # '저작운동은 조금 곤란'
    "연하곤란": ["연하곤란", "연하", "삼킴"],
}


def pick(rows: list[dict], label: str, sec_kw: str | None = None) -> dict | None:
    """행 라벨로 찾기. sec_kw 주면 그 섹션 안에서만 (신서식은 '판단근거'가 섹션마다 중복)."""
    norm = label.replace(" ", "")
    for r in rows:
        if sec_kw and sec_kw not in r["sec"]:
            continue
        if r["label"].replace(" ", "") == norm:
            return r
    return None


def num(text: str, unit: str) -> float | None:
    m = re.search(r"([\d]+(?:\.\d+)?)\s*" + unit, text or "")
    return float(m.group(1)) if m else None


# '이용하고 있지 않으심' / '이용 중인 자원은 없으며' / '느끼지 못하고' 류의 부정 진술
NEG = re.compile(r"없|않|못|미이용|이용\s*안")


def stated_nonuse(basis: str, kw: re.Pattern) -> str:
    """판단근거에 해당 주제의 '미이용' 진술이 있는지.

    '명시'  = 주제를 언급한 절 안에 부정 표현이 있음
    '언급만' = 주제는 나오나 미이용 진술이 아님 (예: '지역사회 자원 연계가 필요할 수 있어')
    '없음'  = 주제 언급 자체가 없음
    """
    clauses = [c for c in re.split(r"[.。\n]|\s-\s|^-", basis or "") if c.strip()]
    hit = [c for c in clauses if kw.search(c)]
    if not hit:
        return "없음"
    return "명시" if any(NEG.search(c) for c in hit) else "언급만"


COM_KW = re.compile(r"지역사회")
HOSP_KW = re.compile(r"병원|의원|진료")


DIS = ["만성질환", "순환기계", "신경계", "근골격계", "정신, 행동장애",
       "호흡기계", "만성 신장질환", "기타 질환"]

# 체크박스 ↔ 판단근거 대조 대상 (구체적 명사값 항목만 — 위 주석 참조)
# 식사형태는 욕구사정에 '일반식/죽식' 2값만 존재(다진식은 급여기록 태그로만) — 사용자 확정 2026-07-16
BASIS_ITEMS: list[tuple[str, list[str]]] = [
    ("영양", ["식사형태", "식사시 문제점", "배설 양상 / 소변상태", "대변상태", "기저귀여부"]),
    ("구강", ["치아상태", "잇몸상태"]),
    ("질병", DIS),                       # 질환명 — dz 정규화(괄호제거·오타허용) 사용
    ("가족", ["동거인"]),
    ("자원", ["지역사회 자원"]),
]

# 참고 등급 — 지적(문제)이 아니라 표시만. 지점이 판단근거에 거의 안 쓰는 관행 항목이라
# 정식 지적으로 올리면 161건이 한꺼번에 나와 진짜 불일치(손자녀↔따님 등)가 묻힌다.
# (매뉴얼상으론 지적 가능 — 필요해지면 BASIS_ITEMS로 옮기면 됨. 사용자 확정 2026-07-16)
BASIS_REF_ITEMS: list[tuple[str, list[str]]] = [
    ("가족", ["거주 환경"]),
    ("자원", ["종교활동"]),
]
# 표기 흔들림 흡수: '골절 등 후유증' ↔ '골절 후유증', '좌측 유방암' ↔ '유방암'
_DZ_DROP = re.compile(r"[\s()]|등|퇴행성|만성|급성|좌측|우측|양측")


def dz_norm(s: str) -> str:
    return _DZ_DROP.sub("", s or "")


def dz_tokens(text: str) -> list[str]:
    # 진단명 구분자가 쉼표·마침표·가운뎃점으로 뒤섞인다: '고혈압. 퇴행성 관절염, 치매'
    return [t.strip() for t in re.split(r"[,、/·.;]|\s{2,}", text or "") if t.strip()]


def _lcs_len(a: str, b: str) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(len(a)):
        cur = [0] * (len(b) + 1)
        for j in range(len(b)):
            if a[i] == b[j]:
                cur[j + 1] = prev[j] + 1
                best = max(best, cur[j + 1])
        prev = cur
    return best


def dz_related(core: str, text: str) -> bool:
    """체크박스용: core가 text에 어순 무시·부분일치로 들어있나.

    복합병명은 어순이 흔들린다(인공관절무릎수술 ↔ 양쪽무릎 인공관절)므로 4글자 이상
    공통 연속부분이면 반영으로 본다. 3글자 이하는 '경색'↔'심근경색증' 같은 우연일치가
    잦아 substring(정확일치)만 인정한다.
    """
    if core in text:
        return True
    return len(core) >= 4 and _lcs_len(core, text) >= 4


def dz_typo(core: str, text: str) -> bool:
    """판단근거용: core가 거의 그대로(1글자 오차) 연속 등장하나. 부분일치는 불허.

    판단근거는 서술형이라 부분일치를 허용하면 '골절'·'후유증' 우연일치로 실제 누락을 놓친다.
    4글자 이상 병명의 오타(루마티스↔류마티스)만 흡수한다.
    """
    return len(core) >= 4 and _lcs_len(core, text) >= len(core) - 1


def _seg_covered(seg: str, pool: set[str]) -> bool:
    return bool(seg) and any((seg in c or c in seg or dz_related(seg, c)) for c in pool if c)


def dz_covered(tok: str, pool: set[str]) -> bool:
    core = re.sub(r"\(.*?\)", "", tok)
    n = dz_norm(core)
    # 1) 통짜 매칭 — 수식어+병명은 핵심 병명으로 잡힘(류마티스 관절염 → '관절염' 포함)
    if _seg_covered(n, pool):
        return True
    # 2) 공백분리 매칭 — 독립 2병명 나열은 조각별 확인(만성요통 협착증 → 요통·협착증 각각)
    segs = [dz_norm(s) for s in core.split() if len(dz_norm(s)) >= 2]
    return len(segs) >= 2 and all(_seg_covered(s, pool) for s in segs)


def dz_in_basis(tok: str, basis: str) -> bool:
    """판단근거는 어순이 달라진다: '인공관절(오른쪽)' ↔ '오른쪽 인공관절 수술 이력'.

    토큰을 조각으로 쪼개 조각이 전부 들어 있으면 기재된 것으로 본다.
    괄호 안은 부가설명이라 판단근거 대조에서 제외한다('관절염(무릎수술관련)' → '관절염'만 확인).
    """
    core = re.sub(r"\(.*?\)", " ", tok)
    # 병명 전체가 1글자 오차 내로 판단근거에 연속 등장하면 통과(루마티스↔류마티스, 뇌졸증↔뇌졸중)
    cn = dz_norm(core)
    if dz_typo(cn, basis):
        return True
    parts = [dz_norm(p) for p in re.split(r"\s+", core) if len(p.strip()) >= 2]
    if not parts:
        return cn in basis
    return all(p in basis for p in parts if p)


def basis_of(rows: list[dict], sec_kw: str) -> dict | None:
    return pick(rows, "판단근거", sec_kw=sec_kw)


def _norm_val(v: str) -> str:
    """괄호 부가설명 제거 + 공백 제거 — '인공관절(오른쪽)' → '인공관절'."""
    return re.sub(r"\(.*?\)", "", v or "").replace(" ", "").strip()


def _sel_values(rows: list[dict], sec_kw: str, labels: list[str]) -> list[tuple[str, str]]:
    """지정 항목에서 체크된 '구체적' 선택값 [(라벨, 값)]. 일반값·문장형 제외."""
    out: list[tuple[str, str]] = []
    for lab in labels:
        r = pick(rows, lab, sec_kw=sec_kw)
        if not r:
            continue
        for raw in (r.get("sel") or []) + (r.get("free") or []):
            # 한 선택값에 여러 개가 들어온다: '녹내장, 황반변성' → 각각 대조
            for v in dz_tokens(raw):
                v = v.strip().rstrip(".")
                if not v or v in GENERIC_SEL or len(v) > SEL_MAXLEN:
                    continue
                out.append((lab, v))
    return out


def basis_mentions(rows: list[dict], sec_kw: str, labels: list[str]) -> tuple[str, list[str]]:
    """체크된 선택값이 그 섹션 판단근거에 서술돼 있는지.

    반환 (판단근거상태, 미언급 목록)
      상태: 'OK' | '공란' | '행없음'(구서식 등 해당 섹션 없음 → 점검 대상 아님)
    체크된 구체값이 없으면 미언급 판정은 하지 않는다(오탐 방지).
    질병은 어순·오타가 흔들려 기존 dz_in_basis(괄호제거·LCS 오타허용)를 쓴다.
    """
    b = basis_of(rows, sec_kw)
    if b is None:
        return "행없음", []
    txt = (b.get("text") or "").strip()
    vals = _sel_values(rows, sec_kw, labels)
    if not vals:
        return ("공란" if not txt else "OK"), []
    if not txt:
        return "공란", [f"{lab}={v}" for lab, v in vals]
    base_dz, t = dz_norm(txt), txt.replace(" ", "")

    def hit(v: str) -> bool:
        for c in SEL_ALIAS.get(v, [v]):     # 상위개념·축약 별칭 허용
            if dz_in_basis(c, base_dz) if sec_kw == "질병" else (_norm_val(c) in t):
                return True
        return False

    miss = [f"{lab}={v}" for lab, v in vals if not hit(v)]
    return "OK", miss


def child_counts(row: dict | None) -> tuple[int | None, int | None]:
    t = (row or {}).get("text", "") or ""
    s = re.search(r"아들\s*:\s*(\d+)", t)
    dg = re.search(r"딸\s*:\s*(\d+)", t)
    return (int(s.group(1)) if s else None, int(dg.group(1)) if dg else None)


def check_one(a: dict, prev: dict | None, ctx: dict | None = None) -> tuple[dict, list[str]]:
    ctx = ctx or {}
    rows = a["rows"]
    probs: list[str] = []
    fmt = "신서식" if pick(rows, "수급자 상태") else "구서식"

    # 0) 낙상·욕창·인지를 먼저 써야 욕구사정을 쓸 수 있다 → 같은 회차에서 늦으면 문제
    rnd = ctx.get("round")
    order_txt = ""
    if rnd:
        late, none_, nodate = [], [], []
        for k in ("낙상", "욕창", "인지"):
            v = rnd.get(k, "")
            if v[:4].isdigit():
                if v > a["date"]:
                    late.append(f"{k} {v}")
            elif rnd.get(k + "_hasdoc"):
                nodate.append(k)   # 문서는 있는데 날짜 미표시 → 순서 판정 불가(오탐 방지)
            else:
                none_.append(k)    # 문서 고유번호 없음 = 실제 미작성
        order_txt = ", ".join(late + [f"{x} 미작성" for x in none_] + [f"{x} 날짜없음" for x in nodate])
        if late:
            probs.append(f"낙상·욕창·인지가 욕구사정({a['date']})보다 늦게 작성: {', '.join(late)}")
        if none_:
            probs.append(f"낙상·욕창·인지 미작성(욕구사정은 있음): {', '.join(none_)}")

    # 1) 키·체중
    h = pick(rows, "키")
    w = pick(rows, "체중")
    hv = num(h["text"], "cm") if h else None
    wv = num(w["text"], "kg") if w else None
    if hv is None:
        probs.append("키 미기재")
    if wv is None:
        probs.append("체중 미기재")
    same = ""
    if prev is not None:
        ph = num((pick(prev["rows"], "키") or {}).get("text", ""), "cm")
        pw = num((pick(prev["rows"], "체중") or {}).get("text", ""), "kg")
        if hv is not None and wv is not None and hv == ph and wv == pw:
            same = f"직전({prev['date']})과 동일"
            probs.append("키·체중 직전과 완전 동일")
    else:
        same = "비교불가(기간 첫 건)"

    # 2) 종교
    rel = pick(rows, "종교활동")
    rel_sel = ", ".join(rel["sel"]) if rel else ""
    if rel is None:
        probs.append("종교활동 행 없음")
    elif not rel["sel"]:
        probs.append("종교 미체크")

    # 3) 자원이용
    com = pick(rows, "지역사회 자원")
    reg = pick(rows, "정기진료")
    hosp = pick(rows, "진료 병원 / 병원명 (진료과)")
    basis = pick(rows, "판단근거", sec_kw="자원") or pick(rows, "판단근거(자원 이용)")
    com_sel = ", ".join(com["sel"]) if com and com["sel"] else ""
    reg_sel = ", ".join(reg["sel"]) if reg and reg["sel"] else ""
    hosp_txt = (hosp["text"] if hosp else "").strip()
    no_com = not com_sel
    no_hosp = (reg_sel == "무") or (not hosp_txt)
    basis_txt = basis["text"] if basis else "(판단근거 행 없음)"

    tel = pick(rows, "전화번호", sec_kw="자원")
    tel_txt = (tel["text"] if tel else "").strip()
    has_tel = bool(re.search(r"\d", tel_txt))
    if not reg_sel:
        probs.append("정기진료 미체크")
    elif reg_sel == "유" and not has_tel:
        probs.append("정기진료 '유'인데 전화번호 미기재")

    com_note = stated_nonuse(basis_txt, COM_KW) if no_com else "-"
    hosp_note = stated_nonuse(basis_txt, HOSP_KW) if no_hosp else "-"
    # 지역사회 자원 미이용이면 판단근거에 '이용하고 있지 않으심' 류의 진술이 반드시 있어야 한다.
    if no_com and com_note != "명시":
        probs.append(f"지역사회 자원 미이용인데 판단근거에 미이용 진술 {com_note}")

    # 질병상태 — 과거 병력 / 현 진단명 중 하나라도 공란이면 지적
    past = pick(rows, "과거 병력")
    curr = pick(rows, "현 진단명")
    blank = [n for n, r in (("과거 병력", past), ("현 진단명", curr)) if not (r or {}).get("text", "").strip()]
    if blank:
        probs.append("병력 공란: " + ", ".join(blank))

    # '현 진단명'에 적은 질환은 ①아래 체크박스(기타 자유입력 포함)에 들어가고 ②판단근거에도 나와야 한다
    # (2026-07-10 사용자 지정: 과거 병력은 대조 대상에서 제외)
    pool: set[str] = set()
    for x in DIS:
        r0 = pick(rows, x, sec_kw="질병") or {}
        pool |= {dz_norm(s) for s in r0.get("sel", [])}
        pool |= {dz_norm(s) for s in r0.get("free", [])}
    dz_basis = pick(rows, "판단근거(주요 질병상태)") or pick(rows, "판단근거", sec_kw="질병")
    dz_basis_txt = dz_norm((dz_basis or {}).get("text", ""))
    names = list(dict.fromkeys(dz_tokens((curr or {}).get("text", ""))))
    no_chk = [t for t in names if not dz_covered(t, pool)]
    no_bas = [t for t in names if not dz_in_basis(t, dz_basis_txt)]
    if no_chk:
        probs.append("현 진단명이 체크박스에 미반영: " + ", ".join(no_chk))
    if no_bas:
        probs.append("현 진단명이 판단근거에 미기재: " + ", ".join(no_bas))

    # 3-0) 체크박스 ↔ 판단근거 대조 — 체크한 항목은 판단근거에 서술돼 있어야 한다
    #      (매뉴얼: "단순 체크리스트는 판단근거 작성 시만 인정")
    b_blank, b_miss = [], []
    for sec_kw, labels in BASIS_ITEMS:
        stat, miss = basis_mentions(rows, sec_kw, labels)
        if stat == "공란":
            b_blank.append(sec_kw)
        if miss:
            b_miss.append(f"{sec_kw}({', '.join(miss)})")
    for sec_kw in BASIS_BLANK_ONLY:
        bb = basis_of(rows, sec_kw)
        if bb is not None and not (bb.get("text") or "").strip():
            b_blank.append(sec_kw)
    basis_blank_txt = ", ".join(b_blank)
    basis_miss_txt = " / ".join(b_miss)
    if b_blank:
        probs.append("판단근거 공란: " + basis_blank_txt)
    if b_miss:
        probs.append("체크했으나 판단근거 미언급 — " + basis_miss_txt)

    # 참고 등급(거주환경·종교) — 표시만 하고 문제로 세지 않는다
    ref_miss = []
    for sec_kw, labels in BASIS_REF_ITEMS:
        _, miss = basis_mentions(rows, sec_kw, labels)
        if miss:
            ref_miss.append(f"{sec_kw}({', '.join(miss)})")
    basis_ref_txt = " / ".join(ref_miss)

    # 4) 신체상태
    miss_adl = [x for x in ADL if not (pick(rows, x) or {}).get("sel")]
    if miss_adl:
        probs.append("신체상태 미체크: " + ", ".join(miss_adl))
    # 주간보호 수급자는 '생활자립'이어야 한다 (신서식에만 있는 항목)
    st = pick(rows, "수급자 상태")
    st_sel = ", ".join(st["sel"]) if st and st["sel"] else ("(미체크)" if st else "")
    if st is not None and st_sel != "생활자립":
        probs.append(f"수급자 상태가 생활자립이 아님: {st_sel}")

    # 5) 가족 및 환경상태
    miss_fam = []
    for x in FAM:
        r = pick(rows, x, sec_kw="가족")
        if r is None:
            miss_fam.append(f"{x}(행없음)")
        elif not r["sel"]:
            miss_fam.append(x)
    ch = pick(rows, "자녀수", sec_kw="가족")
    ch_sel = ", ".join(ch["sel"]) if ch and ch["sel"] else ""
    son, dau = child_counts(ch)
    ch_cnt = f"아들 {son} / 딸 {dau}" if (son is not None and dau is not None) else ""
    if ch_sel == "유" and son is None and dau is None:
        miss_fam.append("자녀수 '유'인데 명수 미기재")

    # 자녀수 '유'인데 아들0·딸0 → 같은 수급자의 다른 회차 값과 대조
    ch_cmp = ""
    if ch_sel == "유" and (son or 0) == 0 and (dau or 0) == 0:
        others = []
        for o in ctx.get("siblings", []):
            if o["date"] == a["date"]:
                continue
            os_, od = child_counts(pick(o["rows"], "자녀수", sec_kw="가족"))
            if (os_ or 0) or (od or 0):
                others.append(f"{o['date']}: 아들 {os_} / 딸 {od}")
        ch_cmp = " / ".join(others) if others else "타 회차에도 값 없음"
        probs.append(f"자녀수 '유'인데 아들0·딸0 (타 회차 → {ch_cmp})")

    car = pick(rows, "주수발자 / 유무", sec_kw="가족") or pick(rows, "유무", sec_kw="가족")
    car_sel = ", ".join(car["sel"]) if car and car["sel"] else ""
    rel_row = pick(rows, "관계", sec_kw="가족")
    rel_who = ", ".join(rel_row["sel"]) if rel_row and rel_row["sel"] else ""
    if not car_sel:
        miss_fam.append("주수발자 유무 미체크")
    elif car_sel == "유" and not rel_who:
        miss_fam.append("주수발자 '유'인데 관계 미체크")
    if "관계" in miss_fam and car_sel == "무":
        miss_fam.remove("관계")  # 주수발자 없으면 관계 공란이 정상
    if miss_fam:
        probs.append("가족·환경 미체크: " + ", ".join(miss_fam))

    # 지역사회 자원은 기초생활수급자만 해당 → 비기초가 '이용중'이면 지적
    econ = pick(rows, "경제 상태", sec_kw="가족")
    econ_sel = ", ".join(econ["sel"]) if econ and econ["sel"] else "(미체크)"
    if com_sel and "기초" not in econ_sel:
        probs.append(f"비기초({econ_sel})인데 지역사회 자원 이용중: {com_sel}")

    row = {
        "서식": fmt,
        "생년월일": ((pick(rows, "성별/생년월일") or {}).get("text", "")),
        "수급자상태": st_sel,
        "경제상태": econ_sel,
        "과거병력": (past or {}).get("text", "")[:40],
        "현진단명": (curr or {}).get("text", "")[:40],
        "병력_체크미반영": ", ".join(no_chk),
        "병력_근거미기재": ", ".join(no_bas),
        "판단근거_공란": basis_blank_txt,
        "판단근거_미언급": basis_miss_txt,
        "참고_판단근거_미언급": basis_ref_txt,
        "회차순서": order_txt or "정상",
        "자녀수_타회차": ch_cmp,
        "키": h["text"] if h else "",
        "체중": w["text"] if w else "",
        "키체중_직전대비": same,
        "종교": rel_sel,
        "신체상태_미체크": ", ".join(miss_adl),
        "가족환경_미체크": ", ".join(miss_fam),
        "자녀수": (ch_sel + (f" ({ch_cnt})" if ch_cnt else "")),
        "주수발자": car_sel + (f" — {rel_who}" if rel_who else ""),
        "지역사회자원": com_sel or "(미체크=미이용)",
        "정기진료": reg_sel,
        "병원명": hosp_txt,
        "전화번호": tel_txt,
        "자원_미이용": ("지역사회자원" if no_com else "") + ("+병원" if no_hosp else "") or "-",
        "지역사회자원_미이용진술": com_note,
        "참고_병원_미이용진술": hosp_note,
        "자원_판단근거_원문": basis_txt,
    }
    return row, probs
