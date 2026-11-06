# Ambient Event-Driven Expense Agent Service

Stand up the agent as an event-driven (ambient) FastAPI service that accepts Cloud Pub/Sub push subscription messages, runs the expense review workflow, and serves on port 8081.

## User Review Required

> [!IMPORTANT]
> The template codebase contains incorrect package imports from `app.*` instead of `expense_agent.*` (e.g. `from app.agent import root_agent` in tests, and `COPY ./app ./app` in the `Dockerfile`), which causes tests and builds to fail with `ModuleNotFoundError`. We will refactor these imports to `expense_agent` to ensure correctness and compatibility with `pyproject.toml`.

## Open Questions

There are no major open questions, but we will add a `run` target to the `Makefile` to make starting the local web service on port 8081 straightforward.

## Proposed Changes

### Configuration & Package Dependencies

---

#### [MODIFY] [pyproject.toml](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/pyproject.toml)
- Explicitly add `fastapi` to `project.dependencies` list.

#### [MODIFY] [Makefile](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/Makefile)
- Add a `run` target: `uv run uvicorn expense_agent.fast_api_app:app --host 0.0.0.0 --port 8081` to run the FastAPI app locally.

### Application Logic

---

#### [MODIFY] [fast_api_app.py](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/expense_agent/fast_api_app.py)
- Set `web=False` (no UI since it is ambient/event-driven) and `otel_to_cloud=False` inside `get_fast_api_app`.
- Use standard Python `logging` for console output instead of `google-cloud-logging`.
- Modify imports from `app.app_utils` to `expense_agent.app_utils`.
- Define a custom POST endpoint (`/` and `/pubsub`) accepting Cloud Pub/Sub push messages.
- Decodes the base64-encoded `message.data` and normalizes the `subscription` path parameter down to the short subscription name to use as `user_id` (session owner).
- Feeds the event payload into the ADK workflow runner and logs status.
- Update default port to `8081` in the main execution block.

#### [MODIFY] [Dockerfile](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/Dockerfile)
- Rename `./app` copying to `./expense_agent` and change the CMD execution module path to `expense_agent.fast_api_app:app`.
- Expose port `8081` instead of `8080`.

### Testing

---

#### [MODIFY] [test_agent.py](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/tests/integration/test_agent.py)
- Fix the module import path from `app.agent` to `expense_agent.agent`.

#### [MODIFY] [test_server_e2e.py](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/tests/integration/test_server_e2e.py)
- Update uvicorn runner command to run `expense_agent.fast_api_app:app` on port 8081.
- Update `BASE_URL` to point to port 8081.
- Rewrite `test_chat_stream` to send a Pub/Sub push request to the new endpoint and assert that it runs successfully (200 status code).

## Verification Plan

### Automated Tests
- Run `uv run pytest tests/unit tests/integration` to verify that both the workflow execution and the FastAPI server endpoints function properly and pass all integration checks.

### Manual Verification
- Run `make run` to verify that the FastAPI server starts up and serves on port 8081.
- Trigger a mock Pub/Sub POST request using `curl` to ensure that it correctly parses, normalizes subscription name, and executes the workflow.
