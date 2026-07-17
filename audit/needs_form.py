# -*- coding: utf-8 -*-
"""욕구사정(case_YK) 상세 폼 HTML → 구조화 파서.

DOM 규칙 (표본 검증):
  - 섹션 제목: <div class="case_tit"><span>8. 자원이용 욕구</span>
  - 행: <tr><th>라벨</th><td> ... </td>
  - 선택지 박스: class 속성이 있는 <div flex> — 미선택 class="", 선택 class="spot_cN"
  - 선택 표시: 박스 안 <img src="/img_work/case_spot.png">
"""
from __future__ import annotations
import re
from bs4 import BeautifulSoup

SPOT = "case_spot.png"


def _txt(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()


def _is_optbox(d) -> bool:
    if not d.has_attr("class"):
        return False
    cls = d.get("class") or []
    return (not cls) or any(c.startswith("spot_c") for c in cls)


def _optname(d) -> str:
    child = d.find("div", recursive=False)
    name = _txt(child) if child else _txt(d)
    return name


def _free_of_selected(cell) -> list[str]:
    """선택된 옵션(예: '기타') 옆 괄호(bracket)의 자유입력 텍스트.

    DOM: <div flex><div flex class="spot_c3">기타<img spot></div><div flex bracket>척추협착증…</div></div>
    괄호는 옵션박스의 형제이므로, 바로 앞 형제 옵션박스가 선택된 경우만 취한다.

    괄호 안이 하위 선택지 목록일 수도 있다(치매 > 경도인지장애/중등도치매/중증치매).
    하위 선택지는 <div class=""><div>이름</div></div> 처럼 자식 div를 갖고,
    자유입력은 <div class="">척추협착증…</div> 처럼 텍스트가 직접 들어간다.
    """
    out = []
    for br in cell.find_all("div", attrs={"bracket": True}):
        prev = br.find_previous_sibling("div")
        if prev is None or not _is_optbox(prev):
            continue
        if not prev.find("img", src=lambda s: s and SPOT in s):
            continue  # 미선택 옵션의 괄호는 자유입력이 아니다
        boxes = [d for d in br.find_all("div", recursive=True) if _is_optbox(d)]
        if not boxes or any(d.find("div", recursive=False) for d in boxes):
            continue  # 하위 선택지 목록 → 자유입력 아님
        t = " ".join(x for x in (_txt(d) for d in boxes) if x)
        if t:
            out.append(t)
    return out


def parse_needs_form(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    secs: list[dict] = [{"sec": "(머리)", "rows": []}]
    cur = secs[0]

    for node in soup.find_all(["div", "table"]):
        cls = node.get("class") or []
        if node.name == "div" and "case_tit" in cls:
            cur = {"sec": _txt(node), "rows": []}
            secs.append(cur)
            continue
        if node.name != "table" or "case_tbl" not in cls:
            continue
        for tr in node.find_all("tr", recursive=True):
            # 한 행에 th/td 쌍이 여러 번 올 수 있다 (예: <th>키</th><td>..</td><th>체중</th><td>..</td>)
            pending: list[str] = []
            for cell in tr.find_all(["th", "td"], recursive=False):
                if cell.name == "th":
                    t = _txt(cell)
                    if t:
                        pending.append(t)
                    continue
                label = " / ".join(pending)
                pending = []
                opts, sel = [], []
                for d in cell.find_all("div", recursive=True):
                    if not _is_optbox(d):
                        continue
                    name = _optname(d)
                    if not name or len(name) > 60:
                        continue
                    opts.append(name)
                    if d.find("img", src=lambda s: s and SPOT in s):
                        sel.append(name)
                cur["rows"].append({"label": label, "opts": opts, "sel": sel,
                                    "free": _free_of_selected(cell), "text": _txt(cell)})
    return [s for s in secs if s["rows"]]


def sections(html: str) -> dict[str, dict]:
    """{섹션명: {행라벨: row}} — 라벨 중복 시 첫 행 유지."""
    out: dict[str, dict] = {}
    for s in parse_needs_form(html):
        d = out.setdefault(s["sec"], {})
        for r in s["rows"]:
            d.setdefault(r["label"], r)
    return out


def sec_get(secs: dict, key: str) -> dict:
    """'4. 신체상태' 처럼 앞부분만으로 섹션 찾기."""
    for k, v in secs.items():
        if k.startswith(key) or key in k:
            return v
    return {}


def row_get(sec: dict, label: str) -> dict | None:
    if label in sec:
        return sec[label]
    for k, v in sec.items():
        if k.replace(" ", "") == label.replace(" ", ""):
            return v
    return None
