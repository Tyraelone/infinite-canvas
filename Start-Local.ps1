param([switch]$NoBrowser, [switch]$Dev, [switch]$SkipSharedEngines)
$ErrorActionPreference='Stop'
$canvasRoot=$PSScriptRoot
$canvasData=Join-Path $canvasRoot 'data\local-runtime'
New-Item -ItemType Directory -Path $canvasData -Force | Out-Null
$canvasNode=(Get-Command node -ErrorAction SilentlyContinue).Source
if(-not $canvasNode){$canvasNode='C:\Users\duanz\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'}
$canvasPython='D:\AI\MiniMax-H3\ComfyUI_windows_portable\python_embeded\python.exe'
$canvasCodex=$env:CANVAS_CODEX_BINARY
if(-not $canvasCodex){
    $canvasDesktopBins=Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin'
    if(Test-Path -LiteralPath $canvasDesktopBins){
        $canvasCodex=(Get-ChildItem -LiteralPath $canvasDesktopBins -Directory | ForEach-Object {Get-Item -LiteralPath (Join-Path $_.FullName 'codex.exe') -ErrorAction SilentlyContinue} | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
    }
}
if(-not $canvasCodex){$canvasCodex=(Get-Command codex -ErrorAction SilentlyContinue).Source}
if(-not $canvasCodex){throw 'Codex executable not found. Install and log in to Codex first.'}
$env:CODEX_HOME='C:\Users\duanz\.codex'
$env:CANVAS_CODEX_BINARY=$canvasCodex
$env:PYTHONUTF8='1'
$env:CANVAS_LOCAL_MODE='1'
$env:CANVAS_LOCAL_URL='http://127.0.0.1:18291'
$env:API_BASE_URL='http://127.0.0.1:8081'
$canvasSecret=Join-Path $canvasData 'token.txt'
if(-not(Test-Path -LiteralPath $canvasSecret)){
    $canvasBytes=New-Object byte[] 32
    $canvasRng=[Security.Cryptography.RandomNumberGenerator]::Create()
    $canvasRng.GetBytes($canvasBytes)
    $canvasRng.Dispose()
    [Convert]::ToBase64String($canvasBytes) | Set-Content -LiteralPath $canvasSecret
}
$env:CANVAS_LOCAL_TOKEN=(Get-Content -LiteralPath $canvasSecret -Raw).Trim()
$env:PORT='8081'
$env:LISTEN_HOST='127.0.0.1'
$env:GIN_MODE='release'
$canvasRecords=@()
$canvasRecordFile=Join-Path $canvasData 'processes.json'
if(Test-Path -LiteralPath $canvasRecordFile){$canvasRecords=Get-Content -LiteralPath $canvasRecordFile -Raw | ConvertFrom-Json}
function Start-CanvasProcess($Name,$Binary,$Arguments,$Directory,$Url,$Marker){
    $canvasHeaders=if($Name -eq 'gateway'){@{Authorization="Bearer $env:CANVAS_LOCAL_TOKEN"}}else{@{}}
    $canvasExisting=$canvasRecords | Where-Object name -eq $Name | Select-Object -First 1
    if($canvasExisting){
        try{
            $canvasProcess=Get-CimInstance Win32_Process -Filter "ProcessId=$($canvasExisting.pid)"
            if($canvasProcess.ExecutablePath -eq $Binary -and $canvasProcess.CommandLine.Contains($Marker)){
                Invoke-WebRequest $Url -Headers $canvasHeaders -UseBasicParsing -TimeoutSec 5 | Out-Null
                Write-Output "$Name already ready: $Url"
                return
            }
        }catch{}
    }
    $canvasStart=@{FilePath=$Binary;WorkingDirectory=$Directory;WindowStyle='Hidden';RedirectStandardOutput=(Join-Path $canvasData "$Name-out.log");RedirectStandardError=(Join-Path $canvasData "$Name-error.log");PassThru=$true}
    if($Arguments.Count){$canvasStart.ArgumentList=$Arguments}
    $canvasProcess=Start-Process @canvasStart
    $script:canvasRecords=@($canvasRecords | Where-Object name -ne $Name)+@([pscustomobject]@{name=$Name;pid=$canvasProcess.Id;binary=$Binary;marker=$Marker;url=$Url})
    $script:canvasRecords | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $canvasRecordFile
    for($canvasAttempt=0;$canvasAttempt -lt 60;$canvasAttempt++){
        if($canvasProcess.HasExited){throw "$Name exited. See $canvasData\$Name-error.log"}
        try{Invoke-WebRequest $Url -Headers $canvasHeaders -UseBasicParsing -TimeoutSec 3 | Out-Null; Write-Output "$Name ready: $Url"; return}catch{Start-Sleep -Seconds 1}
    }
    throw "$Name startup timed out. Check $canvasData logs."
}
# Existing shared GPU services have their own identity checks and are never stopped here.
if(-not $SkipSharedEngines){& 'D:\AI\MiniMax-H3\Start-VideoStudio.ps1' -NoBrowser}
Start-CanvasProcess 'gateway' $canvasPython @('-B','-s',"`"$canvasRoot\local-runtime\gateway.py`"",'--port','18291') $canvasRoot 'http://127.0.0.1:18291/v1/models' 'local-runtime\gateway.py'
$canvasBackend=Join-Path $canvasData 'infinite-canvas.exe'
if(-not(Test-Path -LiteralPath $canvasBackend)){throw 'Backend binary missing. Build with data\tools\go\bin\go.exe build -o data\local-runtime\infinite-canvas.exe .'}
Start-CanvasProcess 'backend' $canvasBackend @() $canvasRoot 'http://127.0.0.1:8081/api/settings' 'infinite-canvas.exe'
$canvasNext=Join-Path $canvasRoot 'web\node_modules\next\dist\bin\next'
$canvasMode=if($Dev){'dev'}else{'start'}
if(-not $Dev -and -not(Test-Path -LiteralPath "$canvasRoot\web\.next\BUILD_ID")){throw 'Frontend build missing. Run node web\node_modules\next\dist\bin\next build --webpack from web, or use -Dev.'}
$canvasWebArgs=@("`"$canvasNext`"",$canvasMode,'-H','127.0.0.1','-p','3000')
if($Dev){$canvasWebArgs+= '--webpack'}
Start-CanvasProcess 'web' $canvasNode $canvasWebArgs "$canvasRoot\web" 'http://127.0.0.1:3000/api/local-runtime/profile' 'next\dist\bin\next'
if(-not $NoBrowser){Start-Process 'http://127.0.0.1:3000/canvas'}
Write-Output 'Infinite Canvas ready: http://127.0.0.1:3000/canvas'
