# -*- coding: utf-8 -*-
"""항목 34② — 결과평가 반영 급여제공계획 30일 이내 재작성 판정.

소스: audit_results/결과평가_<지점>/index.json + 개별 HTML (audit.collect_result_eval 산출)
      + 1-1 스캔 결과(results)의 plans[].wd

판정 규칙 (사용자 확정 2026-07-16, 실제 폼 확인):
  결과평가 팝업 '급여제공결과평가 체크사항'의 c3/c4 —
    c3 = 상태변화 / 기능유지
    c4 = 30일 이내 재작성 / 필요없음 (급여계획 유지)

  · c3=기능유지  + c4=필요없음        → 충족 (재작성 불요)
  · c4=30일 이내 재작성               → 결과평가일 +30일 내 plans[].wd 있어야 충족
  · c3=상태변화  + c4=필요없음        → ★모순 = 오류 건 → 미충족 ("간혹 오류건이 나옴")
  · c3/c4 공란                        → 미기재 → 미충족

주의: 결과평가는 지점별 사전 수집 필요(py -X utf8 -m audit.collect_result_eval <지점>).
      수집물이 없으면 None 을 반환해 판정을 건너뛴다(없는 걸 미흡으로 찍지 않는다).
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

RES = Path(__file__).resolve().parent.parent / "audit_results"

C3_KEEP = "기능유지"
C3_CHANGE = "상태변화"
C4_NONE = "필요없음"          # '필요없음 (급여계획 유지)'
C4_REDO = "30일 이내 재작성"
REDO_DAYS = 30


def _d(s: str) -> date | None:
    m = re.search(r"(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})", s or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _plans_by_key(results: list[dict]) -> tuple[dict, dict]:
    """(pammgno→[작성일], 이름→[작성일]) — 동명이인 때문에 pammgno 우선."""
    by_id: dict[str, list[date]] = {}
    by_name: dict[str, list[date]] = {}
    for p in results or []:
        ds = sorted(d for d in (_d((pl or {}).get("wd") or "") for pl in (p.get("plans") or [])) if d)
        if p.get("pammgno"):
            by_id.setdefault(str(p["pammgno"]), []).extend(ds)
        by_name.setdefault(p.get("name", ""), []).extend(ds)
    return by_id, by_name


def judge(branch_name: str, results: list[dict], cutoff: str) -> dict | None:
    """항목 34② 판정. 수집물 없으면 None."""
    src = RES / f"결과평가_{branch_name}"
    idx = src / "index.json"
    if not idx.exists():
        return None
    try:
        from .make_result_eval_xlsx import parse_eval
    except ImportError:
        from audit.make_result_eval_xlsx import parse_eval

    data = json.loads(idx.read_text(encoding="utf-8"))
    cut = _d(cutoff)
    today = date.today()
    by_id, by_name = _plans_by_key(results)

    bad_conflict, bad_noredo, bad_blank, ok = [], [], [], 0
    for e in data.get("evals", []):
        f = src / e["file"]
        if not f.exists():
            continue
        p = parse_eval(f.read_text(encoding="utf-8"))
        ed = _d(p.get("작성일") or "") or _d(e.get("date") or "")
        if not ed or (cut and ed < cut):
            continue                      # 평가기간 밖
        c3, c4 = (p.get("c3") or "").strip(), (p.get("c4") or "").strip()
        who = f"{e['name']}({ed:%y.%m.%d})"

        if not c3 or not c4:
            bad_blank.append(who)
            continue
        if C4_NONE in c4:
            if C3_CHANGE in c3:
                bad_conflict.append(who)  # ★ 상태변화인데 재작성 필요없음 = 오류 건
            else:
                ok += 1                   # 기능유지 + 필요없음 → 충족
            continue
        if C4_REDO in c4:
            if ed + timedelta(days=REDO_DAYS) > today:
                ok += 1                   # 기한 진행중 → 과탐 방지
                continue
            ds = by_id.get(str(e.get("pammgno") or "")) or by_name.get(e["name"], [])
            if any(ed <= d <= ed + timedelta(days=REDO_DAYS) for d in ds):
                ok += 1
            else:
                bad_noredo.append(who)
            continue
        bad_blank.append(who)

    total = ok + len(bad_conflict) + len(bad_noredo) + len(bad_blank)
    if not total:
        return None

    def _cut(xs, n=5):
        return ", ".join(xs[:n]) + (f" 외 {len(xs)-n}건" if len(xs) > n else "")

    parts = []
    if bad_conflict:
        parts.append(f"상태변화인데 '재작성 필요없음' 체크(오류) {len(bad_conflict)}건 — {_cut(bad_conflict)}")
    if bad_noredo:
        parts.append(f"'30일 이내 재작성'인데 기한 내 계획 없음 {len(bad_noredo)}건 — {_cut(bad_noredo)}")
    if bad_blank:
        parts.append(f"체크사항 미기재 {len(bad_blank)}건 — {_cut(bad_blank)}")
    bad = len(bad_conflict) + len(bad_noredo) + len(bad_blank)
    return {
        "status": "양호" if bad == 0 else "미흡",
        "sub_status": {"②": "양호" if bad == 0 else "미흡"},
        "detail": f"[②30일 재작성] 결과평가 {total}건 중 충족 {ok}건"
                  + ((" — " + " · ".join(parts)) if parts else " — 전건 충족"),
    }
