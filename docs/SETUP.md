# 처음 설치 가이드

## 1단계. 구글 시트 API 인증 (서비스 계정 방식)

### 1-1. Google Cloud 프로젝트 생성
1. https://console.cloud.google.com 접속 (본인 구글 계정으로 로그인)
2. 상단의 프로젝트 선택 → **새 프로젝트** → 이름: `carefor-auto` → 만들기
3. 생성된 프로젝트로 진입

### 1-2. 구글시트 API 활성화
1. 좌측 메뉴 → **API 및 서비스** → **라이브러리**
2. 검색창에 `Google Sheets API` → 클릭 → **사용** 버튼

### 1-3. 서비스 계정 만들기
1. 좌측 메뉴 → **API 및 서비스** → **사용자 인증 정보**
2. 상단 **+ 사용자 인증 정보 만들기** → **서비스 계정**
3. 이름: `carefor-auto-sheet` → 만들고 계속하기 → 완료
4. 생성된 서비스 계정 클릭 → **키** 탭 → **키 추가** → **새 키 만들기** → **JSON** → 만들기
5. 다운로드된 JSON 파일을 다음 경로로 이동/이름 변경:
   ```
   C:\Users\alsgm\AppData\Local\carefor-auto\google_service_account.json
   ```

### 1-4. 구글시트에 서비스 계정 공유
1. 다운로드한 JSON 파일을 메모장으로 열고 `"client_email"` 값 복사 (예: `carefor-auto-sheet@...iam.gserviceaccount.com`)
2. 자동입력할 구글시트 열기 → 우측 상단 **공유** 버튼
3. 위에서 복사한 이메일 붙여넣기 → 권한 **편집자** → 보내기


## 2단계. 슬랙 Webhook 생성

1. https://api.slack.com/apps → **Create New App** → **From scratch**
2. 앱 이름: `케어포 자동 보고` → 워크스페이스 선택 → Create App
3. 좌측 메뉴 **Incoming Webhooks** → 토글 켜기 (On)
4. 하단 **Add New Webhook to Workspace** → 채널 선택 (지점 공지방) → 허용
5. 생성된 **Webhook URL** 복사 (https://hooks.slack.com/... 형태)
6. 앱 실행 후 **설정 화면**에서 이 URL 붙여넣기 (또는 PowerShell에서 직접 등록):
   ```powershell
   cd "$env:USERPROFILE\OneDrive\Desktop\클로드 코드\carefor-auto"
   .\.venv\Scripts\python.exe -c "from src import credentials; credentials.set_slack_webhook('붙여넣을_URL')"
   ```


## 3단계. config.yaml 작성

`C:\Users\alsgm\AppData\Local\carefor-auto\config.yaml` 파일을 메모장으로 열어서:

- `branches:` 아래 각 지점의 `capacity:` 에 **정원** 입력
- `google_sheet.spreadsheet_id:` 에 구글시트 URL의 `/d/` 와 `/edit` 사이 문자열 입력
  - 예: `https://docs.google.com/spreadsheets/d/1Q1N1-6aSE1LSPAtNXi5Z2jj0_jKIj_hpR2BXaJ6yrm0/edit` 
    → `spreadsheet_id: "1Q1N1-6aSE1LSPAtNXi5Z2jj0_jKIj_hpR2BXaJ6yrm0"`


## 4단계. 실행

바탕화면 아이콘 더블클릭, 또는:
```powershell
cd "$env:USERPROFILE\OneDrive\Desktop\클로드 코드\carefor-auto"
.\.venv\Scripts\python.exe run.py
```


## 트러블슈팅

**"google_service_account.json 파일을 찾을 수 없습니다"**
→ 1-3단계 다시 확인. 파일 위치: `%LOCALAPPDATA%\carefor-auto\`

**"슬랙 webhook URL이 자격증명에 저장되어 있지 않음"**
→ 2단계 6번 명령 실행했는지 확인

**"구글시트 권한 없음"**
→ 1-4단계의 공유 설정 확인. 서비스 계정 이메일에 **편집자** 권한 필요
