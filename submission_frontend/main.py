"""
Manager Dashboard — submission_frontend/main.py
================================================
Standalone FastAPI service that sits in front of the ambient-expense-agent
Agent Runtime deployment and gives managers a polished UI to approve or
reject paused expense sessions.

Environment variables (see .env.example):
  GOOGLE_CLOUD_PROJECT     – GCP project id
  GOOGLE_CLOUD_LOCATION    – location of the Agent Runtime (default: europe-west4)
  AGENT_RUNTIME_ID         – numeric id **or** full resource name of the
                             ReasoningEngine / Agent Runtime resource.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

# ── env / logging ─────────────────────────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("dashboard")

# ── config ────────────────────────────────────────────────────────────────────
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "europe-west4")
_RAW_RUNTIME_ID = os.environ.get("AGENT_RUNTIME_ID", "")

# Accept both "1234567890" and the full resource name
_NUMERIC_RE = re.compile(r"\d+$")
if _NUMERIC_RE.search(_RAW_RUNTIME_ID):
    AGENT_ENGINE_ID = _NUMERIC_RE.search(_RAW_RUNTIME_ID).group()
else:
    AGENT_ENGINE_ID = _RAW_RUNTIME_ID  # already short or empty

APP_NAME = "expense_agent"
USER_ID = "default-user"  # must match the owner used by the agent runtime

# ── ADK session service (VertexAiSessionService) ───────────────────────────────
from google.adk.sessions.vertex_ai_session_service import VertexAiSessionService

session_service: VertexAiSessionService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global session_service
    if not PROJECT or not AGENT_ENGINE_ID:
        log.warning(
            "GOOGLE_CLOUD_PROJECT or AGENT_RUNTIME_ID is not set — "
            "API calls will fail at runtime."
        )
    else:
        session_service = VertexAiSessionService(
            project=PROJECT,
            location=LOCATION,
            agent_engine_id=AGENT_ENGINE_ID,
        )
        log.info(
            "VertexAiSessionService ready  project=%s  location=%s  engine=%s",
            PROJECT,
            LOCATION,
            AGENT_ENGINE_ID,
        )
    yield


app = FastAPI(title="Expense Approval Dashboard", lifespan=lifespan)


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_service() -> VertexAiSessionService:
    if session_service is None:
        raise HTTPException(
            status_code=503,
            detail="Session service not initialised — check GOOGLE_CLOUD_PROJECT and AGENT_RUNTIME_ID.",
        )
    return session_service


def _extract_function_calls(events: list[dict]) -> dict[str, dict]:
    """Return {interrupt_id: event_dict} for every adk_request_input *call*."""
    calls: dict[str, dict] = {}
    for ev in events:
        for part in _iter_parts(ev):
            fc = part.get("functionCall") or part.get("function_call")
            if fc and fc.get("name") == "adk_request_input":
                iid = fc.get("id") or fc.get("args", {}).get("interrupt_id", "")
                if iid:
                    calls[iid] = {"event": ev, "fc": fc}
    return calls


def _extract_function_responses(events: list[dict]) -> set[str]:
    """Return interrupt ids that already have a adk_request_input *response*."""
    responded: set[str] = set()
    for ev in events:
        for part in _iter_parts(ev):
            fr = part.get("functionResponse") or part.get("function_response")
            if fr and fr.get("name") == "adk_request_input":
                iid = fr.get("id") or ""
                if iid:
                    responded.add(iid)
    return responded


def _iter_parts(event: Any):
    """Yield all part dicts from an ADK event regardless of its structure."""
    # ADK events can be protobuf objects or plain dicts depending on SDK version.
    if hasattr(event, "content"):
        content = event.content
    elif isinstance(event, dict):
        content = event.get("content", {})
    else:
        return

    if hasattr(content, "parts"):
        parts = content.parts or []
    elif isinstance(content, dict):
        parts = content.get("parts", [])
    else:
        return

    for p in parts:
        yield p if isinstance(p, dict) else _proto_to_dict(p)


def _proto_to_dict(obj: Any) -> dict:
    """Best-effort proto → dict conversion."""
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
    return {}


def _parse_expense_from_fc(fc: dict) -> dict:
    """Pull the human-readable expense payload out of an adk_request_input call."""
    args = fc.get("args") or fc.get("arguments") or {}
    if isinstance(args, str):
        import json
        try:
            args = json.loads(args)
        except Exception:
            args = {"message": args}
    return args


# ── API endpoints ──────────────────────────────────────────────────────────────

class ActionRequest(BaseModel):
    interrupt_id: str
    approved: bool


@app.get("/api/pending", response_class=JSONResponse)
async def get_pending():
    """
    List all sessions that have an unresolved adk_request_input interrupt.
    Returns a list of:
      { session_id, interrupt_id, expense_payload }
    """
    svc = _get_service()

    try:
        sessions_resp = await svc.list_sessions(app_name=APP_NAME, user_id=USER_ID)
    except Exception as exc:
        log.exception("Failed to list sessions")
        raise HTTPException(status_code=502, detail=str(exc))

    sessions = getattr(sessions_resp, "sessions", sessions_resp) or []
    pending = []

    for sess in sessions:
        session_id = getattr(sess, "id", None) or (
            sess.get("id") if isinstance(sess, dict) else None
        )
        if not session_id:
            continue

        try:
            full_session = await svc.get_session(
                app_name=APP_NAME,
                user_id=USER_ID,
                session_id=session_id,
            )
        except Exception:
            log.warning("Could not fetch session %s", session_id)
            continue

        events = getattr(full_session, "events", None) or (
            full_session.get("events", []) if isinstance(full_session, dict) else []
        )
        events = list(events)

        calls = _extract_function_calls(events)
        responded = _extract_function_responses(events)
        unresolved = {iid: data for iid, data in calls.items() if iid not in responded}

        for iid, data in unresolved.items():
            expense_payload = _parse_expense_from_fc(data["fc"])
            pending.append(
                {
                    "session_id": session_id,
                    "interrupt_id": iid,
                    "expense_payload": expense_payload,
                }
            )

    return {"pending": pending}


@app.post("/api/action/{session_id}", response_class=JSONResponse)
async def post_action(session_id: str, body: ActionRequest):
    """
    Resume a paused session with an approve / reject decision.

    The resume message is passed as a raw dict to avoid ADK SDK duplicate-
    parameter errors that occur when keyword arguments are unpacked separately.
    """
    svc = _get_service()

    # Build the resume payload exactly as ADK expects it.
    resume_payload = {
        "role": "user",
        "parts": [
            {
                "functionResponse": {
                    "id": body.interrupt_id,
                    "name": "adk_request_input",
                    "response": {"approved": body.approved},
                }
            }
        ],
    }

    try:
        result = await svc.append_event(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id,
            event=resume_payload,
        )
    except AttributeError:
        # Older SDK: fall back to create_message / send_message style
        try:
            result = await svc.send_message(  # type: ignore[attr-defined]
                app_name=APP_NAME,
                user_id=USER_ID,
                session_id=session_id,
                message=resume_payload,
            )
        except Exception as exc:
            log.exception("send_message fallback failed for session %s", session_id)
            raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        log.exception("Failed to resume session %s", session_id)
        raise HTTPException(status_code=502, detail=str(exc))

    action_label = "APPROVED" if body.approved else "REJECTED"
    log.info("Session %s → %s (interrupt=%s)", session_id, action_label, body.interrupt_id)
    return {"status": "ok", "action": action_label, "session_id": session_id}


# ── Dashboard HTML ─────────────────────────────────────────────────────────────

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Expense Approval · Manager Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet"/>
<style>
  /* ── Reset & base ─────────────────────────────────────────────────── */
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:          #09090f;
    --surface:     rgba(255,255,255,0.045);
    --surface-h:   rgba(255,255,255,0.08);
    --border:      rgba(255,255,255,0.09);
    --border-h:    rgba(255,255,255,0.18);
    --text:        #e8e8f0;
    --muted:       #7d7d99;
    --accent:      #7c6dfa;
    --accent-glow: rgba(124,109,250,0.35);
    --green:       #22d3a0;
    --green-glow:  rgba(34,211,160,0.30);
    --red:         #f43f5e;
    --red-glow:    rgba(244,63,94,0.30);
    --amber:       #f59e0b;
    --radius:      18px;
    --radius-sm:   10px;
    --transition:  0.22s cubic-bezier(.4,0,.2,1);
  }

  html { scroll-behavior: smooth; }

  body {
    font-family: 'Outfit', system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    overflow-x: hidden;
  }

  /* ── Ambient glows ───────────────────────────────────────────────── */
  body::before, body::after {
    content: '';
    position: fixed;
    border-radius: 50%;
    pointer-events: none;
    z-index: 0;
    filter: blur(120px);
    opacity: 0.18;
  }
  body::before {
    width: 700px; height: 700px;
    top: -200px; left: -180px;
    background: radial-gradient(circle, #7c6dfa, transparent 70%);
  }
  body::after {
    width: 600px; height: 600px;
    bottom: -150px; right: -150px;
    background: radial-gradient(circle, #22d3a0, transparent 70%);
  }

  /* ── Layout ──────────────────────────────────────────────────────── */
  .wrapper {
    position: relative;
    z-index: 1;
    max-width: 1140px;
    margin: 0 auto;
    padding: 0 24px 80px;
  }

  /* ── Header ──────────────────────────────────────────────────────── */
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 32px 0 28px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 40px;
  }
  .logo {
    display: flex; align-items: center; gap: 12px;
  }
  .logo-icon {
    width: 42px; height: 42px;
    background: linear-gradient(135deg, #7c6dfa, #22d3a0);
    border-radius: 12px;
    display: flex; align-items: center; justify-content: center;
    font-size: 20px;
    box-shadow: 0 0 24px var(--accent-glow);
  }
  .logo-text h1 { font-size: 1.25rem; font-weight: 700; letter-spacing: -0.02em; }
  .logo-text p  { font-size: 0.78rem; color: var(--muted); margin-top: 1px; }

  .header-right { display: flex; align-items: center; gap: 12px; }

  .badge {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 5px 12px;
    border-radius: 100px;
    font-size: 0.78rem; font-weight: 500;
    border: 1px solid var(--border);
    background: var(--surface);
  }
  .badge-dot {
    width: 7px; height: 7px; border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 6px var(--green);
    animation: pulse 2s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; } 50% { opacity: 0.4; }
  }

  .btn-refresh {
    display: inline-flex; align-items: center; gap: 7px;
    padding: 8px 18px;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--surface);
    color: var(--text);
    font: 500 0.85rem 'Outfit', sans-serif;
    cursor: pointer;
    transition: background var(--transition), border-color var(--transition), transform var(--transition);
  }
  .btn-refresh:hover {
    background: var(--surface-h);
    border-color: var(--border-h);
    transform: translateY(-1px);
  }
  .btn-refresh.spinning svg { animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── Stats bar ───────────────────────────────────────────────────── */
  .stats {
    display: flex; gap: 16px;
    margin-bottom: 36px;
  }
  .stat-card {
    flex: 1;
    padding: 20px 24px;
    border-radius: var(--radius);
    border: 1px solid var(--border);
    background: var(--surface);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    transition: border-color var(--transition), box-shadow var(--transition);
  }
  .stat-card:hover { border-color: var(--border-h); box-shadow: 0 4px 32px rgba(0,0,0,0.25); }
  .stat-label { font-size: 0.75rem; font-weight: 500; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; }
  .stat-value { font-size: 2rem; font-weight: 700; letter-spacing: -0.03em; }
  .stat-value.accent  { color: var(--accent); }
  .stat-value.green   { color: var(--green); }
  .stat-value.red     { color: var(--red); }

  /* ── Section header ──────────────────────────────────────────────── */
  .section-title {
    font-size: 0.75rem; font-weight: 600; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.1em;
    margin-bottom: 20px;
  }

  /* ── Grid ────────────────────────────────────────────────────────── */
  #cards-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
    gap: 20px;
  }

  /* ── Expense card ────────────────────────────────────────────────── */
  .expense-card {
    border-radius: var(--radius);
    border: 1px solid var(--border);
    background: var(--surface);
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    padding: 24px;
    display: flex; flex-direction: column; gap: 16px;
    transition: transform var(--transition), border-color var(--transition), box-shadow var(--transition);
    position: relative;
    overflow: hidden;
  }
  .expense-card::before {
    content: '';
    position: absolute;
    inset: 0;
    background: radial-gradient(ellipse at top left, rgba(124,109,250,0.07), transparent 60%);
    pointer-events: none;
  }
  .expense-card:hover {
    transform: translateY(-3px);
    border-color: var(--border-h);
    box-shadow: 0 8px 48px rgba(0,0,0,0.3);
  }

  .card-top {
    display: flex; align-items: flex-start; justify-content: space-between; gap: 12px;
  }
  .card-amount {
    font-size: 2rem; font-weight: 700;
    letter-spacing: -0.04em;
    background: linear-gradient(135deg, #fff 20%, #a89ffc);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
  }
  .card-category {
    padding: 4px 10px;
    border-radius: 100px;
    font-size: 0.72rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
    background: rgba(124,109,250,0.15);
    color: #c4bbff;
    border: 1px solid rgba(124,109,250,0.3);
    white-space: nowrap;
  }

  .card-meta { display: flex; flex-direction: column; gap: 8px; }
  .meta-row {
    display: flex; align-items: center; gap: 8px;
    font-size: 0.84rem; color: var(--muted);
  }
  .meta-row svg { flex-shrink: 0; opacity: 0.6; }
  .meta-row span { color: var(--text); font-weight: 500; }

  .card-desc {
    font-size: 0.84rem; color: var(--muted);
    line-height: 1.55;
    padding: 10px 14px;
    border-radius: var(--radius-sm);
    background: rgba(255,255,255,0.03);
    border: 1px solid var(--border);
  }

  .card-session {
    font-size: 0.7rem; color: var(--muted);
    font-family: 'Courier New', monospace;
    opacity: 0.6;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }

  /* ── Action buttons ──────────────────────────────────────────────── */
  .card-actions { display: flex; gap: 10px; margin-top: 4px; }

  .btn {
    flex: 1;
    padding: 11px 0;
    border-radius: var(--radius-sm);
    font: 600 0.9rem 'Outfit', sans-serif;
    cursor: pointer;
    display: inline-flex; align-items: center; justify-content: center; gap: 8px;
    border: none;
    transition: transform var(--transition), box-shadow var(--transition), opacity var(--transition);
    position: relative;
    overflow: hidden;
  }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none !important; }

  .btn-approve {
    background: linear-gradient(135deg, #22d3a0, #16a07a);
    color: #001a12;
    box-shadow: 0 4px 20px rgba(34,211,160,0.25);
  }
  .btn-approve:hover:not(:disabled) {
    transform: translateY(-2px);
    box-shadow: 0 8px 32px rgba(34,211,160,0.4);
  }

  .btn-reject {
    background: linear-gradient(135deg, #f43f5e, #c2273e);
    color: #fff;
    box-shadow: 0 4px 20px rgba(244,63,94,0.25);
  }
  .btn-reject:hover:not(:disabled) {
    transform: translateY(-2px);
    box-shadow: 0 8px 32px rgba(244,63,94,0.4);
  }

  /* Spinner inside button */
  .spinner {
    width: 16px; height: 16px;
    border: 2px solid currentColor;
    border-top-color: transparent;
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    display: none;
  }
  .btn.loading .spinner { display: block; }
  .btn.loading .btn-label { display: none; }

  /* Decided state */
  .expense-card.decided {
    opacity: 0.4;
    pointer-events: none;
    filter: grayscale(0.6);
  }
  .expense-card.decided-approve { border-color: rgba(34,211,160,0.4); }
  .expense-card.decided-reject  { border-color: rgba(244,63,94,0.4); }

  /* ── Empty state ─────────────────────────────────────────────────── */
  .empty-state {
    grid-column: 1/-1;
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    gap: 14px;
    padding: 80px 20px;
    color: var(--muted);
    text-align: center;
  }
  .empty-state-icon { font-size: 3rem; opacity: 0.3; }
  .empty-state h3 { font-size: 1.1rem; font-weight: 600; }
  .empty-state p  { font-size: 0.85rem; max-width: 320px; line-height: 1.6; }

  /* ── Error banner ────────────────────────────────────────────────── */
  .error-banner {
    display: flex; align-items: center; gap: 12px;
    padding: 14px 20px;
    border-radius: var(--radius-sm);
    background: rgba(244,63,94,0.1);
    border: 1px solid rgba(244,63,94,0.3);
    color: #fca5b1;
    font-size: 0.85rem;
    margin-bottom: 24px;
    display: none;
  }

  /* ── Slide-out modal ──────────────────────────────────────────────── */
  .modal-overlay {
    position: fixed; inset: 0; z-index: 100;
    background: rgba(0,0,0,0.6);
    backdrop-filter: blur(6px);
    -webkit-backdrop-filter: blur(6px);
    opacity: 0; pointer-events: none;
    transition: opacity 0.3s ease;
    display: flex; justify-content: flex-end;
  }
  .modal-overlay.open { opacity: 1; pointer-events: auto; }

  .modal-panel {
    width: min(480px, 100%);
    height: 100%;
    background: #111118;
    border-left: 1px solid var(--border);
    padding: 36px 32px;
    display: flex; flex-direction: column; gap: 24px;
    overflow-y: auto;
    transform: translateX(100%);
    transition: transform 0.35s cubic-bezier(.4,0,.2,1);
  }
  .modal-overlay.open .modal-panel { transform: translateX(0); }

  .modal-close {
    position: absolute; top: 20px; right: 24px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--muted);
    font-size: 1.1rem;
    width: 36px; height: 36px;
    display: flex; align-items: center; justify-content: center;
    cursor: pointer;
    transition: background var(--transition), color var(--transition);
  }
  .modal-close:hover { background: var(--surface-h); color: var(--text); }

  .modal-status-badge {
    display: inline-flex; align-items: center; gap: 8px;
    padding: 7px 16px;
    border-radius: 100px;
    font-size: 0.85rem; font-weight: 600;
    align-self: flex-start;
  }
  .modal-status-badge.approved { background: rgba(34,211,160,0.15); color: var(--green); border: 1px solid rgba(34,211,160,0.3); }
  .modal-status-badge.rejected { background: rgba(244,63,94,0.15);  color: var(--red);   border: 1px solid rgba(244,63,94,0.3); }

  .modal-title { font-size: 1.5rem; font-weight: 700; letter-spacing: -0.025em; }
  .modal-section-label { font-size: 0.72rem; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 10px; }

  .modal-detail-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
  }
  .modal-detail-cell {
    padding: 14px;
    border-radius: var(--radius-sm);
    background: var(--surface);
    border: 1px solid var(--border);
  }
  .modal-detail-cell .label { font-size: 0.7rem; color: var(--muted); margin-bottom: 4px; }
  .modal-detail-cell .value { font-size: 0.95rem; font-weight: 600; }

  .modal-review-box {
    padding: 16px;
    border-radius: var(--radius-sm);
    background: var(--surface);
    border: 1px solid var(--border);
    font-size: 0.84rem; line-height: 1.6; color: var(--muted);
  }

  /* ── Responsive ──────────────────────────────────────────────────── */
  @media (max-width: 600px) {
    .stats { flex-direction: column; }
    header { flex-direction: column; align-items: flex-start; gap: 16px; }
  }
</style>
</head>
<body>
<div class="wrapper">

  <!-- Header -->
  <header>
    <div class="logo">
      <div class="logo-icon">💼</div>
      <div class="logo-text">
        <h1>Expense Approvals</h1>
        <p>Manager Review Dashboard</p>
      </div>
    </div>
    <div class="header-right">
      <div class="badge"><div class="badge-dot"></div>Live</div>
      <button class="btn-refresh" id="btn-refresh" onclick="loadPending()">
        <svg id="refresh-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>
          <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
        </svg>
        Refresh
      </button>
    </div>
  </header>

  <!-- Stats -->
  <div class="stats">
    <div class="stat-card">
      <div class="stat-label">Pending Review</div>
      <div class="stat-value accent" id="stat-pending">—</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Approved This Session</div>
      <div class="stat-value green" id="stat-approved">0</div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Rejected This Session</div>
      <div class="stat-value red" id="stat-rejected">0</div>
    </div>
  </div>

  <!-- Error banner -->
  <div class="error-banner" id="error-banner">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
    <span id="error-text"></span>
  </div>

  <!-- Cards -->
  <div class="section-title">Awaiting Decision</div>
  <div id="cards-grid"></div>
</div>

<!-- Slide-out result modal -->
<div class="modal-overlay" id="result-modal" onclick="closeModalOnOverlay(event)">
  <div class="modal-panel">
    <button class="modal-close" onclick="closeModal()">✕</button>
    <div class="modal-status-badge" id="modal-badge"></div>
    <div class="modal-title" id="modal-title"></div>

    <div>
      <div class="modal-section-label">Expense Details</div>
      <div class="modal-detail-grid" id="modal-details"></div>
    </div>

    <div>
      <div class="modal-section-label">Compliance Review</div>
      <div class="modal-review-box" id="modal-review"></div>
    </div>

    <div>
      <div class="modal-section-label">Session</div>
      <div class="modal-review-box" id="modal-session" style="font-family:monospace;font-size:0.75rem;word-break:break-all;"></div>
    </div>
  </div>
</div>

<script>
  const sessionApproved = 0, sessionRejected = 0;
  let _approved = 0, _rejected = 0;

  async function loadPending() {
    const btn = document.getElementById('btn-refresh');
    const icon = document.getElementById('refresh-icon');
    btn.classList.add('spinning');
    btn.disabled = true;
    hideError();

    try {
      const res = await fetch('/api/pending');
      if (!res.ok) throw new Error(`Server returned ${res.status}: ${await res.text()}`);
      const data = await res.json();
      renderCards(data.pending || []);
      document.getElementById('stat-pending').textContent = (data.pending || []).length;
    } catch(e) {
      showError(e.message);
    } finally {
      btn.classList.remove('spinning');
      btn.disabled = false;
    }
  }

  function renderCards(items) {
    const grid = document.getElementById('cards-grid');
    grid.innerHTML = '';

    if (!items.length) {
      grid.innerHTML = `
        <div class="empty-state">
          <div class="empty-state-icon">✅</div>
          <h3>All caught up!</h3>
          <p>There are no expense submissions awaiting your review right now. Check back later or hit Refresh.</p>
        </div>`;
      return;
    }

    items.forEach(item => {
      const ep = item.expense_payload || {};
      const amount   = ep.amount   ?? ep.message ?? '—';
      const submitter = ep.submitter ?? '—';
      const category  = ep.category  ?? '—';
      const desc      = ep.description ?? ep.message ?? '—';
      const date      = ep.date       ?? '—';

      const fmtAmount = typeof amount === 'number'
        ? '$' + amount.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})
        : String(amount);

      const card = document.createElement('div');
      card.className = 'expense-card';
      card.id = `card-${item.session_id}`;
      card.innerHTML = `
        <div class="card-top">
          <div class="card-amount">${escHtml(fmtAmount)}</div>
          <div class="card-category">${escHtml(category)}</div>
        </div>
        <div class="card-meta">
          <div class="meta-row">
            ${iconUser()}
            <span>${escHtml(submitter)}</span>
          </div>
          <div class="meta-row">
            ${iconCal()}
            <span>${escHtml(date)}</span>
          </div>
        </div>
        <div class="card-desc">${escHtml(desc)}</div>
        <div class="card-session">Session: ${escHtml(item.session_id)}</div>
        <div class="card-actions">
          <button class="btn btn-approve" id="approve-${item.session_id}"
            onclick="doAction('${escAttr(item.session_id)}','${escAttr(item.interrupt_id)}',true,this,
              ${JSON.stringify({fmtAmount, submitter, category, desc, date, session_id: item.session_id})})">
            <span class="spinner"></span>
            <span class="btn-label">✓ Approve</span>
          </button>
          <button class="btn btn-reject" id="reject-${item.session_id}"
            onclick="doAction('${escAttr(item.session_id)}','${escAttr(item.interrupt_id)}',false,this,
              ${JSON.stringify({fmtAmount, submitter, category, desc, date, session_id: item.session_id})})">
            <span class="spinner"></span>
            <span class="btn-label">✕ Reject</span>
          </button>
        </div>
      `;
      grid.appendChild(card);
    });
  }

  async function doAction(sessionId, interruptId, approved, btn, meta) {
    const card = document.getElementById(`card-${sessionId}`);
    const siblingId = approved ? `reject-${sessionId}` : `approve-${sessionId}`;
    const sibling = document.getElementById(siblingId);

    btn.classList.add('loading');
    btn.disabled = true;
    if (sibling) sibling.disabled = true;

    try {
      const res = await fetch(`/api/action/${encodeURIComponent(sessionId)}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({interrupt_id: interruptId, approved})
      });
      if (!res.ok) throw new Error(`Server returned ${res.status}: ${await res.text()}`);
      const data = await res.json();

      // Mark card decided
      card.classList.add('decided', approved ? 'decided-approve' : 'decided-reject');

      // Update stats
      if (approved) { _approved++; document.getElementById('stat-approved').textContent = _approved; }
      else          { _rejected++; document.getElementById('stat-rejected').textContent = _rejected; }

      // Show result modal
      openModal(approved, meta, data);

    } catch(e) {
      showError(e.message);
      btn.classList.remove('loading');
      btn.disabled = false;
      if (sibling) sibling.disabled = false;
    }
  }

  function openModal(approved, meta, serverResp) {
    const overlay = document.getElementById('result-modal');
    const badge   = document.getElementById('modal-badge');
    const title   = document.getElementById('modal-title');
    const details = document.getElementById('modal-details');
    const review  = document.getElementById('modal-review');
    const sessEl  = document.getElementById('modal-session');

    badge.className = `modal-status-badge ${approved ? 'approved' : 'rejected'}`;
    badge.textContent = approved ? '✓ Approved' : '✕ Rejected';

    title.textContent = `${meta.fmtAmount} · ${meta.submitter}`;

    details.innerHTML = `
      <div class="modal-detail-cell"><div class="label">Amount</div><div class="value">${escHtml(meta.fmtAmount)}</div></div>
      <div class="modal-detail-cell"><div class="label">Category</div><div class="value">${escHtml(meta.category)}</div></div>
      <div class="modal-detail-cell"><div class="label">Submitted by</div><div class="value">${escHtml(meta.submitter)}</div></div>
      <div class="modal-detail-cell"><div class="label">Date</div><div class="value">${escHtml(meta.date)}</div></div>
    `;

    review.textContent = meta.desc || 'No additional compliance notes.';
    sessEl.textContent = meta.session_id;

    overlay.classList.add('open');
  }

  function closeModal() { document.getElementById('result-modal').classList.remove('open'); }
  function closeModalOnOverlay(e) { if (e.target === e.currentTarget) closeModal(); }

  function showError(msg) {
    const banner = document.getElementById('error-banner');
    document.getElementById('error-text').textContent = msg;
    banner.style.display = 'flex';
  }
  function hideError() { document.getElementById('error-banner').style.display = 'none'; }

  function escHtml(s) {
    return String(s ?? '')
      .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
      .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
  }
  function escAttr(s) { return String(s ?? '').replace(/'/g, "\\'"); }

  function iconUser() {
    return `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`;
  }
  function iconCal() {
    return `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>`;
  }

  // Auto-load on page open
  loadPending();

  // Poll every 30 s
  setInterval(loadPending, 30000);
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """Serve the manager approval dashboard."""
    return HTMLResponse(content=DASHBOARD_HTML)


# ── Entrypoint ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
