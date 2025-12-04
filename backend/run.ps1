$env:DATABASE_URL = "postgres://postgres:postgres@localhost:5432/med_sum?sslmode=disable"
$env:JWT_SECRET = "dev-secret"
$env:HTTP_ADDRESS = ":8080"

go run ./cmd/server