# -*- coding: utf-8 -*-
"""본부 공유 페이지용 이름 마스킹 (공단 게시 관행: 성·끝글자만 노출).

지점 대시보드(publish_hq_dashboard)와 점검 요약(summary_page)이 함께 쓴다.
원본 실명은 로컬 audit_results/ 에만 존재하고, 공개물에는 마스킹된 형태만 나간다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PLACEHOLDER = "[상세 명단은 지점 대시보드에서 확인]"

# 동명이인 표기를 벗겨 기본 이름을 얻는다: 이명옥A / 김○숙(동명이인) / 김경자(각) → 이명옥·김○숙·김경자
_NAME_TOKEN = re.compile(r"([가-힣]{2,4})(?:\([^)]{1,8}\))?[A-Za-z]?$")

# 마스킹 뒤에도 남은 '진짜 이름'만 잡는 게이트. '강○희'는 ○가 [가-힣]이 아니라 걸리지 않는다.
# '날짜만' 게이트는 두지 않는다 — 이름이 이미 마스킹돼 과잉 차단이 되기 때문.
# 오탐 주의: 괄호·쉼표는 평범한 문장에도 흔하다('복지(포상)', '제외, 확인용').
# 동명이인 표기는 괄호 안이 한 글자, 이름 나열은 보통 셋 이상 — 그 형태만 잡는다.
_GATE = [
    re.compile(r"[가-힣]{2,4}\s*\(\s*[가-힣]\s*\)"),                   # 이름(각) / 이름(여)
    re.compile(r"[가-힣]{2,4}\s*,\s*[가-힣]{2,4}\s*,\s*[가-힣]{2,4}"),  # 이름, 이름, 이름
    re.compile(r":\s*[가-힣]{2,4}\s*[,)]"),                            # 마커: 이름) / 이름,
    re.compile(r"\d{4}[-.]\d{1,2}\s+[가-힣]{2,4}"),                    # YYYY-MM 이름
    # ★위 ':' 게이트는 목록의 '첫' 항목만 본다 → 첫 이름이 마스킹되면 빗나가고 둘째 이후
    #   미등록 이름이 실명 그대로 공개본에 실린다(검수 실증: '(미작성: 강○표, 홍길동)').
    #   그래서 '마스킹된 이름(○) 뒤에 콤마로 이어지는 미마스킹 이름'을 따로 잡는다.
    #   전부 마스킹된 목록('강○표, 김○수')은 ○가 [가-힣]이 아니라 걸리지 않는다.
    #   실측: 실제 detail 104건 오탐 0건, '외 N명' 접미사 케이스까지 포착.
    re.compile(r"○[가-힣]?\s*,\s*[가-힣]{2,4}"),
]

_SKIP_KEYS = {"items", "item_results"}  # 항목 정의/판정 — 항목명(욕구사정 등)은 사람이름 아님


def mask_name(n: str) -> str:
    """강윤희→강○희, 김옥→김○, 남궁민우→남○○우."""
    n = (n or "").strip()
    if len(n) < 2:
        return n
    if len(n) == 2:
        return n[0] + "○"
    return n[0] + "○" * (len(n) - 2) + n[-1]


def base_name(s: str) -> str | None:
    """동명이인 접미사를 벗긴 기본 이름. 이름 형태가 아니면 None."""
    m = _NAME_TOKEN.fullmatch((s or "").strip())
    return m.group(1) if m else None


def names_from_obj(obj, acc: set[str]) -> None:
    """dict/list를 재귀 순회하며 사람 'name' 필드만 수집(수급자+직원)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _SKIP_KEYS:
                continue
            if k == "name" and isinstance(v, str) and (n := base_name(v)):
                acc.add(n)
            else:
                names_from_obj(v, acc)
    elif isinstance(obj, list):
        for x in obj:
            names_from_obj(x, acc)


def collect_from_audit_results(audit_dir: Path) -> set[str]:
    """audit_results/*.json 전체에서 사람 이름 집합을 만든다."""
    names: set[str] = set()
    for f in Path(audit_dir).glob("*.json"):
        try:
            names_from_obj(json.loads(f.read_text(encoding="utf-8")), names)
        except Exception:
            continue
    return names


def name_rx(names: set[str]):
    """알려진 이름들을 하나의 결합 정규식으로 (긴 이름 우선, 한글 경계)."""
    if not names:
        return None
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![가-힣])(?:{alt})(?![가-힣])")


def mask_known(s: str, rx) -> str:
    """문자열에 남아있는 알려진 이름을 마스킹 (이수→이수율 오치환 방지)."""
    if not s or rx is None:
        return s
    return rx.sub(lambda m: mask_name(m.group(0)), s)


def detail_for_share(text: str, rx) -> str:
    """detail 자유텍스트: 알려진 이름은 마스킹해 살리고, 미등록 이름이 남으면 통째 대체."""
    if not text:
        return text
    s = mask_known(text, rx)
    s = re.sub(r"\s{2,}", " ", s).strip(" ;,")
    for g in _GATE:
        if g.search(s):
            return PLACEHOLDER
    return s
