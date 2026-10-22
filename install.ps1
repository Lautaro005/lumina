# Lumina installer for Windows (PowerShell).
#
#   irm https://lautaro005.github.io/lumina/install.ps1 | iex
#
# Puts Lumina in %USERPROFILE%\.lumina (data in .lumina\data), adds the `lumina` command and installs
# the desktop app from the latest GitHub release. Run it again to update.
# Environment: LUMINA_HOME, LUMINA_REPO, LUMINA_BRANCH, LUMINA_NO_APP=1, LUMINA_MODELS=1
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Repo = if ($env:LUMINA_REPO) { $env:LUMINA_REPO } else { 'https://github.com/Lautaro005/lumina' }
$GhRepo = 'Lautaro005/lumina'
$Branch = if ($env:LUMINA_BRANCH) { $env:LUMINA_BRANCH } else { 'main' }
$Dir = if ($env:LUMINA_HOME) { $env:LUMINA_HOME } else { Join-Path $env:USERPROFILE '.lumina' }
$Bin = Join-Path $env:USERPROFILE '.local\bin'

function Say($m) { Write-Host "› $m" -ForegroundColor Cyan }
function Warn($m) { Write-Host "! $m" -ForegroundColor Yellow }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  throw 'git is required. Install it with: winget install --id Git.Git -e'
}

$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
  Say 'Installing uv (Python manager)…'
  powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" | Out-Null
  $uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
}

if (Test-Path (Join-Path $Dir '.git')) {
  Say "Updating Lumina in $Dir"
  git -C $Dir pull --ff-only --quiet
} elseif (Test-Path (Join-Path $Dir 'pyproject.toml')) {
  Say "Using the Lumina folder at $Dir"
} else {
  Say "Downloading Lumina into $Dir"
  git clone --quiet --depth 1 --branch $Branch $Repo $Dir
}

Say 'Installing dependencies (the first time takes a few minutes)…'
& $uv sync --project $Dir --quiet --no-dev
if ($LASTEXITCODE -ne 0) { throw "Dependency install failed. Run: cd $Dir; uv sync" }
if (-not $env:LUMINA_DATA) { $env:LUMINA_DATA = Join-Path $Dir 'data' }

$appDone = $false
if (-not $env:LUMINA_NO_APP) {
  try {
    $release = Invoke-RestMethod "https://api.github.com/repos/$GhRepo/releases/latest" -Headers @{ Accept = 'application/vnd.github+json' }
    $asset = $release.assets | Where-Object { $_.name -like '*x64-setup.exe' } | Select-Object -First 1
    if ($asset) {
      $setup = Join-Path $env:TEMP $asset.name
      Say "Downloading the desktop app ($($release.tag_name))…"
      Invoke-WebRequest $asset.browser_download_url -OutFile $setup -UseBasicParsing
      Start-Process $setup -ArgumentList '/S' -Wait
      Remove-Item $setup -ErrorAction SilentlyContinue
      $appDone = $true
    } else { Warn 'The latest release has no Windows app yet; use `lumina` (browser).' }
  } catch { Warn 'No desktop release found yet; use `lumina` (browser).' }
}

if ($env:LUMINA_MODELS) {
  Say 'Downloading the base models…'
  & (Join-Path $Dir '.venv\Scripts\lumina.exe') models download embeddinggemma-2 silero-vad parakeet-v3
}

New-Item -ItemType Directory -Force -Path $Bin | Out-Null
$shim = Join-Path $Bin 'lumina.cmd'
Set-Content -Path $shim -Encoding ASCII -Value "@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"$Dir\lumina.ps1`" %*"
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (-not (($userPath -split ';') -contains $Bin)) {
  [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ";$Bin"), 'User')
  $env:Path += ";$Bin"
}

Write-Host ''
Write-Host "✓ Lumina installed in $Dir" -ForegroundColor Green
Write-Host '  Open a new terminal, then:'
Write-Host '    lumina          open Lumina in your browser'
if ($appDone) { Write-Host '    lumina app      open the desktop app (also in the Start menu)' }
Write-Host '    lumina mcp      connect Claude, Cursor or other agents'
Write-Host '    lumina update   update'
