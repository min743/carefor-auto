# 항목33 식사(간식)제공결과 수집기 — 시설 단위(3-1-4 만족도/반영 + 6-1 식단표), 읽기 전용
# ②만족도 반기별 / ③결과반영 월1회 / ⑤1식4찬 식단표 게시 자동판정
import sys, json, io, os, datetime
sys.stdout.reconfigure(encoding="utf-8")
from playwright.sync_api import sync_playwright
from src.config import Config, config_path
from src.carefor_client import build_spa_hash, _navigate_spa, extract_g_pammgno
from audit.explore_pages import login
from audit.branch_pages import CLOSE_MODAL_JS

# --- 3-1-4 식사만족도조사/결과반영 추출 (innerText 파싱) ---
SAT_JS = r"""
(() => {
  const root = document.querySelector('#r_padding') || document.body;
  const txt = (root.innerText || '').replace(/\r/g, '');
  const year = ((txt.match(/(20\d{2})년\s*식사\(간식\)\s*만족도/) || [])[1]) || '';
  const seg = (a, b) => {
    const i = txt.indexOf(a); if (i < 0) return '';
    const j = b ? txt.indexOf(b, i + a.length) : -1;
    return txt.slice(i, j < 0 ? txt.length : j);
  };
  const dateRe = /20\d{2}\.\d{2}\.\d{2}/g;
  const fh = (seg('상반기', '하반기').match(dateRe) || []);
  const sh = (seg('하반기', '신규등록').match(dateRe) || []);
  // 결과반영 월별
  const refl = seg('결과반영', '출력');
  const months = {};
  const mre = /(0[1-9]|1[0-2])월([\s\S]{0,40}?)(?=(0[1-9]|1[0-2])월|$)/g;
  let m;
  while ((m = mre.exec(refl))) {
    const s = m[2];
    months[m[1]] = s.includes('미작성') ? '미작성'
                 : s.includes('없습니다') ? '대상없음'
                 : /작성|반영/.test(s) ? '작성' : s.replace(/\s+/g,'').slice(0,8);
  }
  return { year, firstHalf: fh, secondHalf: sh, months };
})()
"""

# --- 6-1 주간식단표 추출: 게시 여부 + 점심 반찬 수(1식4찬) — g-td[data-gt-row]/menu_div 기반 ---
MENU_JS = r"""
(() => {
  const root = document.querySelector('#r_padding') || document.body;
  const txt = (root.innerText || '').replace(/\r/g, '');
  const period = ((txt.match(/20\d{2}\.\d{2}\.\d{2}\s*~\s*20\d{2}\.\d{2}\.\d{2}/) || [])[0]) || '';
  function mealMax(label) {
    // 라벨 셀(data-gt-col=0)의 row 인덱스 찾기
    let rowIdx = null;
    document.querySelectorAll('g-td[data-gt-col="0"]').forEach(c => {
      if ((c.innerText || '').replace(/\s/g, '').includes(label)) rowIdx = c.getAttribute('data-gt-row');
    });
    if (rowIdx === null) return 0;
    let best = 0;
    document.querySelectorAll('g-td[data-gt-row="' + rowIdx + '"]').forEach(c => {
      if (c.getAttribute('data-gt-col') === '0') return;
      const div = c.querySelector('.menu_div') || c;
      const lines = (div.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
      if (lines.length > best) best = lines.length;
    });
    return best;
  }
  return { period, hasMenu: /점심\s*식단/.test(txt), lunchDishes: mealMax('점심식단'), dinnerDishes: mealMax('저녁식단') };
})()
"""


def judge_item33(data, today):
    """②만족도 반기별 / ③결과반영 월1회 / ⑤1식4찬 식단표 자동판정. ①기피식품·④면담은 수기."""
    sat = data.get("satisfaction", {}); menu = data.get("menu", {})
    y, mth = today.year, today.month
    subs, notes = {}, []

    # ② 만족도 반기별 1회 (3-1-4 조사 기준). 없어도 미흡 단정 불가 — 면담·상담으로도 파악 가능 → '주의'(확인요망)
    fh = len(sat.get("firstHalf", [])); sh = len(sat.get("secondHalf", []))
    p2 = []
    if mth > 6 and fh == 0: p2.append(f"{y} 상반기")
    if mth == 12 and sh == 0: p2.append(f"{y} 하반기")
    subs["②"] = "주의" if p2 else "양호"
    notes.append(f"만족도조사 상반기 {fh}건·하반기 {sh}건" + (f" → {'·'.join(p2)} 상담/면담 확인요망" if p2 else ""))

    # ③ 결과반영 월1회. 3-1-4 결과반영 칸이 비어도 실무상 상담일지(1-4)+요양기록지(3-1)에 매달 작성하므로
    #    자동 미흡 아님 → '주의'(상담일지+요양기록지 확인요망). (사용자 확정 2026-07: 통상 1~5월 상담일지+요양기록지 작성)
    months = sat.get("months", {})
    p3 = [f"{m:02d}월" for m in range(1, mth) if months.get(f"{m:02d}") == "미작성"]
    subs["③"] = "주의" if p3 else "양호"
    if p3: notes.append("결과반영 3-1-4 미기재 " + "·".join(p3) + " → 상담일지+요양기록지 확인요망")

    # ⑤ 1식4찬 식단표 게시 (점심 밥+국+4찬 = 5개 이상), 6-1에서 확인.
    #    점심 0찬 = 이번주 미입력 표본일 가능성 → 미흡 아니라 '주의'(확인요망). 입력됐는데 4찬 미만만 미흡.
    lunch = menu.get("lunchDishes", 0)
    if lunch >= 5:
        subs["⑤"] = "양호"; notes.append(f"식단표 게시 O(점심 {lunch}찬)")
    elif lunch == 0:
        subs["⑤"] = "주의"; notes.append("식단표 이번주 미입력(표본) — 게시 확인요망")
    else:
        subs["⑤"] = "미흡"; notes.append(f"식단표 점심 {lunch}찬(1식4찬 미달)")

    bad = [k for k, v in subs.items() if v == "미흡"]
    warn = [k for k, v in subs.items() if v == "주의"]
    status = "미흡" if bad else ("주의" if warn else "양호")
    detail = ("[자동: ②만족도조사·⑤식단표1식4찬 / ③결과반영은 3-1-4 미기재 시 상담일지+요양기록지 확인요망 / ①기피식품·④면담 수기] "
              + " · ".join(notes))
    return {"status": status, "sub_status": subs, "detail": detail}

def go(page, typ, view, title, g, marker=None):
    """페이지 이동 후 마커 텍스트가 실제로 뜰 때까지 폴링 — 고정대기보다 조기수집에 안전(클라우드 대비)."""
    h = build_spa_hash(typ, view, title, g)
    _navigate_spa(page, f"https://dn.carefor.co.kr/#{h}")
    page.wait_for_timeout(1500)
    try: page.evaluate(CLOSE_MODAL_JS)
    except Exception: pass
    if marker:
        try:
            page.wait_for_function(
                "m => ((document.querySelector('#r_padding')||document.body).innerText||'').includes(m)",
                arg=marker, timeout=9000)
        except Exception:
            page.wait_for_timeout(3000)  # 마커 안 뜨면 여유 대기 후 진행
    page.wait_for_timeout(1200)

def collect_branch(page, g):
    go(page, "left_sub3", "/share/care/view.meal_satisfaction_daynurse", "3-1-4.식사(간식) 만족도 조사 및 반영", g, marker="만족도")
    sat = page.evaluate(SAT_JS)
    go(page, "left_sub6", "/share/safe/view.weekly_menu", "6-1.주간식단표", g, marker="식단")
    menu = page.evaluate(MENU_JS)
    return {"satisfaction": sat, "menu": menu}

def merge_dashboard(judged):
    """dashboard_data.js 의 AUDIT_DATA 각 지점 item_results 에 33번 주입 (나머지 부분 보존)."""
    path = "audit_results/dashboard_data.js"
    raw = io.open(path, encoding="utf-8").read()
    prefix = "window.AUDIT_DATA = "
    i = raw.index(prefix) + len(prefix)
    obj, end = json.JSONDecoder().raw_decode(raw, i)
    hit = 0
    for br, res in judged.items():
        if br in obj:
            obj[br].setdefault("item_results", {})["33"] = res
            hit += 1
    newraw = raw[:i] + json.dumps(obj, ensure_ascii=False) + raw[end:]
    io.open(path, "w", encoding="utf-8").write(newraw)
    print(f"[대시보드 병합] {hit}개 지점에 33번 주입 → {path}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_merge = "--merge" in sys.argv
    keys = args or ["청주"]
    cfg = Config.load(config_path())
    today = datetime.datetime.now()
    out, judged = {}, {}
    with sync_playwright() as pw:
        for key in keys:
            b = next(x for x in cfg.branches if key in x.name)
            print(f"\n===== {b.name} =====")
            browser, page = login(pw, b.ctmnumb)
            try:
                g = extract_g_pammgno(page)
                data = collect_branch(page, g)
                out[b.name] = data
                res = judge_item33(data, today)
                judged[b.name] = res
                print("  수집:", json.dumps(data, ensure_ascii=False))
                print("  판정:", res["status"], res["sub_status"])
                print("       ", res["detail"])
            finally:
                browser.close()
    os.makedirs("audit_results", exist_ok=True)
    io.open("audit_results/item33_raw.json", "w", encoding="utf-8").write(
        json.dumps({"raw": out, "judged": judged}, ensure_ascii=False, indent=2))
    print("\n[저장] audit_results/item33_raw.json")
    if do_merge:
        merge_dashboard(judged)

if __name__ == "__main__":
    main()


def judge_avoid_food(results, cut: str = "2026.01.01"):
    """항목 33①: 기간 내 신규 수급자의 욕구사정에 기피식품 기재 여부.

    - 욕구사정 영양상태 판단근거에 '기피식품'이 있으면 기재로 인정('없음' 포함, 매뉴얼 기준)
    - 기간 내 신규 수급자 없으면 예외(양호)
    - avoidFood 필드가 아예 없으면(구버전 스캔) 판정 보류 → (None, None)
    반환: (status, note) 또는 (None, None)
    """
    has_field = any("avoidFood" in n for p in results for n in (p.get("needs") or []))
    if not has_field:
        return None, None

    new = []
    for p in results:
        starts = [e["d"] for e in (p.get("enroll") or [])
                  if e.get("k") == "급여개시일" and e.get("d")]
        if starts and min(starts) >= cut:
            new.append(p)
    if not new:
        return "양호", "①기피식품: 기간 내 신규 수급자 없음(예외)"

    miss = [p["name"] for p in new if not any(n.get("avoidFood") for n in (p.get("needs") or []))]
    if miss:
        return "미흡", (f"①기피식품 미기재 {len(miss)}명"
                      f"({', '.join(miss[:5])}{'…' if len(miss) > 5 else ''})")
    return "양호", f"①기피식품 기재 확인(신규 {len(new)}명 전원)"
