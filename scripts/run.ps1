# Reads .env.local (KEY=VALUE lines; # starts a comment) into the
# environment. Secrets live there, untracked, never in this script.
$envFile = Join-Path $PSScriptRoot ".env.local"
if (-not (Test-Path $envFile)) {
    Write-Error "Missing $envFile. Copy .env.example to .env.local and fill it in."
    exit 1
}
foreach ($line in Get-Content $envFile) {
    $line = $line.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { continue }
    $name, $value = $line -split "=", 2
    Set-Item -Path "Env:$($name.Trim())" -Value $value.Trim()
}

# Listens on 127.0.0.1 only: only the Go backend on this machine calls it.
# Uses the virtual environment from the README's setup steps (scripts\.venv).
.\.venv\Scripts\python.exe -m uvicorn insights_service:app --app-dir src --reload --host 127.0.0.1 --port 9000
