# -*- coding: utf-8 -*-
"""공유 허브 자동 배포 (GitHub Actions 전용) — 메뉴만 갈아끼우고 데이터 페이지는 보존.

왜 별도 스크립트인가:
  `deploy_hub.py` 는 **로컬 전용**이다. 매출점검·차량수리비·운영런북 HTML 원본이
  저장소 밖(`클로드코드/`)에 있어서 CI 러너에는 **존재하지 않는다**.
  그대로 돌리면 그 3개 페이지가 **빈 내용으로 덮여 사라진다**.

그래서 이 스크립트는:
  1. Apps Script 프로젝트의 **현재 내용을 먼저 읽고**
  2. `hub`(메뉴)와 `Code`(서버 로직)·`appsscript`(설정)만 새 것으로 교체
  3. `revenue`·`carcost`·`runbook` 은 **읽어온 것을 그대로 되돌려 넣는다**
  4. 새 버전 만들고 기존 배포를 갱신(URL 불변)

⚠️ 데이터 페이지(매출·수리비·런북) 내용을 바꾸려면 여전히 **로컬에서 `deploy_hub` 실행**해야 한다.
   이 스크립트는 허브 **메뉴/레이아웃** 변경만 자동 반영한다.

실행: py -X utf8 -m audit.deploy_hub_ci
  인증: env GOOGLE_OAUTH_JSON (clasprc 형식) → 없으면 ~/.clasprc.json 폴백
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

from audit.deploy_hub import (CODE, MANIFEST, SCRIPT_ID, DEPLOY_ID, build_html, api,
                              _mask_revenue_names, _inject_topbar)

# 저장소 밖 원본이 필요해 CI 에서 못 만드는 페이지들 — 현재 배포본을 그대로 살린다
PRESERVE = ("revenue", "carcost", "runbook")


def revenue_page_from(path: str) -> str:
    """CI 가 방금 만든 매출 합본 HTML → 허브용 페이지.
    ★이름 마스킹 필수 — 합본 원본은 수급자 실명이 그대로 들어 있다."""
    s = pathlib.Path(path).read_text(encoding="utf-8")
    s = _mask_revenue_names(s)     # 실명 → 김○수
    s = _inject_topbar(s)
    return s


def token_ci() -> str:
    """CI 는 env, 로컬은 ~/.clasprc.json."""
    raw = os.environ.get("GOOGLE_OAUTH_JSON")
    d = json.loads(raw) if raw else json.loads(
        (pathlib.Path.home() / ".clasprc.json").read_text(encoding="utf-8"))
    t = d.get("tokens", {}).get("default") or d.get("token") or {}
    body = urllib.parse.urlencode({
        "client_id": d.get("oauth2ClientSettings", {}).get("clientId") or t.get("client_id"),
        "client_secret": d.get("oauth2ClientSettings", {}).get("clientSecret") or t.get("client_secret"),
        "refresh_token": t.get("refresh_token"), "grant_type": "refresh_token"}).encode()
    return json.load(urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=body)))["access_token"]


def main():
    # --revenue-from <합본 HTML>: 그 페이지만 새로 만들어 올린다(나머지는 보존)
    src_revenue = None
    if "--revenue-from" in sys.argv:
        src_revenue = sys.argv[sys.argv.index("--revenue-from") + 1]
    # --carcost: 로컬 '차량_월별수리비내역.html' 로 차량 페이지도 갱신(로컬 정기실행용).
    #   경로가 고정이라 인자를 받지 않고 deploy_hub.page_html 을 그대로 쓴다(중복 구현 금지).
    #   CI 에는 그 원본이 없으므로 CI 에서는 이 플래그를 쓰지 않는다 — 안 주면 종전대로 보존.
    want_carcost = "--carcost" in sys.argv

    at = token_ci()
    base = f"https://script.googleapis.com/v1/projects/{SCRIPT_ID}"

    # 1) 현재 배포 내용 읽기 — 보존할 페이지를 여기서 가져온다
    cur = api(at, f"{base}/content")
    if cur.get("ERR"):
        print("현재 내용 조회 실패:", cur["ERR"]); sys.exit(1)
    keep = {f["name"]: f for f in cur.get("files", []) if f["name"] in PRESERVE}

    if src_revenue:   # 방금 만든 매출 합본으로 교체
        keep["revenue"] = {"name": "revenue", "type": "HTML",
                           "source": revenue_page_from(src_revenue)}
        print(f"  갱신: revenue ← {src_revenue} ({len(keep['revenue']['source'])}자, 이름 마스킹 적용)")
    if want_carcost:  # 로컬 차량 수리비 HTML 로 교체 (원본 없으면 조용히 보존)
        try:
            from audit.deploy_hub import page_html as _page_html
            keep["carcost"] = {"name": "carcost", "type": "HTML",
                               "source": _page_html("carcost")}
            print(f"  갱신: carcost ← 로컬 차량 수리비 ({len(keep['carcost']['source'])}자)")
        except Exception as ex:
            print(f"  ⚠️ carcost 원본을 못 읽어 보존합니다: {ex}")

    _updated = {"revenue"} if src_revenue else set()
    if want_carcost and "carcost" in keep:
        _updated.add("carcost")
    for n in PRESERVE:
        if n in keep and n not in _updated:
            print(f"  보존: {n} ({len(keep[n].get('source',''))}자)")
        elif n not in keep:
            # 없으면 만들지 않는다 — 빈 페이지로 덮어 사라지게 하느니 그대로 두는 게 낫다
            print(f"  ⚠️ {n} 없음 — 이번 배포에서 제외(로컬 deploy_hub 로 올릴 것)")

    # 2) 메뉴·로직만 교체 + 보존 페이지 되돌려 넣기
    files = [
        {"name": "appsscript", "type": "JSON", "source": json.dumps(MANIFEST, ensure_ascii=False)},
        {"name": "Code", "type": "SERVER_JS", "source": CODE},
        {"name": "hub", "type": "HTML", "source": build_html()},
    ] + [keep[n] for n in PRESERVE if n in keep]

    r = api(at, f"{base}/content", {"files": files}, method="PUT")
    if r.get("ERR"):
        print("업로드 실패:", r["ERR"]); sys.exit(1)
    print("코드 업로드: OK (파일", len(files), "개)")

    # 3) 버전 + 기존 배포 갱신 (새로 만들면 URL 이 바뀌어 전 직원 안내를 다시 해야 한다)
    v = api(at, f"{base}/versions", {"description": "허브 자동배포(CI)"})
    if v.get("ERR"):
        print("버전 생성 실패:", v["ERR"]); sys.exit(1)
    print("버전:", v["versionNumber"])

    u = api(at, f"{base}/deployments/{DEPLOY_ID}",
            {"deploymentConfig": {"versionNumber": v["versionNumber"],
                                  "manifestFileName": "appsscript",
                                  "description": "허브"}}, method="PUT")
    if u.get("ERR"):
        print("배포 실패:", u["ERR"]); sys.exit(1)
    print("배포 OK — https://script.google.com/a/macros/caring.co.kr/s/%s/exec" % DEPLOY_ID)


if __name__ == "__main__":
    main()
