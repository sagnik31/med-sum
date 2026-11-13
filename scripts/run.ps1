$env:INTERNAL_API_TOKEN = "dev-internal-token"  # must match backend/run.ps1
# Development only: without DATABASE_URL, the service then uses the local
# med_sum database as postgres (scripts/src/settings.py). Anywhere else, set
# DATABASE_URL, ideally to the medsum_ai role (README, "Database roles").
$env:MEDSUM_ENV = "dev"
# Uses the virtual environment from the README's setup steps (scripts\.venv).
.\.venv\Scripts\python.exe -m uvicorn insights_service:app --app-dir src --reload --port 9000
