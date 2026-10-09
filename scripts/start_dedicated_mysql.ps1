$ErrorActionPreference = 'Stop'
$mysqlBin = 'C:\Program Files\MySQL\MySQL Server 8.4\bin'
$instanceRoot = Join-Path $env:LOCALAPPDATA 'NorteBcpMySQL'
$dataDir = Join-Path $instanceRoot 'data'
$pidFile = Join-Path $instanceRoot 'server.pid'
$errorLog = Join-Path $instanceRoot 'server-error.log'
$serverLog = Join-Path $instanceRoot 'server.log'
$mysqld = Join-Path $mysqlBin 'mysqld.exe'
$mysqlBase = (Get-Item $mysqlBin).Parent.FullName

if (-not (Test-Path $dataDir)) {
    throw "Dedicated MySQL data directory is missing: $dataDir"
}

$listener = Get-NetTCPConnection -LocalPort 3307 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    Write-Output 'Dedicated Norte MySQL is already listening on port 3307.'
    exit 0
}

$process = Start-Process -FilePath $mysqld `
    -ArgumentList @('--no-defaults', "`"--basedir=$mysqlBase`"", "--datadir=$dataDir", '--port=3307', '--bind-address=127.0.0.1', '--mysqlx=0', '--local-infile=0', "--pid-file=$pidFile", "--log-error=$errorLog", '--console') `
    -RedirectStandardOutput $serverLog `
    -RedirectStandardError $errorLog `
    -PassThru

for ($attempt = 0; $attempt -lt 60; $attempt++) {
    Start-Sleep -Milliseconds 500
    $listener = Get-NetTCPConnection -LocalPort 3307 -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        Write-Output 'Dedicated Norte MySQL is ready on 127.0.0.1:3307.'
        exit 0
    }
    if ($process.HasExited) {
        throw "Dedicated MySQL stopped during startup. Check: $errorLog"
    }
}

throw "Dedicated MySQL did not start. Check: $errorLog"
