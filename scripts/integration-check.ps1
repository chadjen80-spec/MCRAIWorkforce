<#
.SYNOPSIS
  Integration check of MCR AI Workforce against the real stack (PostgreSQL, Redis, MinIO, API, workers,
  web, n8n) using the existing compose.yaml plus compose.integration.yaml. Requires Docker Desktop.

.DESCRIPTION
  1. Creates the root .env with fresh random secrets if it does not exist (never committed: .gitignore).
  2. Builds and starts the stack; the `migrate` service runs `alembic upgrade head` on PostgreSQL.
  3. Verifies the migration head inside the API container and the health endpoints.
  4. Runs the backend test suite on the real PostgreSQL (a separate `mcr_test` database).
  5. Prints the n8n steps (workflow import, credentials) - see docs/N8N.md.
  Results are written to integration-results.log in the repository root (ignored by git: *.log).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\integration-check.ps1
#>
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $root
$compose = @("compose", "-f", "compose.yaml", "-f", "compose.integration.yaml")
$log = Join-Path $root "integration-results.log"
"MCR AI Workforce integration check $(Get-Date -Format s)" | Out-File $log -Encoding utf8

function Step([string]$name) { Write-Host "`n== $name" -ForegroundColor Cyan; "`n== $name" | Out-File $log -Append -Encoding utf8 }
function Note([string]$text) { Write-Host $text; $text | Out-File $log -Append -Encoding utf8 }

Step "Docker"
docker version --format "{{.Server.Version}}" | ForEach-Object { Note "Docker server $_" }
docker compose version | ForEach-Object { Note $_ }

Step "Secrets (.env)"
if (-not (Test-Path ".env")) {
  function New-Secret([int]$bytes = 32) {
    # Cryptographically secure random bytes (Get-Random is not suitable for secrets).
    $buf = New-Object byte[] $bytes
    $rng = [System.Security.Cryptography.RNGCryptoServiceProvider]::new()
    try { $rng.GetBytes($buf) } finally { $rng.Dispose() }
    [Convert]::ToBase64String($buf) -replace "[+/=]", ""
  }
  $fernet = (& uv run --directory apps/api python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())").Trim()
  $env_lines = (Get-Content ".env.example") -replace "^POSTGRES_PASSWORD=.*", "POSTGRES_PASSWORD=$(New-Secret)" `
    -replace "^MINIO_ROOT_PASSWORD=.*", "MINIO_ROOT_PASSWORD=$(New-Secret)" `
    -replace "^JWT_SECRET=.*", "JWT_SECRET=$(New-Secret 48)" `
    -replace "^SECRETS_ENCRYPTION_KEY=.*", "SECRETS_ENCRYPTION_KEY=$fernet" `
    -replace "^N8N_ENCRYPTION_KEY=.*", "N8N_ENCRYPTION_KEY=$(New-Secret)"
  $env_lines | Set-Content ".env" -Encoding utf8
  Note "Created .env with random secrets (not committed)."
} else { Note ".env exists; using it." }

function Assert-Ok([string]$what) {
  # Native commands do not throw on failure in Windows PowerShell; check the exit code explicitly.
  if ($LASTEXITCODE -ne 0) { Note "FAILED: $what (exit $LASTEXITCODE)"; throw "$what failed (see $log)" }
}

Step "Build and start the stack"
docker @compose up -d --build --wait postgres redis minio
Assert-Ok "starting postgres, redis and minio"
docker @compose up --build migrate
Assert-Ok "building and running migrate"
$migrateExit = (docker @compose ps -a migrate --format "{{.ExitCode}}")
Note "migrate exit code: $migrateExit"
if ($migrateExit -ne "0") { docker @compose logs migrate | Out-File $log -Append -Encoding utf8; throw "migrations failed (see $log)" }
docker @compose up -d --build --wait api worker-core worker-image worker-video worker-maintenance web n8n
Assert-Ok "starting the API, workers, web and n8n"

Step "Migrations on PostgreSQL"
docker @compose exec -T api alembic current | ForEach-Object { Note "alembic current: $_" }
docker @compose exec -T api alembic check | ForEach-Object { Note $_ }

Step "Health"
foreach ($path in @("/health/live", "/health/ready")) {
  $r = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:18000$path"
  Note "$path -> $($r.StatusCode) $($r.Content)"
}

Step "Backend tests on PostgreSQL"
$pgUser = ((Get-Content .env | Select-String "^POSTGRES_USER=") -replace "^POSTGRES_USER=", "")
if (-not $pgUser) { $pgUser = "mcr" }
$pgPass = ((Get-Content .env | Select-String "^POSTGRES_PASSWORD=") -replace "^POSTGRES_PASSWORD=", "")
# Plain arguments only: Windows PowerShell 5.1 drops nested quotes when it passes a `sh -c "..."` string
# to a native command, which silently skipped this step before.
$exists = (docker @compose exec -T postgres psql -U $pgUser -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = 'mcr_test'" | Out-String).Trim()
if ($exists -ne "1") {
  docker @compose exec -T postgres psql -U $pgUser -d postgres -c "CREATE DATABASE mcr_test" | Out-Null
  Assert-Ok "creating the mcr_test database"
  Note "Created database mcr_test."
} else { Note "Database mcr_test exists." }
$env:TEST_DATABASE_URL = "postgresql+psycopg://${pgUser}:${pgPass}@127.0.0.1:15432/mcr_test"
Push-Location apps/api
try {
  # --tb=line keeps connection strings (which contain the password) out of the log.
  uv run pytest -q --tb=line 2>&1 | Tee-Object -Variable testOut | Out-Null
  $testExit = $LASTEXITCODE
  $testOut | ForEach-Object { "$_" -replace [regex]::Escape($pgPass), "***" } | Select-Object -Last 3 | ForEach-Object { Note $_ }
} finally { Pop-Location; Remove-Item Env:TEST_DATABASE_URL }
if ($testExit -ne 0) { Note "FAILED: backend tests on PostgreSQL (exit $testExit)"; throw "backend tests on PostgreSQL failed (see $log)" }

Step "n8n"
Note "n8n UI: http://localhost:15678 - import n8n/workflows/mcr-ai-workforce.json and create the two"
Note "credentials as in docs/N8N.md (API key: docker compose exec api python -m app.cli create-api-key n8n)."
Note "Webhook for this stack: http://localhost:15678/webhook/mcr-ai-workforce"

Write-Host "`nDone. Results: $log" -ForegroundColor Green
