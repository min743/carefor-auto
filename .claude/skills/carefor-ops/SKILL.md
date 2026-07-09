---
name: carefor-ops
description: 케어포(carefor-auto) 운영 런북 — 상담 공지, 출석보고, 지점점검, 차량관리, 케어포 대조 실행 방법과 주의사항. 상담시트/출석관리/차량관리/지점점검/슬랙 공지/케어포 관련 작업 시 사용.
---

# carefor-auto 운영 런북

작업 폴더: `C:\Users\alsgm\OneDrive\Desktop\클로드코드\carefor-auto` (공개 저장소 min743/carefor-auto — **개인정보 커밋 금지**)
파이썬: 시스템 `py -X utf8` 사용 (venv 없음, Playwright·openpyxl·keyring 전부 시스템에 설치됨)

## 주요 명령

| 작업 | 명령 |
|---|---|
| 상담 공지 발송 (엑셀 생성→드라이브 갱신→슬랙) | `py -X utf8 publish_excel.py` (--dry-run 가능) |
| 상담 미입력 제외 (목록 선택→제외번호 추가) | `py -X utf8 exclude_tool.py` (--list로 현재 제외목록) |
| 케어포 수급자 캐시 갱신 (4지점 로그인, ~3분) | `py -X utf8 carefor_phone_check.py` (--skip-download는 캐시 재사용) |
| 지점점검 | `py -X utf8 run_audit.py --branch 천안점 --limit 5` |
| 검수 대장 생성 (평가수정+욕구사정 통합, 승인/진행/반영 열) | `py -X utf8 -m audit.make_review_xlsx 청주` |
| 반영검수 (재점검 전/후 대조) | 재스캔 → `make_eval_fix_xlsx 청주` → `py -X utf8 -m audit.review_recheck 청주` |
| 출석보고 상태 확인 | GitHub API로 daily_report.yml runs 조회 (PAT는 `git credential fill`) |

## 슬랙 (전부 Incoming Webhook — 봇 토큰 없음, 무료 플랜)

- keyring 서비스명 `carefor-auto`: `slack_webhook_url`(#차량관리=테스트방), `arongi_webhook_url`(아롱이 앱), `consult_notice_webhook_url`(상담 공지 채널 C087JL55TA6)
- 발송처 임시 변경: env `ARONGI_WEBHOOK_URL` 지정
- 메시지는 Block Kit (header+context+divider). **연락처는 슬랙 표시 금지(개인정보)** — 엑셀에만.

## 구글 연동

- 시트 데이터 읽기: Apps Script 웹앱 (keyring `consult_webhook_url`, `?ss=main|phone&sheet=시트명`)
- 드라이브 업로드: `publish_excel.py`의 google_token() — `~/.clasprc.json` OAuth 재사용 (drive.file)
- 드라이브 파일은 **고정 이름 덮어쓰기** → 링크 불변, anyone 뷰어 전용
- Apps Script 재배포: scratchpad deploy_webapp.py 패턴 (REST API 직접 — clasp는 Node 24에서 고장)

## 규칙·주의

- 상담 집계 기준: **2026년 5월~** (consult_report.CUTOFF_YM)
- **상담 미입력 판정(2026-07 개편)**: 본사 Y/N이 아니라 **실제 상담시트(ss=phone '충청본부_상담시트' 유선/대면/계약/상담요청/등급신청 탭의 연락처) 번호대조**. 번호가 상담시트에 있으면 입력됨→미입력 제외(본사 하루지연 없음). 실패 시 본사 Y/N 폴백. `consult_report.annotate_missing`이 `r['missing']` 세팅 → build_message/consult_excel/consult_summary_page 전부 이걸 사용.
- **제외번호**: 앱계정(minoneki@caring.co.kr) 소유 구글시트 `충청본부_상담제외번호(자동관리)`(consult_report.EXCL_SSID). 앱이 Drive media PATCH로 쓰기(덮어쓰기), 웹앱 ssid로 읽기. **본사 시트(주보/상담시트)는 읽기전용·수정금지.** 추가는 `exclude_tool.py`(목록 선택). 본사 웹앱은 `spreadsheets.readonly`라 거기에 쓰기 붙이면 재인증→읽기 깨짐(금지).
- **매일 자동 발송**(consult_report.yml, KST 07:00)이 슬랙표+허브+**상세엑셀(publish_excel --dry-run, continue-on-error, GOOGLE_OAUTH_JSON 시크릿)** 전부 갱신 → 입력시 다음날 3개 산출물 자동 제외.
- 출석보고: daily_report.yml 3중 구조 (10:45 본발송 / 11:00 백업 gate / 외부호출 백업 / 실패 슬랙알림). **오전에 dry_run 돌리면 gate 오인 — 금지**
- 잘 돌아가는 발송 경로는 새 방식 검증 전 제거 금지 (2026-07-07 사고)
- 점검 산출물(평가수정지시서·통합점검·검수·문제목록 등)은 `클로드코드/평가준비/<지점>/`에 **지점별 저장** (`audit/deskpath.py`의 `out_dir(key)`). 신규 생성기도 이걸 써서 저장할 것
- **기저귀·요실금 수동 명단**: 케어포 3-1에 태그 없어 자동으로 못 잡는 분은 `평가준비/<지점>/_기저귀요실금_수동명단.txt`(한 줄 한 명)에 추가 → 화장실 부분도움 2순위 강제 편입 + R1/R6 평가 안 낮춤 (`deskpath.manual_continence`). 수정 후 `make_eval_fix_xlsx`·`make_review_xlsx` 재실행
- 크롬 확장은 script.google.com / api.slack.com / accounts.google.com 접근 불가 → Start-Process로 열고 사용자에게 클릭 요청
- 케어포 자동 로그인: eform.caring.co.kr/carefor 포털 → `login2('<ctmnumb>')`, 지점 코드는 config.yaml (%LOCALAPPDATA%\carefor-auto\config.yaml)
- 상세 인수인계: carefor-auto/작업요약_인수인계.md
