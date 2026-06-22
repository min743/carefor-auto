"""
차량관리 현황 이미지 생성 (Pillow).

레이아웃:
  ┌─────────────────────────────────────────┐
  │  차량관리 현황              26.06.22(월) │
  ├──────────┬────┬──────────┬──────────────┤
  │  지점명  │차량│오일교환  │ 정기검사     │  ← 하늘색 헤더
  │          │ 수 │초과│임박 │ 임박 │만료  │
  ├──────────┼────┼────┼────┼──────┼───────┤
  │ 둔산점   │  8 │  0 │  2 │   0  │  0   │
  └──────────┴────┴────┴────┴──────┴───────┘
  ┌─────────────────────────────────────────┐
  │ 🔴 오일교환 초과 차량                    │
  │  서구점  269호2768  -1,259km 초과        │
  └─────────────────────────────────────────┘
"""
from __future__ import annotations

import io
from datetime import date

from PIL import Image, ImageDraw, ImageFont

_FONT_REG  = "C:/Windows/Fonts/malgun.ttf"
_FONT_BOLD = "C:/Windows/Fonts/malgunbd.ttf"

_BG        = "#FFFFFF"
_HDR_BG    = "#5BA4CF"
_HDR_TEXT  = "#FFFFFF"
_ROW_EVEN  = "#FFFFFF"
_ROW_ODD   = "#EBF5FB"
_BORDER    = "#B8D9F0"
_TEXT_DARK = "#1A1A2E"
_DATE_TEXT = "#4A4A6A"
_ACCENT    = "#3A7EBD"
_WARN_BG   = "#FFF3CD"   # 경고 섹션 배경
_DANGER_BG = "#FFE0E0"   # 초과 섹션 배경
_WARN_HDR  = "#E67E22"   # 임박 헤더 색
_DANGER_HDR= "#C0392B"   # 초과 헤더 색
_SUM_BG    = "#D6EAF8"


def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_FONT_BOLD if bold else _FONT_REG, size)


def _tw(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[2] - bb[0]


def _th(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    bb = draw.textbbox((0, 0), text, font=font)
    return bb[3] - bb[1]


def _cell(draw, text, font, x, y, w, h, color, align="center"):
    tw = _tw(draw, text, font)
    th_ = _th(draw, text, font)
    ty = y + (h - th_) // 2
    tx = x + (w - tw) // 2 if align == "center" else x + 12
    draw.text((tx, ty), text, font=font, fill=color)


def _classify_vehicles(branches_data: dict, today: date) -> dict:
    """각 차량을 정상/오일임박/오일초과/검사임박/검사만료로 분류."""
    result = {}
    for branch, cars in branches_data.items():
        oil_overdue, oil_soon, insp_soon, insp_overdue = [], [], [], []
        for c in cars:
            # 오일 교환 판정
            total_km = c.get("totalKm") or 0
            next_km  = c.get("oilNextKm") or 0
            remain   = next_km - total_km
            if total_km > 0:  # 주행거리 입력된 차량만
                if remain < 0:
                    oil_overdue.append({**c, "remain": remain})
                elif remain <= 1000:
                    oil_soon.append({**c, "remain": remain})

            # 정기검사 판정
            inspect_end = c.get("inspectEnd")
            if inspect_end:
                try:
                    end_date = date.fromisoformat(inspect_end)
                    days_left = (end_date - today).days
                    if days_left < 0:
                        insp_overdue.append({**c, "days_left": days_left})
                    elif days_left <= 60:
                        insp_soon.append({**c, "days_left": days_left})
                except ValueError:
                    pass

        result[branch] = {
            "total": len(cars),
            "oil_overdue": oil_overdue,
            "oil_soon": oil_soon,
            "insp_soon": insp_soon,
            "insp_overdue": insp_overdue,
        }
    return result


def generate_vehicle_image(target_date: date, branches_data: dict) -> bytes:
    """차량관리 현황 PNG를 생성해 bytes로 반환."""
    today = target_date
    classified = _classify_vehicles(branches_data, today)

    W       = 860
    SIDE    = 24
    TABLE_W = W - 2 * SIDE

    f_title  = _font(True,  20)
    f_date   = _font(False, 13)
    f_hdr    = _font(True,  13)
    f_name   = _font(False, 14)
    f_num    = _font(True,  16)
    f_alert  = _font(False, 13)
    f_alertb = _font(True,  13)

    # ── 컬럼 정의 ─────────────────────────────────────
    # 지점명 | 차량수 | 오일초과 | 오일임박 | 검사임박 | 검사만료
    cols = [
        ("지점명",   180, "left"),
        ("차량수",    80, "center"),
        ("오일\n초과", 110, "center"),
        ("오일\n임박", 110, "center"),
        ("검사\n임박", 110, "center"),
        ("검사\n만료", 110, "center"),
        ("이상없음",  156, "center"),
    ]
    assert sum(c[1] for c in cols) == TABLE_W, f"{sum(c[1] for c in cols)} != {TABLE_W}"

    TITLE_H = 72
    HDR_H   = 56
    ROW_H   = 44
    SUM_H   = 48
    PAD     = 20

    # 경고 차량 모음
    alert_rows = []  # (branch, car, type, detail)
    for br, info in classified.items():
        for c in info["oil_overdue"]:
            alert_rows.append((br, c, "oil_over", f"오일 {abs(c['remain']):,}km 초과"))
        for c in info["oil_soon"]:
            alert_rows.append((br, c, "oil_soon", f"오일 잔여 {c['remain']:,}km"))
        for c in info["insp_overdue"]:
            alert_rows.append((br, c, "insp_over", f"검사 {abs(c['days_left'])}일 만료"))
        for c in info["insp_soon"]:
            alert_rows.append((br, c, "insp_soon", f"검사 {c['days_left']}일 남음"))

    ALERT_ROW_H = 36
    ALERT_HDR_H = 40
    alert_section_h = (ALERT_HDR_H + len(alert_rows) * ALERT_ROW_H + PAD) if alert_rows else 0

    n = len(classified)
    H = TITLE_H + HDR_H + ROW_H * n + SUM_H + PAD + alert_section_h + PAD

    img  = Image.new("RGB", (W, H), _BG)
    draw = ImageDraw.Draw(img)

    # ── 제목 ──────────────────────────────────────────
    title = "차량관리 현황"
    tw = _tw(draw, title, f_title)
    draw.text(((W - tw) // 2, 20), title, font=f_title, fill=_TEXT_DARK)

    weekday_kr = ["월", "화", "수", "목", "금", "토", "일"][today.weekday()]
    date_str = f"{today.strftime('%y.%m.%d')}({weekday_kr}요일)"
    dw = _tw(draw, date_str, f_date)
    draw.text((W - SIDE - dw, 26), date_str, font=f_date, fill=_DATE_TEXT)

    line_y = TITLE_H - 8
    draw.line([(SIDE, line_y), (W - SIDE, line_y)], fill=_ACCENT, width=2)

    # ── 헤더 행 ───────────────────────────────────────
    hy = TITLE_H
    draw.rectangle([SIDE, hy, W - SIDE, hy + HDR_H], fill=_HDR_BG)
    x = SIDE
    for label, cw, _ in cols:
        lines = label.split("\n")
        if len(lines) == 2:
            lh = _th(draw, lines[0], f_hdr)
            total = lh * 2 + 3
            ty0 = hy + (HDR_H - total) // 2
            for k, ln in enumerate(lines):
                lw = _tw(draw, ln, f_hdr)
                draw.text((x + (cw - lw) // 2, ty0 + k * (lh + 3)), ln, font=f_hdr, fill=_HDR_TEXT)
        else:
            _cell(draw, label, f_hdr, x, hy, cw, HDR_H, _HDR_TEXT)
        x += cw

    # ── 데이터 행 ─────────────────────────────────────
    branches = list(classified.keys())
    totals = {"total": 0, "oil_over": 0, "oil_soon": 0, "insp_soon": 0, "insp_over": 0, "ok": 0}

    for i, br in enumerate(branches):
        info = classified[br]
        ry = TITLE_H + HDR_H + i * ROW_H
        bg = _ROW_EVEN if i % 2 == 0 else _ROW_ODD
        draw.rectangle([SIDE, ry, W - SIDE, ry + ROW_H], fill=bg)

        n_over  = len(info["oil_overdue"]) + len(info["insp_overdue"])
        n_soon  = len(info["oil_soon"])    + len(info["insp_soon"])
        n_ok    = info["total"] - n_over - n_soon

        # 이상있는 셀 컬러링
        x = SIDE
        vals = [
            br,
            str(info["total"]),
            str(len(info["oil_overdue"])),
            str(len(info["oil_soon"])),
            str(len(info["insp_soon"])),
            str(len(info["insp_overdue"])),
            str(max(0, n_ok)),
        ]
        col_colors = [_TEXT_DARK, _TEXT_DARK, "#C0392B", "#E67E22", "#E67E22", "#C0392B", "#27AE60"]

        for j, (_, cw, align) in enumerate(cols):
            font = f_name if j == 0 else f_num
            _cell(draw, vals[j], font, x, ry, cw, ROW_H, col_colors[j], align)
            x += cw

        totals["total"]    += info["total"]
        totals["oil_over"] += len(info["oil_overdue"])
        totals["oil_soon"] += len(info["oil_soon"])
        totals["insp_soon"]+= len(info["insp_soon"])
        totals["insp_over"]+= len(info["insp_overdue"])
        totals["ok"]       += max(0, n_ok)

    # ── 합계 행 ───────────────────────────────────────
    sy = TITLE_H + HDR_H + n * ROW_H
    draw.rectangle([SIDE, sy, W - SIDE, sy + SUM_H], fill=_SUM_BG)
    sum_vals = [
        "합  계",
        str(totals["total"]),
        str(totals["oil_over"]),
        str(totals["oil_soon"]),
        str(totals["insp_soon"]),
        str(totals["insp_over"]),
        str(totals["ok"]),
    ]
    x = SIDE
    for j, (_, cw, align) in enumerate(cols):
        font = f_hdr if j == 0 else f_num
        _cell(draw, sum_vals[j], font, x, sy, cw, SUM_H, _TEXT_DARK, align)
        x += cw

    # ── 격자선 ────────────────────────────────────────
    table_top    = TITLE_H
    table_bottom = TITLE_H + HDR_H + n * ROW_H + SUM_H

    for i in range(n + 1):
        ly = TITLE_H + HDR_H + i * ROW_H
        draw.line([(SIDE, ly), (W - SIDE, ly)], fill=_BORDER, width=1)
    draw.line([(SIDE, table_bottom), (W - SIDE, table_bottom)], fill=_BORDER, width=1)
    draw.rectangle([SIDE, table_top, W - SIDE, table_bottom], outline=_BORDER, width=2)
    x = SIDE
    for _, cw, _ in cols[:-1]:
        x += cw
        draw.line([(x, table_top), (x, table_bottom)], fill=_BORDER, width=1)

    # ── 경고 섹션 ─────────────────────────────────────
    if alert_rows:
        ay = table_bottom + PAD
        # 헤더
        draw.rectangle([SIDE, ay, W - SIDE, ay + ALERT_HDR_H], fill=_DANGER_BG)
        hdr_text = "⚠  요주의 차량"
        draw.text((SIDE + 14, ay + (ALERT_HDR_H - _th(draw, hdr_text, f_alertb)) // 2),
                  hdr_text, font=f_alertb, fill=_DANGER_HDR)

        for k, (br, c, atype, detail) in enumerate(alert_rows):
            ry2 = ay + ALERT_HDR_H + k * ALERT_ROW_H
            bg  = _DANGER_BG if atype in ("oil_over", "insp_over") else _WARN_BG
            draw.rectangle([SIDE, ry2, W - SIDE, ry2 + ALERT_ROW_H], fill=bg)
            color = _DANGER_HDR if atype in ("oil_over", "insp_over") else _WARN_HDR
            line_text = f"  {br}  {c['carNumber']}  ({c.get('carModel', '')})  —  {detail}"
            draw.text((SIDE + 12, ry2 + (ALERT_ROW_H - _th(draw, line_text, f_alert)) // 2),
                      line_text, font=f_alert, fill=color)

        # 외곽선
        alert_bottom = ay + ALERT_HDR_H + len(alert_rows) * ALERT_ROW_H
        draw.rectangle([SIDE, ay, W - SIDE, alert_bottom], outline=_BORDER, width=1)

    buf = io.BytesIO()
    img.save(buf, format="PNG", dpi=(144, 144))
    return buf.getvalue()
