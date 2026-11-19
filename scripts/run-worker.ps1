<#
.SYNOPSIS
  Runs the MCR AI Workforce background worker on Windows and keeps it running.

.DESCRIPTION
  Starts two processes from apps/api and restarts either one if it stops:
    - the Celery worker for every queue (threads pool, Windows-safe), and
    - Celery beat, which schedules the 30-second lease reaper that recovers tasks lost by a crashed worker.
  Output goes to apps/api/data/worker.log and apps/api/data/beat.log. Stop with Ctrl+C.
  The dashboard and Mira warn when no worker heartbeat has arrived for 75 seconds.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\run-worker.ps1
#>
param(
  [int]$Concurrency = 6,
  [int]$RestartDelaySeconds = 5
)

$ErrorActionPreference = "Stop"
$api = Join-Path $PSScriptRoot "..\apps\api" | Resolve-Path
$data = Join-Path $api "data"
New-Item -ItemType Directory -Force -Path $data | Out-Null
$queues = "planner,content,image,document,video,qa,maintenance"

function Start-Part([string]$name, [string[]]$celeryArgs, [string]$log) {
  $argList = @("run", "celery", "-A", "app.worker.celery_app") + $celeryArgs
  Start-Process -FilePath "uv" -ArgumentList $argList -WorkingDirectory $api -NoNewWindow -PassThru `
    -RedirectStandardOutput $log -RedirectStandardError "$log.err"
}

$workerArgs = @("worker", "-P", "threads", "-c", "$Concurrency", "-Q", $queues, "-l", "INFO")
$beatArgs = @("beat", "-s", (Join-Path $data "celerybeat-schedule"), "-l", "INFO")

$worker = Start-Part "worker" $workerArgs (Join-Path $data "worker.log")
$beat = Start-Part "beat" $beatArgs (Join-Path $data "beat.log")
Write-Host "Worker (pid $($worker.Id)) and beat (pid $($beat.Id)) started. Ctrl+C stops both."

try {
  while ($true) {
    Start-Sleep -Seconds 2
    if ($worker.HasExited) {
      Write-Warning "$(Get-Date -Format s) worker stopped (exit $($worker.ExitCode)); restarting in $RestartDelaySeconds s"
      Start-Sleep -Seconds $RestartDelaySeconds
      $worker = Start-Part "worker" $workerArgs (Join-Path $data "worker.log")
    }
    if ($beat.HasExited) {
      Write-Warning "$(Get-Date -Format s) beat stopped (exit $($beat.ExitCode)); restarting in $RestartDelaySeconds s"
      Start-Sleep -Seconds $RestartDelaySeconds
      $beat = Start-Part "beat" $beatArgs (Join-Path $data "beat.log")
    }
  }
}
finally {
  foreach ($p in @($worker, $beat)) {
    # /T stops the whole tree: uv starts Python as a child process.
    if ($p -and -not $p.HasExited) { & taskkill.exe /PID $p.Id /T /F 2>$null | Out-Null }
  }
  Write-Host "Worker and beat stopped."
}
