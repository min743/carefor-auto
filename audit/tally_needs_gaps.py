# -*- coding: utf-8 -*-
"""지점별 욕구사정 누락 항목 집계 (교육자료 근거용, 읽기 전용).

기존 needs_full_<지점>.json + case_grid_<지점>.json 에 check_one 을 돌려
문제(probs)를 항목 카테고리로 묶어 지점별 건수·비율을 낸다. 라이브 접속 없음.
실행: py -X utf8 -m audit.tally_needs_gaps
"""
from __future__ import annotations
import sys, json, re
from pathlib import Path
from collections import defaultdict
sys.stdout.reconfigure(encoding="utf-8")
from audit.needs_rules import pick, check_one

RES = Path(__file__).resolve().parent.parent / "audit_results"
EXCLUDE_BIRTH = {("이언년", "1935.12.22")}

# prob 문자열 → 교육 카테고리. 순서대로 첫 매치.
CATS = [
    ("회차순서(낙상·욕창·인지 선행)", re.compile(r"낙상·욕창·인지")),
    ("키·체중 미기재/복붙",           re.compile(r"^키 미기재|^체중 미기재|키·체중 직전")),
    ("종교활동 미체크",               re.compile(r"^종교")),
    ("정기진료 미체크/전화번호",       re.compile(r"정기진료")),
    ("지역사회자원 미이용 진술 누락",   re.compile(r"지역사회 자원 미이용")),
    ("비기초인데 자원 이용중",         re.compile(r"비기초")),
    ("병력 공란",                     re.compile(r"^병력 공란")),
    ("진단명 체크박스 미반영",         re.compile(r"체크박스에 미반영")),
    ("진단명 판단근거 미기재",         re.compile(r"진단명이 판단근거에 미기재")),
    ("판단근거 공란",                 re.compile(r"^판단근거 공란")),
    ("체크했으나 판단근거 미언급",     re.compile(r"판단근거 미언급")),
    ("신체상태(ADL) 미체크",          re.compile(r"^신체상태 미체크")),
    ("신체 판단근거 미서술/공란",      re.compile(r"^신체 판단근거")),
    ("수급자상태 생활자립 아님",       re.compile(r"수급자 상태가 생활자립")),
    ("가족·환경 미체크",              re.compile(r"^가족·환경 미체크")),
    ("자녀수 '유'인데 0명",           re.compile(r"자녀수 '유'인데 아들0")),
]


def categorize(p: str) -> str:
    for name, rx in CATS:
        if rx.search(p):
            return name
    return "기타: " + p[:24]


def run_branch(src: Path):
    d = json.loads(src.read_text(encoding="utf-8"))
    branch = d["branch"]
    gp = RES / f"case_grid_{branch}.json"
    grid = {}
    if gp.exists():
        grid = {x["pammgno"]: x["rounds"] for x in json.loads(gp.read_text(encoding="utf-8"))["people"]}
    cnt = defaultdict(int)          # 카테고리 → 문제 건수(사정 기준)
    ppl = defaultdict(set)          # 카테고리 → 해당 수급자 집합
    n_assess = n_people = 0
    for p in d["people"]:
        if "등급외" in (p.get("grade") or "") or not p.get("grade"):
            continue
        birth = ""
        if p["assess"]:
            bt = (pick(p["assess"][0]["rows"], "성별/생년월일") or {}).get("text", "")
            m = re.search(r"(\d{4}\.\d{2}\.\d{2})", bt)
            birth = m.group(1) if m else ""
        if (p["name"], birth) in EXCLUDE_BIRTH:
            continue
        n_people += 1
        rounds = {r["욕구"]: r for r in grid.get(p.get("pammgno", ""), []) if r["욕구"][:4].isdigit()}
        prev = None
        for a in p["assess"]:
            _, probs = check_one(a, prev, {"round": rounds.get(a["date"]), "siblings": p["assess"]})
            prev = a
            n_assess += 1
            seen = set()
            for x in probs:
                c = categorize(x)
                cnt[c] += 1
                ppl[c].add(p["name"])
                seen.add(c)
    return branch, n_people, n_assess, cnt, ppl


def main():
    results = []
    allcats = defaultdict(int)
    for src in sorted(RES.glob("needs_full_*.json")):
        results.append(run_branch(src))
    # 지점별 출력
    for branch, npl, nas, cnt, ppl in results:
        print(f"\n{'='*66}\n■ {branch} — 대상 {npl}명 / 사정 {nas}건")
        for c, n in sorted(cnt.items(), key=lambda x: -x[1]):
            rate = n / nas * 100 if nas else 0
            print(f"   {n:>4}건 ({rate:4.0f}%)  {len(ppl[c]):>3}명  {c}")
            allcats[c] += n
    # 전체 순위
    print(f"\n{'='*66}\n■ 4지점 합산 — 항목별 총 건수(교육 우선순위)")
    for c, n in sorted(allcats.items(), key=lambda x: -x[1]):
        print(f"   {n:>4}건  {c}")
    # JSON 저장(교육자료 생성기가 읽음)
    out = {"branches": [], "totals": dict(allcats)}
    for branch, npl, nas, cnt, ppl in results:
        out["branches"].append({
            "branch": branch, "people": npl, "assess": nas,
            "cats": {c: {"n": n, "people": len(ppl[c])} for c, n in cnt.items()},
        })
    (RES / "needs_gaps_tally.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[저장] {RES/'needs_gaps_tally.json'}")


if __name__ == "__main__":
    main()
