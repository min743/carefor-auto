# -*- coding: utf-8 -*-
"""
상담 공지 엑셀 생성 — 지점별 파일 + 전체 통합본

내용 (파일당 시트 2개):
  · 신규상담 미입력  : 신규상담 세부사항에서 '상담시트 입력 여부'=N (2026년 5월~)
  · 상담 대기명단    : 센터별 상담 대기 명단 (아웃콜 차수 + 기한 경과일)

실행:
  py -X utf8 consult_excel.py
  → 상담공지_엑셀/YYYY-MM-DD/ 폴더에 센터별 4개 + 전체본 1개 생성
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import consult_report as cr
import waitlist_report as wr

OUT_ROOT = Path(__file__).resolve().parent / "상담공지_엑셀"

HEADER_FILL = PatternFill("solid", fgColor="4472C4")
HEADER_FONT = Font(bold=True, color="FFFFFF")
URGENT_FILL = PatternFill("solid", fgColor="FFC7CE")   # 입소완료 미입력 / 기한 지남
TODAY_FILL = PatternFill("solid", fgColor="FFEB9C")    # 오늘 예정

MISS_COLS = ["센터명", "구분", "연월", "해당 주차", "상담일자", "급여개시일자", "고객 번호", "입소 여부", "AI 요약"]
WAIT_COLS = ["센터명", "아웃콜 차수", "예정일자", "기한 경과(일)", "연락처", "첫 상담일"]
SUMMARY_COLS = ["센터", "신규상담(누적)", "시트 미입력", "미입력률", "⚠️ 입소완료 미입력", "당월 미입력",
                "상담 대기", "기한 지남"]


def _style_sheet(ws, widths: list[int]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
    # 데이터 전체: 가운데 정렬 + 줄바꿈으로 셀 안에 맞춤
    center_wrap = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = center_wrap
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def add_summary_sheet(wb: Workbook, srows: list[dict]) -> None:
    ws = wb.create_sheet("요약")
    ws.append(SUMMARY_COLS)
    for s in srows:
        ws.append([s["center"], s["total"], s["miss"], s["rate"],
                   s["urgent"], s["recent"], s["wait"], s["overdue"]])
        if s["center"] == "합계":
            for cell in ws[ws.max_row]:
                cell.font = Font(bold=True)
    _style_sheet(ws, [13, 14, 12, 10, 17, 11, 10, 10])


def add_miss_sheet(wb: Workbook, rows: list[dict], ym: str) -> None:
    ws = wb.create_sheet("신규상담 미입력")
    ws.append(MISS_COLS)
    for r in rows:
        if r["admitted"] == "Y":
            kind = "⚠️ 입소완료 미입력"
        elif r["yearmonth"] == ym:
            kind = "당월 미입력"
        else:
            kind = "이전 미입력"
        ws.append([r["center"], kind, r["yearmonth"], r["week"], r["consult_date"],
                   r["start_date"], r["phone"], r["admitted"], r["summary"]])
        if r["admitted"] == "Y":
            for cell in ws[ws.max_row]:
                cell.fill = URGENT_FILL
    _style_sheet(ws, [12, 17, 12, 16, 12, 13, 14, 9, 80])


def add_wait_sheet(wb: Workbook, items: list[dict]) -> None:
    ws = wb.create_sheet("상담 대기명단")
    ws.append(WAIT_COLS)
    for it in items:
        over = it["overdue"]
        ws.append([it["center"], it["round"], it["due"],
                   over if over is not None else "", it["phone"], it["first"]])
        if over is not None and over > 0:
            for cell in ws[ws.max_row]:
                cell.fill = URGENT_FILL
        elif over == 0:
            for cell in ws[ws.max_row]:
                cell.fill = TODAY_FILL
    _style_sheet(ws, [12, 14, 14, 13, 15, 13])


def make_book(path: Path, srows: list[dict], miss: list[dict], wait: list[dict], ym: str) -> None:
    wb = Workbook()
    wb.remove(wb.active)
    add_summary_sheet(wb, srows)
    add_miss_sheet(wb, miss, ym)
    add_wait_sheet(wb, wait)
    wb.save(path)


def _summary_row(name: str, grp: list[dict], wait: list[dict], ym: str) -> dict:
    miss = [r for r in grp if r["sheet_entered"] == "N"]
    total = len(grp)
    return {
        "center": name,
        "total": total,
        "miss": len(miss),
        "rate": f"{round(len(miss) / total * 100)}%" if total else "-",
        "urgent": sum(1 for r in miss if r["admitted"] == "Y"),
        "recent": sum(1 for r in miss if r["yearmonth"] == ym),
        "wait": len(wait),
        "overdue": sum(1 for it in wait if it["overdue"] and it["overdue"] > 0),
    }


def generate(today: date | None = None) -> tuple[Path, list[Path], list[dict]]:
    """엑셀 파일들 생성. (출력폴더, [전체본, 센터별...], 센터별 요약) 반환."""
    today = today or date.today()

    # 데이터 로드 (공지 스크립트와 동일 소스)
    a_rows = cr.load_rows_from_webhook()
    miss_all = sorted((r for r in a_rows if r["sheet_entered"] == "N"),
                      key=lambda r: (r["center"], r["consult_date"]))

    w_raw = wr.load_rows()
    wait_all = []
    for row in w_raw[2:]:
        if len(row) >= 9 and row[0].strip():
            due_d = wr.parse_due(row[8])
            wait_all.append({
                "center": wr.parse_center(row[0]),
                "first": row[1].strip(),
                "round": wr.parse_round(row[7]),
                "due": row[8].strip(),
                "overdue": (today - due_d).days if due_d else None,
                "phone": wr.norm_phone(row[4]),
            })
    wait_all.sort(key=lambda it: (it["center"], -(it["overdue"] if it["overdue"] is not None else -999)))

    out_dir = OUT_ROOT / today.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)

    ym = f"{today.year}년 {today.month:02d}월"
    paths = []

    # 센터별 요약 계산
    summaries = []
    center_data = {}
    for short, full in cr.CENTER_ORDER:
        grp = [r for r in a_rows if r["center"] == full]
        miss = [r for r in miss_all if r["center"] == full]
        # 대기명단 센터명은 '둔산점'/'서구점' 식 — 공백 제거 후 접두 매칭
        wait = [it for it in wait_all if it["center"].replace(" ", "").startswith(short)]
        center_data[full] = (miss, wait)
        summaries.append(_summary_row(full, grp, wait, ym))
    total_summary = _summary_row("합계", a_rows, wait_all, ym)

    # 전체 통합본 (요약 시트에 센터별 + 합계)
    total_path = out_dir / f"전체_상담공지_{today:%Y%m%d}.xlsx"
    make_book(total_path, summaries + [total_summary], miss_all, wait_all, ym)
    paths.append(total_path)
    print(f"생성: {total_path.name}  (미입력 {len(miss_all)} / 대기 {len(wait_all)})")

    # 지점별 파일
    for s, (short, full) in zip(summaries, cr.CENTER_ORDER):
        miss, wait = center_data[full]
        p = out_dir / f"{full}_상담공지_{today:%Y%m%d}.xlsx"
        make_book(p, [s], miss, wait, ym)
        paths.append(p)
        print(f"생성: {p.name}  (미입력 {len(miss)} / 대기 {len(wait)})")

    print(f"\n저장 위치: {out_dir}")
    return out_dir, paths, summaries


def main():
    generate()


if __name__ == "__main__":
    main()
