@echo off
REM Daily: revenue check (carefor) -> deploy shared hub. STRICTLY sequential.
REM ASCII ONLY. Korean paths break under cmd cp949 (pushd fails, task exits 255).
REM All Korean paths live in audit/daily_pull.py. Do NOT put Korean here.
setlocal
pushd "%~dp0"
py -X utf8 -m audit.daily_pull
set RC=%ERRORLEVEL%
popd
endlocal & exit /b %RC%
