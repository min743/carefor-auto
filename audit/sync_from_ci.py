# -*- coding: utf-8 -*-
"""CI 가 올린 지점점검 결과를 로컬로 내려받아 audit_results/ 를 최신화한다.

    py -X utf8 -m audit.sync_from_ci            # 최근 성공한 지점점검 런에서
    py -X utf8 -m audit.sync_from_ci --run ID   # 특정 런 지정
    py -X utf8 -m audit.sync_from_ci --dry-run  # 받기만 하고 audit_results/ 는 안 건드림

왜 필요한가 (2026-07-17, [[local-dashboard-stale]]):
  대시보드가 두 개인데 서로 갱신하지 않았다.
    · 로컬 audit_results/dashboard_data.js — 실명 있음. 로컬 run_audit.py 를 돌려야만 갱신(2시간+)
    · 배포 docs/dashboard_data.js         — 이름 제거본. CI 07:00 런이 갱신
  CI 는 07:00 에 4지점을 이미 다 긁어 '실명 포함 완전한 데이터'를 러너 안에서 만드는데,
  공개 저장소에 못 올리니(개인정보) 로컬이 같은 스캔을 2시간 걸려 또 했다. 그래서 로컬이
  일주일씩 묵었다(둔산·천안 07-10). 의도된 건 '이름을 지우는 것'뿐이고 로컬이 묵는 건 결함이다.

어떻게 (merge job 이 러너에서 하는 일과 동일):
  CI 는 지점 결과를 AUDIT_ARTIFACT_KEY 로 대칭 암호화해 아티팩트(audit-<key>/result.json.gpg)로
  올린다. 이 스크립트는 같은 열쇠로 로컬에서 받아 푼다 → audit_results/<지점>.json 복원
  → collector._write_dashboard_data() (기존 검증된 함수)가 대시보드·요약페이지 재생성.
  케어포에 접속하지 않으므로 세션 충돌이 없다([[carefor-ci-single-account]]).

준비물:
  1) 열쇠 — GitHub Secrets 의 AUDIT_ARTIFACT_KEY 와 '같은 값'을 로컬 keyring 에:
       py -X utf8 -m audit.sync_from_ci --set-key      (입력이 화면에 안 보인다)
     ★ py -c "...set_audit_artifact_key('열쇠')" 로 넣지 말 것 — PowerShell 이 명령줄을
       ConsoleHost_history.txt 에 평문으로 남긴다(셸 히스토리 = 파일 저장과 같다).
  2) GitHub 토큰 — git credential helper 에 이미 있는 걸 실행 시점에 읽어 쓴다(파일 저장 없음).
  3) gpg — 로컬에 설치돼 있어야 한다(확인: gpg --version).

한계:
  · 아티팩트 보존기간이 workflow 의 retention-days(현재 1일)로 제한된다. 이틀 지난 런은
    아티팩트가 사라져 받을 수 없다 → 그 경우 안내만 하고 로컬 데이터는 건드리지 않는다.
  · CI 가 지점 job 에 실패하면 그 지점 아티팩트가 없다. 없는 지점은 '건너뜀'으로 알리고
    기존 로컬 파일을 그대로 둔다(빈 값으로 덮어써 '수집전'이 되는 사고를 막는다).
  · ★ audit_results/ 만 건드리는 게 아니다 — _write_dashboard_data() 안의
    summary_page.generate() 가 추적 대상인 docs/audit_summary.html 을 재생성한다.
    실행하면 그 파일이 dirty 해진다(내용은 CI 와 같은 마스킹 체인을 타 실명은 없다).
    이후 `git add docs/` 나 `commit -a` 의 사정권이니 알고 있을 것.
"""
from __future__ import annotations

import argparse
import getpass
import io
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import requests

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
AUDIT_DIR = ROOT / "audit_results"
REPO = "min743/carefor-auto"
WORKFLOW = "weekly_audit.yml"
API = "https://api.github.com"


def _token() -> str | None:
    """git credential helper 에서 GitHub 토큰을 읽는다 (파일에 저장하지 않는다)."""
    try:
        p = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=15, cwd=str(ROOT),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in p.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip()
    return None


def _hdr(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}


def _latest_run(tok: str) -> dict | None:
    """가장 최근의 '성공한' 지점점검 런. 실패한 런의 아티팩트는 불완전할 수 있어 제외한다."""
    r = requests.get(f"{API}/repos/{REPO}/actions/workflows/{WORKFLOW}/runs",
                     headers=_hdr(tok), params={"per_page": 20}, timeout=30)
    r.raise_for_status()
    for run in r.json().get("workflow_runs", []):
        if run.get("status") == "completed" and run.get("conclusion") == "success":
            return run
    return None


def _artifacts(tok: str, run_id: int) -> list[dict]:
    r = requests.get(f"{API}/repos/{REPO}/actions/runs/{run_id}/artifacts",
                     headers=_hdr(tok), params={"per_page": 100}, timeout=30)
    r.raise_for_status()
    return [a for a in r.json().get("artifacts", []) if a["name"].startswith("audit-")]


def _decrypt(blob: bytes, key: str, out: Path) -> bool:
    """result.json.gpg(대칭) → out.

    ★ 암호는 argv 로 넘기지 않는다(--passphrase-fd 0 + stdin). 이유:
      · argv 는 프로세스 목록·EDR·감사로그(4688)에 남는다.
      · 더 현실적인 건 예외 경로다 — subprocess.TimeoutExpired 의 메시지에 argv 가
        통째로 들어가서(실측 확인), 타임아웃 한 번이면 열쇠가 콘솔에 그대로 찍힌다.
      임시파일이 필요한 건 '암호문'(gpg 가 파일 인자를 받음) 때문이지 '암호' 때문이 아니다.
      (초안 주석에 "gpg 는 stdin 으로 암호를 못 받는다"고 적었는데 사실이 아니었다.)
    """
    with tempfile.TemporaryDirectory() as td:
        enc = Path(td) / "result.json.gpg"
        enc.write_bytes(blob)
        try:
            p = subprocess.run(
                ["gpg", "--batch", "--yes", "--decrypt",
                 "--passphrase-fd", "0", "--pinentry-mode", "loopback",
                 "-o", str(out), str(enc)],
                input=key, capture_output=True, text=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            # str(e) 에 argv 가 들어가므로 예외 객체를 그대로 찍지 않는다.
            print("    복호화 실패: gpg 응답 없음(120초 초과)")
            return False
        except OSError as e:
            print(f"    복호화 실패: gpg 실행 불가 ({type(e).__name__})")
            return False
        if p.returncode != 0:
            err = (p.stderr or "").strip().splitlines()
            print(f"    복호화 실패: {err[-1] if err else '원인 불명'}")
            return False
    return True


def _existing(branch: str) -> dict | None:
    """이미 있는 로컬 지점 결과 (덮어쓰기 전 대조용)."""
    f = AUDIT_DIR / f"{branch}.json"
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


SHRINK_RATIO = 0.9        # 기존 인원의 90% 미만이면 거부
MIN_PEOPLE_NO_BASELINE = 50   # 기존 파일이 없을 때 이보다 적으면 거부 (아래 참조)


def _reject_reason(new: dict, old: dict | None) -> str | None:
    """새 데이터로 덮어쓰면 안 되는 이유. None 이면 덮어써도 된다.

    ★ 왜 필요한가: 아티팩트 업로드는 test_mode 만 보고 limit 은 안 본다(워크플로 191·207행).
      그래서 `limit=5` 로 디버그 디스패치한 런도 people=5 로 암호화·업로드되고 conclusion=success
      가 되어 _latest_run() 에 뽑힌다. 그걸로 139명짜리를 덮어쓰면 5명 기준 item_results 만
      남아 실제 미흡이 사라진 채 '양호'로 보인다 = 은폐. people>0 가드로는 못 잡는다.
      JSON 에 limit 표시가 없어 내용만으로는 구분이 불가능하므로 '기존 대비 급감'으로 막는다.
      부분 수집(세션 끊김 등)도 같은 그물에 걸린다([[carefor-ci-single-account]]).

    ★ 기준선이 없을 때(old=None): 급감 판정은 상대비교라 무력해진다. 그런데 새 PC·파일
      삭제 후 첫 동기화가 정확히 그 상황이고, sync_from_ci 를 쓰는 게 바로 그때다.
      그래서 절대 하한을 둔다 — 실측 지점 규모는 139~227명이라 50명은 한참 아래고,
      limit 디스패치 기본값(3~5명)은 확실히 걸린다. 신규 소규모 지점이 걸리면 --force.
      "확신 없으면 덮어쓰지 않는다" 쪽으로 틀리게 둔 것이다(반대는 은폐라 되돌릴 수 없다).
    """
    if not new.get("people"):
        return "수급자 0명 — 수집 실패 의심"
    if not old:
        if new["people"] < MIN_PEOPLE_NO_BASELINE:
            return (f"기존 파일이 없어 대조 불가 + 수급자 {new['people']}명 "
                    f"(< {MIN_PEOPLE_NO_BASELINE}명) — limit 디스패치 의심")
        return None
    op, np_ = old.get("people") or 0, new["people"]
    if op and np_ < op * SHRINK_RATIO:
        return f"수급자 급감 {op}명 → {np_}명 (limit 디스패치·부분수집 의심)"
    o_at, n_at = (old.get("run_at") or ""), (new.get("run_at") or "")
    if o_at and n_at and n_at < o_at:
        return f"수집시각 역행 {o_at} → {n_at} (옛 런으로 롤백)"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="CI 지점점검 결과를 로컬 audit_results/ 로 동기화")
    ap.add_argument("--run", type=int, help="런 ID 지정 (기본: 최근 성공 런)")
    ap.add_argument("--dry-run", action="store_true", help="audit_results/ 를 건드리지 않고 확인만")
    ap.add_argument("--force", action="store_true",
                    help="인원 급감·시각 역행 거부를 무시하고 덮어쓴다 (의도한 롤백일 때만)")
    ap.add_argument("--set-key", action="store_true",
                    help="아티팩트 열쇠를 keyring 에 저장한다 (입력이 화면에 안 보임)")
    args = ap.parse_args()

    from src import credentials

    if args.set_key:
        # getpass: 화면에도 셸 히스토리에도 남지 않는다.
        v = getpass.getpass("AUDIT_ARTIFACT_KEY (GitHub Secrets 와 같은 값): ").strip()
        if not v:
            print("입력이 비어 있습니다. 저장하지 않았습니다.")
            return 1
        credentials.set_audit_artifact_key(v)
        print(f"저장 완료 (keyring, {len(v)}자). 이제 인자 없이 실행하세요.")
        return 0

    if shutil.which("gpg") is None:
        print("ERROR: gpg 가 없습니다. GnuPG 를 설치하세요 (확인: gpg --version).")
        return 1

    key = credentials.get_audit_artifact_key()
    if not key:
        print("ERROR: 로컬에 아티팩트 열쇠가 없습니다.")
        print("  GitHub Secrets 의 AUDIT_ARTIFACT_KEY 와 '같은 값'을 넣으세요:")
        print("    py -X utf8 -m audit.sync_from_ci --set-key")
        return 1

    tok = _token()
    if not tok:
        print("ERROR: GitHub 토큰을 git credential 에서 찾지 못했습니다.")
        print("  한 번 push/pull 해서 자격증명을 저장한 뒤 다시 실행하세요.")
        return 1

    if args.run:
        r = requests.get(f"{API}/repos/{REPO}/actions/runs/{args.run}", headers=_hdr(tok), timeout=30)
        r.raise_for_status()
        run = r.json()
        # _latest_run() 은 성공 런만 고르는데 --run 은 그냥 통과시켜 문서와 어긋났다.
        # 실패·진행중 런의 아티팩트는 일부 지점만 있거나 부분 수집이라 그대로 쓰면 위험하다.
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            print(f"ERROR: 런 {args.run} 은 {run.get('status')}/{run.get('conclusion')} 입니다.")
            print("  성공한 런만 동기화합니다(부분 데이터로 덮어쓰는 사고 방지). 의도한 것이면 --force")
            if not args.force:
                return 1
            print("  [--force] 무시하고 진행합니다.")
    else:
        run = _latest_run(tok)
        if not run:
            print("ERROR: 최근 20건 중 성공한 지점점검 런이 없습니다.")
            return 1

    print(f"런 {run['id']} ({run.get('run_started_at')}, {run.get('conclusion')}) 에서 동기화")

    arts = _artifacts(tok, run["id"])
    if not arts:
        print("ERROR: 이 런에 지점 아티팩트가 없습니다.")
        print("  · 보존기간(retention-days)이 지나 삭제됐거나,")
        print("  · test_mode=true 로 돌아 아티팩트를 안 올린 런입니다.")
        print("  로컬 데이터는 그대로 두었습니다.")
        return 1

    expired = [a["name"] for a in arts if a.get("expired")]
    if expired:
        print(f"  ⚠️ 만료된 아티팩트 {len(expired)}개: {', '.join(expired)}")
    arts = [a for a in arts if not a.get("expired")]
    if not arts:
        print("ERROR: 살아 있는 아티팩트가 없습니다(전부 만료). 로컬 데이터는 그대로 두었습니다.")
        return 1

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    done, failed = [], []
    for a in sorted(arts, key=lambda x: x["name"]):
        print(f"  {a['name']} ...", end=" ", flush=True)
        try:
            z = requests.get(a["archive_download_url"], headers=_hdr(tok), timeout=180)
            z.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
                names = [n for n in zf.namelist() if n.endswith(".gpg")]
                if not names:
                    print("실패 (zip 안에 .gpg 없음)")
                    failed.append(a["name"]); continue
                blob = zf.read(names[0])
        except (requests.RequestException, zipfile.BadZipFile) as e:
            print(f"실패 ({type(e).__name__})")
            failed.append(a["name"]); continue

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "result.json"
            if not _decrypt(blob, key, tmp):
                failed.append(a["name"]); continue
            try:
                d = json.loads(tmp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                print(f"실패 (복호화본 파손: {e})")
                failed.append(a["name"]); continue

            # ★ 지점명은 아티팩트 이름이 아니라 '내용'에서 얻는다 — 이름→지점 매핑을
            #   여기 또 적으면 워크플로와 갈라져 같은 사고가 난다([[audit-ci-package-split]]).
            branch = d.get("branch")
            if not branch or "item_results" not in d:
                print("실패 (branch/item_results 없음 — 지점 결과가 아님)")
                failed.append(a["name"]); continue

            why = _reject_reason(d, _existing(branch))
            if why and not args.force:
                print(f"거부 ({branch}: {why})")
                print("       기존 로컬 파일을 그대로 뒀습니다. 의도한 것이면 --force")
                failed.append(a["name"]); continue
            if why and args.force:
                print(f"[--force] 경고 무시하고 덮어씀 ({branch}: {why})")

            if args.dry_run:
                print(f"OK ({branch}, {d['people']}명, run_at={d.get('run_at')}) [dry-run]")
            else:
                (AUDIT_DIR / f"{branch}.json").write_text(
                    json.dumps(d, ensure_ascii=False), encoding="utf-8")
                print(f"OK ({branch}, {d['people']}명, run_at={d.get('run_at')})")
            done.append(branch)

    if failed:
        print(f"\n⚠️ 실패·건너뜀 {len(failed)}개: {', '.join(failed)}")
        print("  해당 지점의 기존 로컬 파일은 건드리지 않았습니다.")
    if not done:
        print("복원된 지점이 없습니다. 로컬 데이터는 그대로입니다.")
        return 1
    if args.dry_run:
        print(f"\n[dry-run] {len(done)}개 지점 확인 — 아무것도 쓰지 않았습니다: {', '.join(done)}")
        return 0

    print(f"\n{len(done)}개 지점 복원: {', '.join(done)}")
    from audit.collector import _write_dashboard_data
    _write_dashboard_data()
    print("대시보드 데이터·요약페이지 재생성 완료 → audit_results/dashboard_data.js")
    return 0


if __name__ == "__main__":
    sys.exit(main())
