param()
$ErrorActionPreference='Stop'
$toolsPath=Split-Path -Parent $PSScriptRoot
$workbenchBase=if ($env:KB_BASE) { [IO.Path]::GetFullPath($env:KB_BASE) } else { $toolsPath }
$workbenchEntry=Join-Path $toolsPath 'kb.py'
$pythonPath=(Get-Command python.exe -ErrorAction Stop).Source

# 只读检查：不结束仍在处理索引或研究任务的工作台。
$probeCode=@'
import json,sys
from pathlib import Path
code=Path(sys.argv[1])
base=Path(sys.argv[2])
sys.path.insert(0,str(code))
import sqlite3
from contextlib import closing
from kbapp.core.paths import Paths
from kbapp.core.win_runtime import listener_pid,process_info
paths=Paths.from_base(base)
with closing(sqlite3.connect(paths.database.as_uri()+'?mode=ro',uri=True)) as db:
    db.row_factory=sqlite3.Row
    jobs=[dict(r) for r in db.execute("SELECT id,kind,status FROM jobs WHERE status IN ('running','queued','stopping')")]
    runs=[dict(r) for r in db.execute("SELECT id,kind,status FROM agent_runs WHERE status IN ('running','queued','stopping')")]
pid=listener_pid(8765)
print(json.dumps({'process':process_info(pid) if pid else None,'jobs':jobs,'runs':runs}))
'@
$probeOutput=& $pythonPath -X utf8 -c $probeCode $toolsPath $workbenchBase
if ($LASTEXITCODE -ne 0) { throw '工作台状态检查失败，未执行重启。' }
$snapshot=$probeOutput | ConvertFrom-Json
if (@($snapshot.jobs).Count -gt 0 -or @($snapshot.runs).Count -gt 0) {
    throw '工作台仍有运行中的任务。请先在页面停止任务或等待完成，再运行本脚本。'
}
if ($snapshot.process) {
    $owner=Get-CimInstance Win32_Process -Filter ('ProcessId = '+$snapshot.process.pid)
    if (-not $owner -or $owner.CommandLine -notmatch [regex]::Escape($workbenchEntry) -or
        $owner.ExecutablePath -ine $snapshot.process.executable) {
        throw '8765端口的进程身份不符合本工作台，未结束任何进程。'
    }
    # 用创建时间核对打开的进程句柄，防止PID被复用后误结束其他程序。
    $stopCode=@'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])))
from kbapp.core.win_runtime import listener_pid,terminate_owned
pid=int(sys.argv[2]);ticks=int(sys.argv[3])
if listener_pid(8765)!=pid:
    raise RuntimeError('监听进程已改变，未结束任何进程')
terminate_owned(pid,ticks)
'@
    & $pythonPath -X utf8 -c $stopCode $toolsPath ([string]$snapshot.process.pid) ([string]$snapshot.process.created_ticks)
    if ($LASTEXITCODE -ne 0) { throw '原服务身份核对或停止失败，未启动第二个服务。' }
}

$stamp=[DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff')
$stdoutPath=Join-Path $workbenchBase ('_index/runtime/logs/workbench-restart-'+$stamp+'.stdout.log')
$stderrPath=Join-Path $workbenchBase ('_index/runtime/logs/workbench-restart-'+$stamp+'.stderr.log')
$previousStartup=$env:KB_NO_STARTUP_JOBS
try {
    # 沿用当前部署方式：重启不额外触发全库索引或云端批处理。
    $env:KB_NO_STARTUP_JOBS='1'
    $server=Start-Process -FilePath $pythonPath -ArgumentList @('-X','utf8',('"'+$workbenchEntry+'"'),'serve') -WorkingDirectory $workbenchBase -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
} finally {
    $env:KB_NO_STARTUP_JOBS=$previousStartup
}
$watch=[Diagnostics.Stopwatch]::StartNew()
$healthy=$null
while ($watch.Elapsed.TotalSeconds -lt 30) {
    if ($server.HasExited) { throw ('新服务已退出，请查看日志：'+$stderrPath) }
    try {
        $status=Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/status' -TimeoutSec 3
        if ($null -ne $status.index_health) { $healthy=$status.index_health;break }
    } catch {}
    Start-Sleep -Milliseconds 500
}
if (-not $healthy) { throw ('新统计接口尚未就绪，请查看日志：'+$stderrPath) }
Write-Output '新版工作台已就绪：http://127.0.0.1:8765/#status'
Write-Output ('已发布向量 '+$healthy.vector_count+' / 可检索来源 '+$healthy.sources+'；待向量化 '+$healthy.vector_pending+'；覆盖率 '+$healthy.percent+'%')
Write-Output '请刷新浏览器页面。'
