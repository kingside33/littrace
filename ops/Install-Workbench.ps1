param([string]$TaskName='ResearchLiteratureWorkbench')
$ErrorActionPreference='Stop'
$toolsPath=Split-Path -Parent $PSScriptRoot
$pythonPath=(Get-Command python.exe).Source
$pythonwPath=Join-Path (Split-Path -Parent $pythonPath) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonwPath)) { throw '未找到 pythonw.exe，请先确认当前 Python 环境' }
$entryPath=Join-Path $toolsPath 'kb.py'
$argumentText='"'+$entryPath+'" serve'
$method='scheduled_task'
try {
    $dailyTrigger=New-ScheduledTaskTrigger -Daily -At '09:00'
    $dailyTrigger.StartBoundary=(Get-Date -Year 2026 -Month 10 -Day 31 -Hour 9 -Minute 0 -Second 0).ToString('yyyy-MM-ddTHH:mm:ss')+'+08:00'
    $logonTrigger=New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
    $action=New-ScheduledTaskAction -Execute $pythonwPath -Argument $argumentText -WorkingDirectory $toolsPath
    $principal=New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
    $settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($dailyTrigger,$logonTrigger) -Settings $settings -Principal $principal -Description '本机文献工作台：北京时间09:00增量更新，开机补跑；全文审批后获取，云端配置限额后调用。' -Force | Out-Null
} catch {
    # 无任务计划程序注册权限时，用当前用户启动文件夹启动同一个后台定时服务。
    $startupPath=[Environment]::GetFolderPath('Startup')
    $shortcutPath=Join-Path $startupPath 'ResearchLiteratureWorkbench.lnk'
    $shortcut=(New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
    $shortcut.TargetPath=$pythonwPath
    $shortcut.Arguments=$argumentText
    $shortcut.WorkingDirectory=$toolsPath
    $shortcut.WindowStyle=7
    $shortcut.Description='本机文献工作台，每天北京时间09:00增量更新并补跑'
    $shortcut.Save()
    $method='user_startup'
}
$env:KB_SCHEDULE_METHOD=$method
$env:KB_SCHEDULE_TASK=$TaskName
$stateCode=@'
import os,sys
sys.path.insert(0,os.getcwd())
from kbapp.storage.store import Store
from kbapp.core.io import now
Store().set_state('schedule',{'enabled':True,'method':os.environ['KB_SCHEDULE_METHOD'],'task':os.environ['KB_SCHEDULE_TASK'],'daily_time':'09:00','timezone':'Asia/Shanghai','installed':now()})
'@
Push-Location -LiteralPath $toolsPath
try { $stateCode | & $pythonPath - } finally { Pop-Location }
Write-Output ('自动启动已安装：'+$method+'；每天北京时间09:00，当前用户开机登录后补跑。')
