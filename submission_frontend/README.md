# Submission Frontend — Manager Dashboard

Standalone FastAPI service providing a polished glassmorphism UI for managers
to approve or reject paused expense sessions on Agent Runtime.

## Setup

```bash
cd submission_frontend
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
# edit .env
```

## Run

```bash
python main.py
# or:
uvicorn main:app --host 0.0.0.0 --port 8080
```

Open [http://localhost:8080](http://localhost:8080).

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Manager dashboard HTML |
| `GET` | `/api/pending` | List unresolved `adk_request_input` interrupts |
| `POST` | `/api/action/{session_id}` | Approve or reject a session (`{interrupt_id, approved}`) |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_CLOUD_PROJECT` | ✅ | GCP project ID |
| `GOOGLE_CLOUD_LOCATION` | optional | Region of Agent Runtime (default: `europe-west4`) |
| `AGENT_RUNTIME_ID` | ✅ | Numeric ID or full resource name of the ReasoningEngine |
| `PORT` | optional | Port to listen on (default: `8080`) |
