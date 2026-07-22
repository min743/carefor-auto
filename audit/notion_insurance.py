# -*- coding: utf-8 -*-
"""항목 28③ 자동차종합보험 가입기간 유효 — 노션 차량현황으로 자동판정.

소스: 노션 차량현황(소속·보험사·보험증서). src.notion_client.fetch_insurance 가
  - result: 보험사·증서 있고 만기가 안 지난 차량 {차량번호: {branch, insurer, expiry, cert}}
  - errors: '만기일 지남' / '증서 파일 없음' / '보험사 없음' / '번호판 불일치 의심' 등
로 갈라 준다. 만기 = 증서 파일명의 날짜(YYYY-MM-DD).

토큰(NOTION_TOKEN)은 클라우드 전용이라 로컬에선 None 을 돌려 수기 판정을 유지한다.
판정 규칙(사용자 확정 2026-07-22):
  - 만기 지난 차량 있음        → 미흡 (가입기간 무효)
  - 증서·보험사 누락 등 오류    → 주의 (노션 입력 보완 필요, 단정 금지)
  - 전 차량 유효               → 양호 (노션에 입력돼 있으면 점수 부여)
  - 이 지점 차량이 노션에 없음  → 주의 (판정 불가 — 조용히 양호로 넘기지 않는다)
"""
from __future__ import annotations

import re
from datetime import date

EXPIRED = "만기일 지남"


def _core(s: str) -> str:
    """지점명 정규화: 공백·'지점'·끝의 '점' 제거 → '둔산점'/'대전둔산' 둘 다 '둔산' 계열로."""
    s = re.sub(r"\s+", "", s or "")
    s = s.replace("지점", "")
    return re.sub(r"점$", "", s)


def _match(notion_branch: str, branch_name: str) -> bool:
    a, b = _core(notion_branch), _core(branch_name)
    return bool(a and b and (a in b or b in a))


def judge_from(cars: dict, errors: list, branch_name: str, all_branches: list | None = None) -> dict:
    """순수 판정(테스트용) — fetch 결과를 받아 28③ 판정 dict 를 만든다.

    all_branches 를 주면 '어느 지점에도 매칭 안 되는' 차량(소속 공란·'공용'·'본부' 등)을 세어
    드러낸다. 안 그러면 그런 차량의 만기경과가 조용히 사라져 '양호'가 된다(검수 실증).
    """
    mine = {no: c for no, c in (cars or {}).items() if _match(c.get("branch", ""), branch_name)}
    myerr = [e for e in (errors or []) if _match(e.get("branch", ""), branch_name)]

    orphan = []
    orph_valid, orph_problem = [], []
    if all_branches:
        def _assigned(b: str) -> bool:
            return any(_match(b, x) for x in all_branches)
        orph_valid = [no for no, c in (cars or {}).items() if not _assigned(c.get("branch", ""))]
        orph_problem = [f"{e.get('car','?')}({e.get('reason','')})"
                        for e in (errors or []) if not _assigned(e.get("branch", ""))]
    exp = [e for e in myerr if EXPIRED in (e.get("reason") or "")]
    other = [e for e in myerr if EXPIRED not in (e.get("reason") or "")]

    # 소속 미지정 안내. ★유효한 미지정 차량은 숨겨도 문제가 없으니(보험 유효) 강등하지 않고 안내만 한다.
    #   반면 '문제 있는(만기경과·증서없음 등) 미지정 차량'은 이 지점 것일 수 있어 숨기면 안 됨 → 주의로 낮춘다.
    notes = []
    if orph_problem:
        notes.append(f"소속 미지정 '문제' 차량 {len(orph_problem)}건(이 지점 것일 수 있어 확인요망): "
                     + ", ".join(orph_problem[:4]) + (f" 외 {len(orph_problem)-4}건" if len(orph_problem) > 4 else ""))
    if orph_valid:
        notes.append(f"소속 미지정 유효차량 {len(orph_valid)}건(노션 '소속' 입력 권장): "
                     + ", ".join(orph_valid[:4]) + (f" 외 {len(orph_valid)-4}건" if len(orph_valid) > 4 else ""))
    orph_txt = (" ※" + " / ".join(notes)) if notes else ""

    if not mine and not myerr:
        return {"status": "주의",
                "detail": "[③자동차종합보험] 노션 차량현황에 이 지점 차량이 없어 판정 불가 — 수기 확인" + orph_txt}
    if exp:
        st = "미흡"
        head = (f"만기 경과 {len(exp)}건 — "
                + ", ".join(f"{e.get('car','?')}({e.get('expiry','?')})" for e in exp[:5])
                + (f" 외 {len(exp)-5}건" if len(exp) > 5 else ""))
    elif other:
        st = "주의"
        head = (f"유효 {len(mine)}대 · 확인요망 {len(other)}건 — "
                + ", ".join(f"{e.get('car','?')}({e.get('reason','')})" for e in other[:4])
                + (f" 외 {len(other)-4}건" if len(other) > 4 else ""))
    else:
        st = "양호"
        soon = sorted((c.get("expiry", "") for c in mine.values()))
        head = f"전 차량 유효 {len(mine)}대 (최근 만기 {soon[0] if soon else '?'})"
    if orph_problem and st == "양호":
        st = "주의"          # '문제 있는' 미매칭 차량의 만기경과 등이 숨지 않도록 양호로 두지 않는다
    return {"status": st, "detail": f"[③자동차종합보험] {head} (노션 차량현황 기준)" + orph_txt}


def judge(branch_name: str, all_branches: list | None = None, progress_cb=print) -> dict | None:
    """노션에서 읽어 28③ 판정. 토큰 없음·조회 실패면 None(호출측이 '주의'로 처리)."""
    try:
        from src.notion_client import fetch_insurance
        cars, errors = fetch_insurance()
        # judge_from 도 try 안에 둔다 — 노션 스키마가 바뀌어 errors 원소가 dict 가 아니면
        # e.get() 에서 터지는데, 밖에 두면 그 예외가 호출측으로 새어 ③ 미설정 → 자동만점이 된다.
        r = judge_from(cars, errors, branch_name, all_branches)
    except Exception as e:                     # 토큰 없음(로컬)·API 오류·스키마 변경
        progress_cb(f"  28③ 노션 보험 조회 건너뜀: {e}")
        return None
    progress_cb(f"  28③ 자동차보험(노션): {r['status']}")
    return r
