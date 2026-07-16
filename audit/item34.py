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


# ─────────────────────────────────────────────────────────────────────────────
# 항목 34③ — 급여제공기록지 월 1회 이상 제공 (2점)
#
# 소스: audit_results/청구발송_<지점명>.json (audit.collect_billing_history 산출)
#       7-1 본인부담금 청구관리 → '청구서 발송' → '청구서 발송이력' 의 [9]급여제공기록지 열
#       = '포함'(기록지 동봉 발송) / '제외'(기록지 없이 발송)
#
# 판정 규칙 (사용자 확정 2026-07-16):
#   1. 대상 = 청구서 발송되는 수급자 전원 (제외 없음)
#   2. 충족 = 수급자별로 '포함' 발송 이력이 1건이라도 있으면 OK
#   3. ★중복 발송 이력이 실재함(같은 사람·같은 달 최대 8건 관측) → 수급자 단위로 dedup 후 카운트.
#      중복 자체는 지적 대상이 아니다.
#   4. ★'제외'만 있는 수급자 = '미흡' 확정 금지. 지점이 수기 서명부를 보관하면 충족이므로
#      '주의(확인요망 — 수기 서명부 확인)' 로만 표시한다. (선례: 33③ 상담일지+요양기록지 확인요망)
#      → 이 판정은 어떤 경우에도 '미흡' 을 내지 않는다.
#
# 수집물이 없으면 None 을 반환해 판정을 건너뛴다(없는 걸 미흡으로 찍지 않는다).
# ─────────────────────────────────────────────────────────────────────────────
INCLUDED = "포함"
EXCLUDED = "제외"


def _ym(s: str) -> tuple[int, int] | None:
    """'2026.03' / '202603' → (2026, 3)."""
    m = re.search(r"(\d{4})[.\-]?(\d{1,2})", s or "")
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    return (y, mo) if 1 <= mo <= 12 else None


def judge3(branch_name: str, cutoff: str) -> dict | None:
    """항목 34③ 판정. 수집물 없으면 None."""
    src = RES / f"청구발송_{branch_name}.json"
    if not src.exists():
        return None
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    cut = _d(cutoff)
    cut_ym = (cut.year, cut.month) if cut else None

    # 수급자 단위 dedup — 이름별로 '포함'/'제외' 이력 유무만 집계(중복 횟수는 무시)
    per: dict[str, dict] = {}
    for r in data.get("rows") or []:
        name = (r.get("수급자") or "").strip()
        rec = (r.get("기록지") or "").strip()
        if not name or rec not in (INCLUDED, EXCLUDED):
            continue
        ym = _ym(r.get("청구년월") or "")
        if cut_ym and ym and ym < cut_ym:
            continue                      # 평가기간 밖 청구년월
        st = per.setdefault(name, {"inc": 0, "exc": 0, "months": set()})
        if rec == INCLUDED:
            st["inc"] += 1
        else:
            st["exc"] += 1
        if ym:
            st["months"].add(ym)

    total = len(per)
    if not total:
        return None

    ok = sorted(n for n, st in per.items() if st["inc"] > 0)
    warn = sorted(n for n, st in per.items() if st["inc"] == 0)   # '제외'만 = 확인요망

    def _cut(xs, n=5):
        return ", ".join(xs[:n]) + (f" 외 {len(xs)-n}명" if len(xs) > n else "")

    status = "주의" if warn else "양호"
    detail = f"[③기록지 월1회 제공] 청구서 발송 수급자 {total}명 중 기록지 '포함' 발송 {len(ok)}명"
    if warn:
        detail += (f" · 기록지 '제외'만 {len(warn)}명 → 수기 서명부 확인요망(자동 미흡 아님) — "
                   + _cut(warn))
    else:
        detail += " — 전원 충족"
    return {"status": status, "sub_status": {"③": status}, "detail": detail}
