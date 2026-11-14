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

go run ./cmd/server
