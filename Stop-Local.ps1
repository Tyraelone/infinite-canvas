$ErrorActionPreference='Stop'
$canvasRecordFile=Join-Path $PSScriptRoot 'data\local-runtime\processes.json'
if(-not(Test-Path -LiteralPath $canvasRecordFile)){Write-Output 'No locally owned services recorded.'; exit}
$canvasRecords=Get-Content -LiteralPath $canvasRecordFile -Raw | ConvertFrom-Json
foreach($canvasRecord in $canvasRecords){
    $canvasProcess=Get-CimInstance Win32_Process -Filter "ProcessId=$($canvasRecord.pid)" -ErrorAction SilentlyContinue
    if(-not $canvasProcess){Write-Output "$($canvasRecord.name) already stopped"; continue}
    if($canvasProcess.ExecutablePath -ne $canvasRecord.binary -or -not $canvasProcess.CommandLine.Contains($canvasRecord.marker)){
        Write-Warning "Skipped reused or unrelated PID $($canvasRecord.pid)"; continue
    }
    Stop-Process -Id $canvasRecord.pid
    Write-Output "$($canvasRecord.name) stopped (PID $($canvasRecord.pid))"
}
Write-Output 'Shared H3 and video studio services retain their existing lifecycle.'
