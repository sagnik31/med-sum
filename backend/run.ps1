$env:DATABASE_URL = "postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable"
$env:INTERNAL_API_TOKEN = "dev-internal-token"  # must match scripts/run.ps1
$env:HTTP_ADDRESS = ":8080"
$env:ALLOWED_ORIGINS = "http://localhost:5173"  # the Vite dev server, which proxies /api here

go run ./cmd/server