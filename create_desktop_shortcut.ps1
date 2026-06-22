# 바탕화면에 "케어포 자동 보고" 바로가기 생성

$ProjectDir = $PSScriptRoot
$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop "케어포 자동 보고.lnk"

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"
$Shortcut.Arguments = "run.py"
$Shortcut.WorkingDirectory = $ProjectDir
$Shortcut.IconLocation = "shell32.dll,167"  # 차트 모양 아이콘
$Shortcut.Description = "케어포 → 구글시트 자동 출석 보고"
$Shortcut.Save()

Write-Host ""
Write-Host "✅ 바로가기 생성 완료" -ForegroundColor Green
Write-Host "   위치: $ShortcutPath"
Write-Host ""
Write-Host "바탕화면에 '케어포 자동 보고' 아이콘이 보입니다."
Write-Host "더블클릭하면 앱이 실행됩니다."
