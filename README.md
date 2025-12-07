# Med-Sum

Med-Sum is a medical summary application that leverages Vision-Language Models (VLM) and Small Language Models (SLM) to extract and summarize insights from medical reports (PDFs, Images). It features a Go backend, a React frontend, and a Python-based analysis microservice.

## 🚀 Project Overview

The system allows users to:
1.  Upload medical reports/documents.
2.  Automatically extract text and clinical content from images/PDFs using local AI models (via Ollama).
3.  Generate medical insights and summaries from the extracted data.
4.  View and manage these insights through a web interface.

## 🛠️ Prerequisites

Before running the project, ensure you have the following installed:

1.  **PostgreSQL**: Database for storing user data, documents, and insights.
    *   [Download PostgreSQL](https://www.postgresql.org/download/)
    *   *Windows*: Download the installer for your version.
    *   *Mac*: Use Postgres.app or Homebrew (`brew install postgresql`).
    *   *Linux*: Use your package manager (e.g., `sudo apt install postgresql`).

2.  **Go (v1.22+)**: For the backend API.
    *   [Download Go](https://go.dev/dl/)
    *   Follow the specific installation instructions for your OS from the download page.

3.  **Node.js (v18+) & npm**: For the React frontend.
    *   [Download Node.js](https://nodejs.org/en/download/)
    *   Choose the "LTS" (Long Term Support) version.

4.  **Python (v3.10+)**: For the AI processing scripts.
    *   [Download Python](https://www.python.org/downloads/)
    *   Make sure to check "Add Python to PATH" during installation.

5.  **Ollama**: For running local LLMs and VLMs.
    *   [Download Ollama](https://ollama.com/download)
    *   *Windows/Mac*: Download and run the installer.
    *   *Linux*: Run `curl -fsSL https://ollama.com/install.sh | sh`

## 📦 Installation & Setup

### 1. Database Setup
1.  Ensure PostgreSQL is running.
2.  Create a database named `med_sum`.
3.  Run the migration script to set up the schema:
    ```bash
    psql -U postgres -d med_sum -f db/migrations/0001_init.sql
    ```
    *Note: The default connection string expects user `postgres` and password `postgres`. Update `backend/run.ps1` and `scripts/src/insights_service.py` if your credentials differ.*

### 2. Ollama Setup
Pull the required models specified in the configuration:
```bash
ollama pull qwen2.5vl:7b
ollama pull qwen3:4b-instruct-2507-q8_0
```
*Note: Check `scripts/src/config.py` and `scripts/src/extract_report_slm.py` if you wish to use different models.*

### 3. Backend (Go)
Navigate to the backend directory and install dependencies:
```bash
cd backend
go mod tidy
```

### 4. Frontend (React)
Navigate to the frontend directory and install dependencies:
```bash
cd frontend
npm install
```

### 5. Scripts (Python)
Navigate to the scripts directory and install Python dependencies:
```bash
cd scripts
pip install fastapi uvicorn psycopg2-binary ollama pymupdf pydantic
```

## 🏃‍♂️ How to Run

To run the full application, you need to start all three services (Backend, Frontend, Scripts). You can use the provided PowerShell scripts or run the commands manually.

**Option 1: Using PowerShell scripts (Windows)**

Open three separate terminals:

1.  **Backend**:
    ```powershell
    cd backend
    .\run.ps1
    ```
    (Runs on `localhost:8080`)

2.  **Frontend**:
    ```powershell
    cd frontend
    .\run.ps1
    ```
    (Runs on `localhost:5173`)

3.  **AI Service**:
    ```powershell
    cd scripts
    .\run.ps1
    ```
    (Runs on `localhost:9000`)

**Option 2: Manual Start**

1.  **Backend**:
    ```bash
    cd backend
    # Set env vars: DATABASE_URL, JWT_SECRET, HTTP_ADDRESS
    go run ./cmd/server
    ```

2.  **Frontend**:
    ```bash
    cd frontend
    npm run dev
    ```

3.  **AI Service**:
    ```bash
    cd scripts
    uvicorn insights_service:app --app-dir src --reload --port 9000
    ```

## 📂 Project Structure

-   `backend/`: Go backend (API handling, DB interactions)
-   `frontend/`: React frontend (UI)
-   `scripts/`: Python scripts for LLM/VLM processing and FastAPI service
-   `db/`: Database migrations
-   `data/`: Storage for uploaded/generated files
