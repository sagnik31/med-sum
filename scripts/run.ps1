$env:INTERNAL_API_TOKEN = "dev-internal-token"  # must match backend/run.ps1
# Uses the virtual environment from the README's setup steps (scripts\.venv).
.\.venv\Scripts\python.exe -m uvicorn insights_service:app --app-dir src --reload --port 9000
