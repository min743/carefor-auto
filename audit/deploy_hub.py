# -*- coding: utf-8 -*-
"""충청본부 공유 허브 → Apps Script 웹앱 배포 (접속자 신원·항목별 현황).

왜 옮기나: GitHub Pages 는 **접속 로그를 안 주고**, PIN 은 신원을 안 남긴다.
게다가 PIN 이 클라이언트 소스에 박혀 있어 공개 저장소에서 그대로 읽힌다(실측 확인).
Apps Script 웹앱을 **caring.co.kr 도메인 한정**으로 띄우면 구글이 접속자를 알려준다.

★ 허브 본문 원본은 `apps_script/hub_source.html` (Pages 밖 — docs/ 에 두면 공개 저장소에서 그대로 읽힌다).
  `docs/hq.html` 은 기존 링크·북마크를 살리려고 남긴 **이동 페이지**일 뿐이다.
  변환: PIN 게이트 제거 · 상대링크를 GitHub Pages 절대주소로 · 상단 접속현황 바 주입

실행: py -X utf8 -m audit.deploy_hub [--create]
  --create : 스크립트 프로젝트를 새로 만든다(최초 1회). 이후엔 코드 갱신·재배포만.

⚠️ 최초 1회 **소유자가 편집기에서 ▶실행 → 승인**해야 동작한다(Authorization needed). 대리 불가.
⚠️ 로그 탭은 `_허브접속` — `_` 로 시작해야 차량관리 앱의 getBranches() 가 지점으로 오인하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
SHEET_ID = "1ErsNQ7elSORuB6Z20cKUOWjdroxp-4-01N0WoW6PAOI"   # 충청본부 차량관리 (시트를 더 늘리지 않는다)
PAGES = "https://caring-chungcheong.github.io/carefor-auto/"
# ★ ID 를 코드에 박아둔다 — .gitignore 가 *.json 을 전부 막아 파일로 두면 버전관리 밖으로 샌다.
#   이걸 잃으면 재배포가 **새 스크립트**를 만들어 주소가 바뀌고(관제탑·북마크 전부 깨짐)
#   승인도 다시 받아야 한다. 비밀값 아님 — 허브 주소는 어차피 직원에게 공유한다.
SCRIPT_ID = "1nz04XdaSB1Pg3iCy7EdgiABUhQaJme-QVi2qZ8ONjygXTVhPRBcOHP0-"
DEPLOY_ID = "AKfycby4fQaPyn3AthrSy3NAnbnTRqyXxt-HiB3AHv2uWutQEUWA-xQnMDcOD0f_3XGhTD3Z"
HUB_URL = "https://script.google.com/a/macros/caring.co.kr/s/AKfycby4fQaPyn3AthrSy3NAnbnTRqyXxt-HiB3AHv2uWutQEUWA-xQnMDcOD0f_3XGhTD3Z/exec"

CODE = r"""
const SHEET_ID = '%s';
const LOG_SHEET = '_허브접속';
const NAME_SHEET = '_이름표';   // 이메일→이름 (구글은 이름을 안 준다)
const HEADERS = ['시각', '이메일', '이름', '항목'];

/** 최초 1회 편집기에서 이 함수를 골라 ▶실행 → 승인.
 *  ⚠️ doGet 을 실행해 승인하면 **시트 권한이 빠진 채로 승인될 수 있다**(실측: userinfo.email 만
 *     승인되고 spreadsheets 가 빠져 로그가 조용히 실패했다 — log_ 가 에러를 삼켜 안 보였다).
 *     이 함수는 시트를 직접 건드리므로 구글이 spreadsheets 권한을 반드시 묻는다. */
function setup() {
  sheet_().appendRow([new Date(), who_(), nameOf_(who_()), '설치 확인']);
  return '_허브접속 탭 준비 완료';
}

function doGet(e) {
  var page = (e && e.parameter && e.parameter.page) || '';
  var map = { revenue: '매출 점검', carcost: '차량 월별 수리비', runbook: '케어포 운영 런북' };  // 도메인(caring.co.kr) 로그인해야 열림
  if (map[page]) { log_(map[page]); return out_(page, map[page]); }
  log_('허브 열기');
  return out_('hub', '충청본부 공유 허브');
}
function out_(file, title) {
  return HtmlService.createHtmlOutputFromFile(file)
    .setTitle(title)
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

/** 접속자 이메일. 같은 도메인(caring.co.kr)이면 executeAs=USER_DEPLOYING 이어도 잡힌다. */
function who_() {
  var e = '';
  try { e = Session.getActiveUser().getEmail() || ''; } catch (err) { e = ''; }
  return e;
}

/** 이메일 → 표시용 이름.
 *  구글은 이메일까지만 준다(이름은 관리자 권한이 필요한 디렉터리 API 에나 있다).
 *  → `_이름표` 탭에서 찾고, 없으면 아이디를 쓴다. 처음 보는 이메일은 자동으로 이름표에 추가되므로
 *    사람이 이름 칸만 채우면 그때부터 이름으로 뜬다. */
function nameOf_(email) {
  if (!email) return '(확인 안 됨)';
  var m = nameMap_();
  return m[email] || email.split('@')[0];
}

function nameSheet_() {
  var ss = SpreadsheetApp.openById(SHEET_ID);
  var sh = ss.getSheetByName(NAME_SHEET);
  if (!sh) {
    sh = ss.insertSheet(NAME_SHEET);
    sh.getRange(1, 1, 1, 2).setValues([['이메일', '이름']])
      .setFontWeight('bold').setBackground('#e8eaf6');
    sh.setFrozenRows(1);
    sh.setColumnWidth(1, 240);
    sh.getRange('C1').setValue('← 이름 칸을 채우면 허브에 그 이름으로 뜹니다 (접속한 이메일은 자동 추가)')
      .setFontColor('#888');
  }
  return sh;
}

/** 이메일→이름 (한 번 읽어 캐시 — 요청마다 시트를 여러 번 읽지 않게) */
var _nm = null;
function nameMap_() {
  if (_nm) return _nm;
  _nm = {};
  try {
    var sh = nameSheet_();
    if (sh.getLastRow() > 1) {
      sh.getRange(2, 1, sh.getLastRow() - 1, 2).getValues().forEach(function (r) {
        var e = String(r[0] || '').trim(), n = String(r[1] || '').trim();
        if (e && n) _nm[e] = n;
      });
    }
  } catch (err) {}
  return _nm;
}

/** 처음 보는 이메일이면 이름표에 빈 줄로 추가 — 채울 대상이 저절로 모인다 */
function seedName_(email) {
  if (!email) return;
  try {
    var sh = nameSheet_();
    var have = sh.getLastRow() > 1
      ? sh.getRange(2, 1, sh.getLastRow() - 1, 1).getValues().map(function (r) { return String(r[0]).trim(); })
      : [];
    if (have.indexOf(email) === -1) sh.appendRow([email, '']);
  } catch (err) {}
}

function sheet_() {
  var ss = SpreadsheetApp.openById(SHEET_ID);
  var sh = ss.getSheetByName(LOG_SHEET);
  if (!sh) {
    sh = ss.insertSheet(LOG_SHEET);
    sh.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS])
      .setFontWeight('bold').setBackground('#e8eaf6');
    sh.setFrozenRows(1);
  }
  return sh;
}

function log_(item) {
  try {
    var email = who_();
    seedName_(email);   // 처음 보는 사람이면 이름표에 빈 줄로 올려둔다
    sheet_().appendRow([new Date(), email, nameOf_(email), item]);
  } catch (err) { /* 로깅 실패가 허브를 막으면 안 된다 */ }
}

/** 카드 클릭 — 화면에서 google.script.run 으로 부른다 */
function logItem(item) { log_(String(item || '').slice(0, 60)); return true; }

/** 상단 바에 뿌릴 현황: 나 · 최근 접속자 · 항목별 조회수 */
function status() {
  var email = who_();
  var out = { me: nameOf_(email), recent: [], items: [], todo: 0 };
  try {   // 이름 안 채워진 사람이 몇인지 — 안 알려주면 이름표가 영영 안 채워진다
    var ns = nameSheet_();
    if (ns.getLastRow() > 1) {
      out.todo = ns.getRange(2, 1, ns.getLastRow() - 1, 2).getValues()
        .filter(function (r) { return String(r[0]).trim() && !String(r[1]).trim(); }).length;
    }
  } catch (err) {}
  try {
    var sh = sheet_();
    var last = sh.getLastRow();
    if (last < 2) return out;
    var n = Math.min(last - 1, 800);
    var rows = sh.getRange(last - n + 1, 1, n, HEADERS.length).getValues();
    var seen = {}, cnt = {};
    for (var i = rows.length - 1; i >= 0; i--) {
      var nm = String(rows[i][2] || ''), it = String(rows[i][3] || ''), at = rows[i][0];
      if (nm && !seen[nm]) {
        seen[nm] = true;
        out.recent.push({ name: nm, at: Utilities.formatDate(new Date(at), 'Asia/Seoul', 'MM/dd HH:mm') });
      }
      if (it && it !== '허브 열기') cnt[it] = (cnt[it] || 0) + 1;
    }
    out.recent = out.recent.slice(0, 8);
    out.items = Object.keys(cnt).map(function (k) { return { item: k, n: cnt[k] }; })
      .sort(function (a, b) { return b.n - a.n; }).slice(0, 8);
  } catch (err) {
    // ⚠️ 삼키지 말 것 — 권한이 빠져 로그가 통째로 안 남는데도 "최근 접속 없음"으로만 보여
    //    정상인 줄 알았다. 실패는 화면에 드러낸다.
    out.err = String(err).slice(0, 120);
  }
  return out;
}
""" % SHEET_ID

# 상단 접속현황 바 — 원본 header 바로 뒤에 끼워 넣는다
TOPBAR = """
<div id="hubwho" style="max-width:1080px;margin:14px auto 0;padding:0 18px">
  <div style="background:#fff;border:1px solid #e3e8f0;border-radius:14px;padding:12px 15px;
              font-size:13px;color:#3b4252;box-shadow:0 2px 10px rgba(21,38,71,.05)">
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <b style="color:#152647">👤 <span id="whoMe">확인 중…</span></b>
      <span style="color:#9aa1ab">·</span>
      <span id="whoRecent" style="color:#6b7280">최근 접속 불러오는 중…</span>
    </div>
    <div id="whoItems" style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap"></div>
  </div>
</div>
<script>
google.script.run.withSuccessHandler(function(s){
  document.getElementById('whoMe').textContent = s.me;
  var rc = document.getElementById('whoRecent');
  if (s.err) { rc.textContent = '⚠️ 접속기록 저장 안 됨 — ' + s.err; rc.style.color = '#b42318'; }
  else rc.textContent = s.recent.length
    ? '최근 접속 — ' + s.recent.map(function(r){return r.name+'('+r.at+')';}).join(', ')
    : '최근 접속 기록 없음';
  if (s.todo) {
    var w = document.createElement('span');
    w.style.cssText = 'color:#a85a00;font-weight:700';
    w.textContent = ' · 이름 미등록 ' + s.todo + '명 (시트 _이름표 에 이름만 적으면 됩니다)';
    rc.parentNode.appendChild(w);
  }
  document.getElementById('whoItems').innerHTML = s.items.map(function(i){
    return '<span style="background:#eef2f9;border-radius:999px;padding:3px 10px;font-size:11.5px;'
         + 'font-weight:700;color:#152647">'+i.item+' '+i.n+'</span>';}).join('');
}).status();
// 카드 클릭을 항목별 현황으로 남긴다
document.addEventListener('click', function(e){
  var a = e.target.closest('a[href]'); if(!a) return;
  var t = a.querySelector('.ttl');
  var nm = (t ? t.textContent : a.textContent).trim().slice(0,60);
  if(nm) google.script.run.logItem(nm);
}, true);
</script>
"""


def _git_md(path: str) -> str:
    """저장소 파일의 마지막 커밋 시각 → 'MM/DD' (항목별 갱신시각 초기값). 실패 시 빈 문자열."""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "log", "-1", "--format=%cI", "--", path],
                             capture_output=True, text=True, encoding="utf-8").stdout.strip()
        if len(out) >= 10:                       # 2026-07-21T10:16:32+09:00
            return out[5:7] + "/" + out[8:10]
    except Exception:
        pass
    return ""


def _embed_date(kind: str, src: str | None = None) -> str:
    """매출·차량수리비(Apps Script 서빙, 커밋 안 됨)의 데이터 기준일 → 'MM/DD 기준' 캡션.
    차량수리비는 파일에 박힌 '데이터 기준일'을, 매출은 합본 파일 수정시각을 쓴다.

    ★ src: 원본 HTML 문자열을 직접 넘기면 로컬 파일 대신 그걸 읽는다.
      CI 러너에는 저장소 밖 원본(`클로드코드/`)이 없어 파일을 읽으면 죽는다
      (실제로 2026-07-22~23 허브 자동배포가 이걸로 3회 연속 실패했다).
      CI 는 보존해 온 carcost 소스를 넘겨 캡션을 유지한다. 없으면 캡션만 비운다."""
    if kind == "carcost":
        t = src
        if t is None:
            p = CC / "차량_월별수리비내역.html"
            if not p.exists():
                return ""
            t = p.read_text(encoding="utf-8")
        m = re.search(r"데이터 기준일\s*(\d{4})-(\d{2})-(\d{2})", t)
        if m:
            return "🔄 " + m.group(2) + "/" + m.group(3) + " 기준"
        return ""
    if kind == "revenue":
        cands = sorted((CC / "매출점검").glob("매출점검_합본_*.html"))
        if cands:
            import datetime
            mt = datetime.datetime.fromtimestamp(cands[-1].stat().st_mtime)
            return "🔄 " + mt.strftime("%m/%d") + " 기준"
    return ""


def build_html(carcost_src: str | None = None) -> str:
    """허브 원본 → Apps Script 용으로 변환. 원본은 건드리지 않는다."""
    # 원본은 **Pages 밖**(apps_script/)에 둔다 — docs/ 에 두면 허브 내용이 공개 저장소에서 그대로 읽힌다.
    s = (ROOT / "apps_script" / "hub_source.html").read_text(encoding="utf-8")
    # 0) 항목별 갱신시각 — 커밋된 페이지는 커밋시각을 초기값으로 심고(로드 시 라이브 갱신),
    #    매출·차량수리비는 데이터 기준일을 직접 박는다.
    s = re.sub(r'<span class="upd" data-gh="([^"]+)"></span>',
               lambda m: '<span class="upd" data-gh="%s">%s</span>' % (
                   m.group(1), ("🔄 " + _git_md(m.group(1)) + " 갱신") if _git_md(m.group(1)) else ""),
               s)
    s = s.replace("{{UPD_REVENUE}}", _embed_date("revenue"))
    s = s.replace("{{UPD_CARCOST}}", _embed_date("carcost", carcost_src))
    # 1) PIN 게이트 제거 — 도메인 인증이 대신한다(PIN 은 소스에 노출돼 있어 보호 효과도 없었다)
    s = re.sub(r'<div id="gate">.*?</div>\s*(?=<header>)', "", s, flags=re.S)
    s = re.sub(r"const PIN='[^']*';", "", s)
    s = re.sub(r"document\.getElementById\('pin'\)\.addEventListener\(.*?\}\);", "", s, flags=re.S)
    s = re.sub(r"if\(sessionStorage\.getItem\('ap'\)==='1'\).*", "", s)
    # 2) 상대 링크 → GitHub Pages 절대주소 (허브만 Apps Script 로 옮기고 나머지 페이지는 그대로 둔다)
    s = re.sub(r'href="(?!https?:|#|mailto:)([^"]+)"', lambda m: f'href="{PAGES}{m.group(1)}"', s)
    # 2.5) #SELF → 이 허브 웹앱 주소(매출·차량수리비는 같은 Apps Script 로 도메인 제한 서빙)
    s = s.replace("#SELF", HUB_URL)
    # 3) 상단 접속현황 바 주입
    s = s.replace("</header>", "</header>" + TOPBAR, 1)
    return s


# 허브에 도메인 제한으로 얹을 페이지들 (Pages 밖, 개인정보 있어 공개 저장소·공개 Pages 금지)
CC = ROOT.parent   # 클로드코드/
PAGE_SRC = {
    "carcost": CC / "차량_월별수리비내역.html",
    # 매출은 월별 합본 최신본을 자동 선택
    "revenue": None,
    # 운영 런북 — 공개 저장소에서 뺀 SKILL.md 내용(케어포 로그인·시트/채널 ID·사고 이력)을
    # 본부에만 도메인 제한으로 서빙한다. 원본은 Pages 밖 클로드코드/ 폴더.
    "runbook": CC / "케어포_운영런북.html",
}


def _mask_name(nm: str) -> str:
    """수급자 이름 가운데 마스킹 — 김여수→김○수, 이재분→이○분, 남궁민수→남○○수. (앞뒤만 남김)"""
    nm = nm.strip()
    if len(nm) <= 1:
        return nm
    if len(nm) == 2:
        return nm[0] + "○"
    return nm[0] + "○" * (len(nm) - 2) + nm[-1]


def _inject_topbar(s: str) -> str:
    """'← 공유 허브' 복귀 링크를 페이지 **맨 위 sticky 바**로 주입한다.
    이 페이지들은 상단에 전체폭 sticky 탭바(매출 .tabbar)·툴바(차량 .toolbar top:0)가 있어,
    그냥 위에 얹으면 그것들이 덮어 잘렸다 → 복귀 바를 top:0 sticky(z 최상단)로 두고,
    페이지의 sticky 요소는 그 높이만큼 아래로 내려(top:BARH) 겹치지 않게 한다.
    target=_top: Apps Script iframe 밖(최상위 창)으로 이동해야 허브가 정상 로드됨."""
    # ★페이지에 헤더 슬롯(#hubslot)이 있으면 그 안에 복귀 버튼을 넣는다(매출 페이지).
    #   전에는 복귀 바를 body 맨 위에 얹었는데, 매출 페이지의 sticky 탭바와 top:0 에서 겹쳐
    #   노트북 화면에서 서로 덮었다(반복된 문제). 헤더 안에 넣으면 한 덩어리라 충돌이 없다.
    if 'id="hubslot"' in s:
        pill = ('<a href="' + HUB_URL + '" target="_top" style="display:inline-flex;'
                'align-items:center;gap:6px;background:#eaf0f8;color:#152647 !important;'
                '-webkit-text-fill-color:#152647;border:1px solid #c4d0e6;padding:7px 15px;'
                'border-radius:999px;text-decoration:none;'
                'font-family:\'Malgun Gothic\',system-ui,sans-serif;font-size:13px;font-weight:700;'
                'white-space:nowrap">← 🏢 본부 공유 허브</a>')
        return s.replace('<span id="hubslot"></span>', '<span id="hubslot">' + pill + '</span>', 1)

    # (그 외 페이지 — 차량 월별 수리비 등) 복귀 바를 맨 위에 얹는다.
    # ⚠️ 오프셋을 상수로 박지 말 것 — 글꼴·확대율에 따라 바 높이가 달라져 탭이 그만큼 잘린다(실제로 그랬다).
    #    바 높이를 실측해 sticky top 에 그대로 넣고, 창 크기·폰트 변화에도 다시 맞춘다.
    bar = ('<div id="hubbackbar" style="background:#eef3fa;'
           'border-bottom:1px solid #d4deec;padding:8px 14px;line-height:1;'
           'box-shadow:0 2px 8px rgba(21,38,71,.06)">'
           '<a href="' + HUB_URL + '" target="_top" style="display:inline-block;'
           'background:#eaf0f8;color:#152647 !important;-webkit-text-fill-color:#152647;'
           'border:1px solid #c4d0e6;padding:8px 16px;border-radius:9px;text-decoration:none;'
           'font-family:\'Malgun Gothic\',system-ui,sans-serif;font-size:13.5px;font-weight:700;line-height:1.2">'
           '← 공유 허브</a></div>'
           '<script>(function(){'
           # ★ 페이지의 sticky(.tabbar/.toolbar)는 **건드리지 않는다**.
           #   전에는 복귀 바를 sticky 로 띄우고 탭바 top 을 밀었는데, 그때마다
           #   (1) 최상단에서 바가 탭을 덮거나 (2) 탭바가 본문 제목을 덮는 문제가 번갈아 났다.
           #   원인은 '보이는 위치'와 '차지하는 자리'가 어긋나서다. 그래서 바는 일반 흐름에 두고
           #   페이지 레이아웃은 원래 설계 그대로 둔다 — 간섭이 0이라 어떤 확대율에서도 안 깨진다.
           #   (스크롤을 내리면 바는 위로 사라지고, 탭바가 원래대로 top:0 에 붙는다.)
           'var bar=document.getElementById("hubbackbar");'
           'function sync(){var cs=getComputedStyle(document.body),'
           'ml=parseFloat(cs.marginLeft)||0,mr=parseFloat(cs.marginRight)||0,'
           'mt=parseFloat(cs.marginTop)||0;'
           'bar.style.marginTop=(-mt)+"px";'          # 바만 위로 붙임 — 아래 내용은 그대로
           'bar.style.marginBottom=mt+"px";'          # 줄어든 자리를 아래 여백으로 되돌려 준다
           'bar.style.marginLeft=(-ml)+"px";bar.style.marginRight=(-mr)+"px";}'
           'sync();addEventListener("resize",sync);'
           'if(document.fonts&&document.fonts.ready)document.fonts.ready.then(sync);'
           '})();</script>')
    return re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + bar, s, count=1)


def _inject_bg(s: str) -> str:
    """페이지 배경이 거의 흰색(매출 #f6f8fb·차량 #fff)이라 밋밋 → 소프트 블루 틴트로 교체.
    흰 카드·표가 배경 위로 도드라져 눈에 잘 들어온다. !important 로 원본 body 배경만 덮는다."""
    css = ('<style>body{background:radial-gradient(1100px 460px at 50% -150px,'
           '#cfddf3 0%,rgba(207,221,243,0) 68%),#dde7f4 !important}</style>')
    return re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + css, s, count=1)


def page_html(kind: str) -> str:
    """도메인 제한 서빙용 페이지 HTML — 원본 + 이름 마스킹(매출) + 배경 틴트 + 하단 복귀 링크."""
    p = PAGE_SRC[kind]
    if kind == "revenue":
        cands = sorted((CC / "매출점검").glob("매출점검_합본_*.html"))
        if not cands:
            raise SystemExit("매출점검 합본 HTML을 찾지 못함 (클로드코드/매출점검/)")
        p = cands[-1]
    s = pathlib.Path(p).read_text(encoding="utf-8")
    if kind == "revenue":
        s = _mask_revenue_names(s)
    s = _inject_bg(s)
    s = _inject_topbar(s)
    return s


# 첫 칸에 오지만 이름이 아닌 값(마스킹 제외)
_NOT_NAME = {"합계", "소계", "총계", "평균", "전체", "미배정", "기타", "계", "구분", "지점"}


def _mask_revenue_names(s: str) -> str:
    """모든 표의 '행 첫 번째 td'가 수급자 이름 — 가운데 마스킹. 사유·금액·합계는 안 건드림.
    (매출점검 표는 전부 수급자가 첫 컬럼. 실측: 첫칸 한글값 전부 이름, 합계/지점은 첫칸에 없음)"""
    def repl(m):
        cls, name = m.group(1) or "", m.group(2)
        if name in _NOT_NAME or name.endswith("점") or name.endswith("급"):
            return m.group(0)
        return "<tr><td" + cls + ">" + _mask_name(name) + "</td>"
    return re.sub(r"<tr>\s*<td(\s+class='[^']*')?>([가-힣]{2,4})</td>", repl, s)


def token() -> str:
    d = json.loads((pathlib.Path.home() / ".clasprc.json").read_text())
    t = d.get("tokens", {}).get("default") or d.get("token") or {}
    body = urllib.parse.urlencode({
        "client_id": d.get("oauth2ClientSettings", {}).get("clientId") or t.get("client_id"),
        "client_secret": d.get("oauth2ClientSettings", {}).get("clientSecret") or t.get("client_secret"),
        "refresh_token": t.get("refresh_token"), "grant_type": "refresh_token"}).encode()
    return json.load(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=body)))["access_token"]


def api(at, url, data=None, method=None):
    r = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                               headers={"Authorization": "Bearer " + at, "Content-Type": "application/json"},
                               method=method)
    try:
        return json.load(urllib.request.urlopen(r))
    except urllib.error.HTTPError as e:
        return {"ERR": e.read().decode()[:300]}


MANIFEST = {
    "timeZone": "Asia/Seoul", "exceptionLogging": "STACKDRIVER", "runtimeVersion": "V8",
    "oauthScopes": ["https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/userinfo.email"],   # 접속자 이메일에 필요
    # 🔒 caring.co.kr 계정만 — 이게 PIN 을 대체하고, 동시에 신원을 만들어준다
    "webapp": {"executeAs": "USER_DEPLOYING", "access": "DOMAIN"},
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", action="store_true", help="스크립트 프로젝트 새로 만들기(최초 1회)")
    args = ap.parse_args()
    at = token()
    st = {"scriptId": SCRIPT_ID, "deploymentId": DEPLOY_ID}

    if args.create or not st.get("scriptId"):
        p = api(at, "https://script.googleapis.com/v1/projects",
                {"title": "충청본부 공유허브", "parentId": SHEET_ID})
        if p.get("ERR"):
            print("생성 실패:", p["ERR"]); sys.exit(1)
        st["scriptId"] = p["scriptId"]
        print("스크립트 생성:", st["scriptId"])

    r = api(at, f"https://script.googleapis.com/v1/projects/{st['scriptId']}/content",
            {"files": [
                {"name": "appsscript", "type": "JSON", "source": json.dumps(MANIFEST, ensure_ascii=False)},
                {"name": "Code", "type": "SERVER_JS", "source": CODE},
                {"name": "hub", "type": "HTML", "source": build_html()},
                {"name": "revenue", "type": "HTML", "source": page_html("revenue")},
                {"name": "carcost", "type": "HTML", "source": page_html("carcost")},
                {"name": "runbook", "type": "HTML", "source": page_html("runbook")},
            ]}, method="PUT")
    print("코드 업로드:", "OK" if r.get("files") else r.get("ERR"))
    if r.get("ERR"):
        sys.exit(1)

    v = api(at, f"https://script.googleapis.com/v1/projects/{st['scriptId']}/versions",
            {"description": "허브"})
    print("버전:", v.get("versionNumber") or v.get("ERR"))
    if v.get("ERR"):
        sys.exit(1)

    cfg = {"versionNumber": v["versionNumber"], "manifestFileName": "appsscript", "description": "허브"}
    if st.get("deploymentId"):
        # ★ 기존 배포를 갱신 — 새로 만들면 URL 이 바뀌어 안내를 다시 해야 한다
        u = api(at, f"https://script.googleapis.com/v1/projects/{st['scriptId']}/deployments/{st['deploymentId']}",
                {"deploymentConfig": cfg}, method="PUT")
    else:
        u = api(at, f"https://script.googleapis.com/v1/projects/{st['scriptId']}/deployments", cfg)
        st["deploymentId"] = u.get("deploymentId")
    if u.get("ERR"):
        print("배포 실패:", u["ERR"]); sys.exit(1)

    st["url"] = f"https://script.google.com/a/macros/caring.co.kr/s/{st['deploymentId']}/exec"
    if st["scriptId"] != SCRIPT_ID or st["deploymentId"] != DEPLOY_ID:
        print("\n⚠️ ID 가 바뀌었다 — 이 파일의 SCRIPT_ID/DEPLOY_ID/HUB_URL 과 docs/hq.html 을 갱신할 것")
    print("배포 OK\n")
    print("  편집기:", f"https://script.google.com/home/projects/{st['scriptId']}/edit")
    print("  허브  :", st["url"])


if __name__ == "__main__":
    main()
