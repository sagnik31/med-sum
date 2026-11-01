$env:DATABASE_URL = "postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable"
$env:JWT_SECRET = "dev-secret"
$env:INTERNAL_API_TOKEN = "dev-internal-token"  # must match scripts/run.ps1
$env:HTTP_ADDRESS = ":8080"

go run ./cmd/server