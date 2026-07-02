@echo off
rem 지점점검 자동 실행 (작업 스케줄러용) — 로그: audit_results\last_run.log
cd /d "C:\Users\alsgm\Desktop\클로드코드\carefor-auto"
echo ===== %date% %time% 점검 시작 ===== >> "audit_results\last_run.log"
".venv\Scripts\python.exe" -X utf8 run_audit.py >> "audit_results\last_run.log" 2>&1
echo ===== %date% %time% 점검 종료 ===== >> "audit_results\last_run.log"
