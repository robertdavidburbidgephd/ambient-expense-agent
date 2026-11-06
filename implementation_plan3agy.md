# Ambient Agent Web Service Implementation Plan

This plan details how to stand up the ambient expense agent as a local FastAPI web service that listens for Pub/Sub triggers on port 8081, including normalizing the fully-qualified subscription path.

## User Review Required

> [!IMPORTANT]  
> The plan proposes using FastAPI middleware to intercept incoming Pub/Sub trigger payloads. It will parse the JSON body, rewrite the fully-qualified `subscription` field to its short name (e.g., extracting "my-subscription" from "projects/.../subscriptions/my-subscription"), and forward it to the ADK `trigger/pubsub` handler. This keeps session IDs readable as requested. 

## Proposed Changes

### [MODIFY] [pyproject.toml](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/pyproject.toml)
- Ensure `uvicorn` is explicitly listed in dependencies (since we are creating a standalone web service). `fastapi` is already present.

### [NEW] [fast_api_app.py](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/fast_api_app.py)
Create the main entrypoint for the web service.
1. **App Initialization**: Use `get_fast_api_app(agents_dir="expense_agent", web=False, trigger_sources=["pubsub"], otel_to_cloud=False)`.
2. **Logging**: Configure standard Python logging to stdout instead of cloud logging.
3. **Pub/Sub Normalization Middleware**: 
   - Add a FastAPI `@app.middleware("http")` to intercept POST requests to `/apps/expense_agent/trigger/pubsub`.
   - Read the raw JSON payload, parse it, and modify the `subscription` key to split by `/` and keep only the final segment.
   - Re-serialize and inject the modified body back into the request stream before passing it to the ADK handler.

### [MODIFY] [Makefile](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/Makefile)
Add a new target to run the ambient server:
```makefile
serve:
	uv run uvicorn fast_api_app:app --host 0.0.0.0 --port 8081
```

## Verification Plan

### Manual Verification
- Start the web service using `make serve`.
- Send a mock Pub/Sub JSON payload (with a fully-qualified `subscription` path and base64 encoded expense data) to `http://127.0.0.1:8081/apps/expense_agent/trigger/pubsub` using `curl`.
- Verify the console logs indicate the session ID matches the shortened subscription name, and the event was successfully processed by the workflow.
