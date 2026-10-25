# The `lumina` command on Windows (install.ps1 adds a lumina.cmd shim to your PATH).
#   lumina            open Lumina in your browser
#   lumina app        open the desktop app
#   lumina mcp        MCP server over stdio
#   lumina update     update to the latest version
#   lumina doctor | models | version | uninstall
$ErrorActionPreference = 'Stop'
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:LUMINA_HOME = $Dir
if (-not $env:LUMINA_DATA) { $env:LUMINA_DATA = Join-Path $Dir 'data' }

function Invoke-Lumina([string[]]$Rest) {
  $exe = Join-Path $Dir '.venv\Scripts\lumina.exe'
  if (Test-Path $exe) { & $exe @Rest; exit $LASTEXITCODE }
  $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
  if (-not $uv) { $uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe' }
  & $uv run --project $Dir --quiet lumina @Rest
  exit $LASTEXITCODE
}

$cmd = if ($args.Count -gt 0) { $args[0] } else { '' }
$rest = if ($args.Count -gt 1) { $args[1..($args.Count - 1)] } else { @() }

switch ($cmd) {
  '' { Invoke-Lumina @('serve', '--open') }
  'app' {
    $app = @("$env:LOCALAPPDATA\Lumina\lumina-app.exe", "$env:LOCALAPPDATA\Programs\Lumina\lumina-app.exe") |
      Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $app) { Write-Host 'The desktop app is not installed. Run: lumina update'; exit 1 }
    Start-Process $app
  }
  'update' {
    if (Test-Path (Join-Path $Dir '.git')) {
      # uv.lock is written by uv on this computer (no longer part of the code): a local copy must not stop the update
      try { git -C $Dir checkout --quiet -- uv.lock 2>$null } catch {}
      git -C $Dir pull --ff-only --quiet
    }
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dir 'install.ps1') @rest
  }
  'uninstall' {
    $shim = Join-Path $env:USERPROFILE '.local\bin\lumina.cmd'
    if (Test-Path $shim) { Remove-Item $shim }
    Write-Host 'Removed the lumina command. Uninstall the desktop app from Settings > Apps.'
    Write-Host "Your data is still in $($env:LUMINA_DATA)."
  }
  default { Invoke-Lumina $args }
}
