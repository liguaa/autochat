$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $projectRoot 'frontend'
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$env:PYTHONNOUSERSITE = '1'
$env:PYTHONUSERBASE = Join-Path $projectRoot '.python-user'

Push-Location $frontend
try {
    npm ci --cache (Join-Path $projectRoot '.npm-cache') --registry https://registry.npmjs.org
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'frontend build failed' }
} finally {
    Pop-Location
}

Push-Location $projectRoot
try {
    & $python -m PyInstaller --noconfirm scripts\AutoChat.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
} finally {
    Pop-Location
}
