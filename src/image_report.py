"""
출석 현황 이미지 생성 (Pillow).

레이아웃:
  ┌──────────────────────────────────────┐
  │ 지점별 출석 현황       26.06.22(월)  │
  ├──────────┬──────────┬────┬────┬──────┤
  │ 지점명   │현원(수급)│결석│출석│총인원│  ← 하늘색 헤더
  ├──────────┼──────────┼────┼────┼──────┤
  │ 둔산점   │    69    │  0 │ 65 │  80  │
  │ 서구점   │    79    │  3 │ 71 │  80  │  ← 흰/연파 교차
  │ 천안점   │    64    │  2 │ 50 │  70  │
  │청주오창점│    50    │  3 │ 47 │  60  │
  └──────────┴──────────┴────┴────┴──────┘
"""
from __future__ import annotations

import io
from datetime import date

from PIL import Image, ImageDraw, ImageFont

_FONT_REG  = "C:/Windows/Fonts/malgun.ttf"
_FONT_BOLD = "C:/Windows/Fonts/malgunbd.ttf"

# 색상
_BG          = "#FFFFFF"
_HDR_BG      = "#5BA4CF"   # 하늘색
_HDR_TEXT    = "#FFFFFF"
_ROW_EVEN    = "#FFFFFF"
_ROW_ODD     = "#EBF5FB"   # 연한 하늘색
_BORDER      = "#B8D9F0"
_TEXT_DARK   = "#1A1A2E"
_TEXT_GRAY   = "#555577"
_TITLE_TEXT  = "#1A1A2E"
_DATE_TEXT   = "#4A4A6A"
_ACCENT      = "#3A7EBD"   # 제목 하단 선
_CAP_BG      = "#FFE066"   # 총인원 열 노란색 배경
_SUM_BG      = "#D6EAF8"   # 합계 행 배경 (연한 파랑)


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    path = _FONT_BOLD if bold else _FONT_REG
    return ImageFont.truetype(path, size)


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> tuple[int, int]:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0], bb[3] - bb[1]


def _draw_cell_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    x: int, y: int, w: int, h: int,
    color: str,
    align: str = "center",   # "center" | "left"
) -> None:
    tw, th = _text_size(draw, text, font)
    ty = y + (h - th) // 2
    if align == "center":
        tx = x + (w - tw) // 2
    else:
        tx = x + 14
    draw.text((tx, ty), text, font=font, fill=color)


def generate_image(target_date: date, branches_data: list[dict]) -> bytes:
    """출석 현황 표 PNG를 생성해 bytes로 반환."""

    W          = 820
    SIDE       = 24
    TABLE_W    = W - 2 * SIDE
    TITLE_H    = 88
    HDR_H      = 60
    ROW_H      = 54
    BOTTOM_PAD = 28

    n = len(branches_data)
    SUM_ROW_H  = 56
    H = TITLE_H + HDR_H + ROW_H * n + SUM_ROW_H + BOTTOM_PAD

    # 컬럼 정의: (헤더 텍스트, 너비, 정렬)
    # TABLE_W = 772 → 합이 맞아야 함
    cols = [
        ("지점명",        155, "center"),
        ("현원\n(수급중)", 148, "center"),
        ("결석",          107, "center"),
        ("출석",          107, "center"),
        ("정원",          135, "center"),
        ("월평균\n입소자", 120, "center"),
    ]
    assert sum(c[1] for c in cols) == TABLE_W, f"col sum={sum(c[1] for c in cols)} != {TABLE_W}"

    img  = Image.new("RGB", (W, H), _BG)
    draw = ImageDraw.Draw(img)

    f_title = _font(True,  30)
    f_date  = _font(True,  19)
    f_hdr   = _font(True,  16)
    f_name  = _font(True,  17)
    f_num   = _font(False, 17)

    # ── 제목 영역 ─────────────────────────────────────
    title_text = "지점별 출석 현황"
    tw, _ = _text_size(draw, title_text, f_title)
    draw.text(((W - tw) // 2, 22), title_text, font=f_title, fill=_TITLE_TEXT)

    weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][target_date.weekday()]
    date_str   = f"{target_date.strftime('%y.%m.%d')}({weekday_kr}요일)"
    dw, _      = _text_size(draw, date_str, f_date)
    draw.text((W - SIDE - dw, 30), date_str, font=f_date, fill=_DATE_TEXT)

    # 제목 하단 강조선
    line_y = TITLE_H - 10
    draw.line([(SIDE, line_y), (W - SIDE, line_y)], fill=_ACCENT, width=2)

    # ── 헤더 행 ───────────────────────────────────────
    hy = TITLE_H
    draw.rectangle([SIDE, hy, W - SIDE, hy + HDR_H], fill=_HDR_BG)

    x = SIDE
    for label, cw, align in cols:
        lines = label.split("\n")
        if len(lines) == 2:
            lh = _text_size(draw, lines[0], f_hdr)[1]
            total = lh * 2 + 3
            ty0   = hy + (HDR_H - total) // 2
            for k, ln in enumerate(lines):
                lw, _ = _text_size(draw, ln, f_hdr)
                draw.text((x + (cw - lw) // 2, ty0 + k * (lh + 3)), ln, font=f_hdr, fill=_HDR_TEXT)
        else:
            _draw_cell_text(draw, label, f_hdr, x, hy, cw, HDR_H, _HDR_TEXT, "center")
        x += cw

    # ── 데이터 행 ─────────────────────────────────────
    keys = ["name", "hyeon_won", "gyeol_seok", "chul_seok", "capacity", "avg_attendees"]

    cap_x   = SIDE + sum(c[1] for c in cols[:3])   # 출석 열 시작 x
    cap_end = cap_x + cols[3][1]                    # 출석 열 끝 x

    for i, b in enumerate(branches_data):
        ry     = TITLE_H + HDR_H + i * ROW_H
        row_bg = _ROW_EVEN if i % 2 == 0 else _ROW_ODD
        draw.rectangle([SIDE, ry, W - SIDE, ry + ROW_H], fill=row_bg)
        # 출석 열만 노란색 배경
        draw.rectangle([cap_x, ry, cap_end, ry + ROW_H], fill=_CAP_BG)

        x = SIDE
        for j, (_, cw, align) in enumerate(cols):
            raw = b.get(keys[j], "-")
            if j == 5 and raw != "-":  # 월평균입소자 소수점 포맷
                val = f"{float(raw):.2f}"
            else:
                val = str(raw)
            font = f_name if j == 0 else f_num
            col  = _TEXT_DARK
            _draw_cell_text(draw, val, font, x, ry, cw, ROW_H, col, align)
            x += cw

    # ── 합계 행 ───────────────────────────────────────
    sy = TITLE_H + HDR_H + n * ROW_H
    draw.rectangle([SIDE, sy, W - SIDE, sy + SUM_ROW_H], fill=_SUM_BG)
    draw.rectangle([cap_x, sy, cap_end, sy + SUM_ROW_H], fill=_CAP_BG)  # 출석 열 노란색

    totals = {
        "hyeon_won":  sum(b["hyeon_won"]  for b in branches_data),
        "gyeol_seok": sum(b["gyeol_seok"] for b in branches_data),
        "chul_seok":  sum(b["chul_seok"]  for b in branches_data),
        "avg_attendees": round(
            sum(b.get("avg_attendees", 0) for b in branches_data) / len(branches_data), 1
        ) if branches_data else 0.0,
    }
    sum_vals = [
        "합계",
        str(totals["hyeon_won"]),
        str(totals["gyeol_seok"]),
        str(totals["chul_seok"]),
        str(totals["hyeon_won"]),
        f"{totals['avg_attendees']:.2f}",
    ]

    x = SIDE
    for j, (_, cw, align) in enumerate(cols):
        font = f_hdr if j == 0 else f_num
        _draw_cell_text(draw, sum_vals[j], font, x, sy, cw, SUM_ROW_H, _TEXT_DARK, align)
        x += cw

    # ── 격자선 ────────────────────────────────────────
    table_top    = TITLE_H
    table_bottom = TITLE_H + HDR_H + n * ROW_H + SUM_ROW_H

    # 가로선 (데이터 행 구분)
    for i in range(n + 1):
        ly = TITLE_H + HDR_H + i * ROW_H
        draw.line([(SIDE, ly), (W - SIDE, ly)], fill=_BORDER, width=1)
    # 합계 행 하단선
    draw.line([(SIDE, sy + SUM_ROW_H), (W - SIDE, sy + SUM_ROW_H)], fill=_BORDER, width=1)

    # 테이블 외곽선
    draw.rectangle([SIDE, table_top, W - SIDE, table_bottom], outline=_BORDER, width=2)

    # 세로선 (열 구분)
    sep_chul = SIDE + sum(c[1] for c in cols[:4])   # 출석 | 정원
    sep_cap  = SIDE + sum(c[1] for c in cols[:5])   # 정원 | 월평균
    x = SIDE
    for _, cw, _ in cols[:-1]:
        x += cw
        if x == sep_chul:
            lw = 5   # 출석↔정원 넓은 구분
        elif x == sep_cap:
            lw = 3   # 정원↔월평균 기존 유지
        else:
            lw = 1
        draw.line([(x, table_top), (x, table_bottom)], fill=_BORDER, width=lw)

    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=(144, 144))
    return buf.getvalue()
