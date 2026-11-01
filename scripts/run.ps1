$env:INTERNAL_API_TOKEN = "dev-internal-token"  # must match backend/run.ps1
uvicorn insights_service:app --app-dir src --reload --port 9000