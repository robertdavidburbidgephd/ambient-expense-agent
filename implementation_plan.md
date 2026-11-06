# Ambient Expense Agent Implementation Plan

This plan outlines the steps to build the ambient expense-approval agent using the ADK 2.0 Graph Workflow API.

## User Review Required

> [!IMPORTANT]
> The default scaffold created an `app/` directory. I will rename it to `expense_agent/` and update the relevant configuration files (`pyproject.toml` and `agents-cli-manifest.yaml`) to ensure the `agents-cli` continues to work correctly.

## Proposed Changes

### Configuration Updates

#### [MODIFY] [agents-cli-manifest.yaml](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/agents-cli-manifest.yaml)
Update `agent_directory` from `"app"` to `"expense_agent"`.

#### [MODIFY] [pyproject.toml](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/pyproject.toml)
Update the `packages` list under `[tool.hatch.build.targets.wheel]` to point to `["expense_agent"]` and update the `isort` configuration.

### Agent Implementation (`expense_agent/`)

#### [NEW] [expense_agent/config.py](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/expense_agent/config.py)
Define configuration constants:
- `AUTO_APPROVE_THRESHOLD = 100.0`
- `LLM_MODEL = "gemini-3.1-flash-lite"`

#### [NEW] [expense_agent/schemas.py](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/expense_agent/schemas.py)
Define the Pydantic models:
- `ExpenseEvent`: Models the incoming Pub/Sub style event (`data` field).
- `Expense`: Represents the parsed expense (amount, submitter, category, description, date).
- `RiskAnalysis`: The structured output from the LLM agent regarding risk factors.
- `ApprovalOutcome`: The final recorded outcome.

#### [NEW] [expense_agent/nodes.py](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/expense_agent/nodes.py)
Implement the workflow nodes:
1. `extract_expense`: Parses the incoming JSON event, decodes `data` (if base64), extracts the `Expense`. Routes to `auto_approve` if `< 100.0`, else routes to `llm_review`.
2. `llm_reviewer`: An `LlmAgent` using `gemini-3.1-flash-lite` that outputs a `RiskAnalysis` detailing if the expense is high risk.
3. `human_approval`: Checks if the LLM flagged it as risky or simply pauses using `RequestInput(interrupt_id="human_decision")`. Upon resume, it yields the decision.
4. `auto_approve` & `record_outcome`: Terminal nodes that log and return the final `ApprovalOutcome`.

#### [MODIFY] [expense_agent/agent.py](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/expense_agent/agent.py)
Wire up the `Workflow` graph with the defined nodes:
- `edges = [...]` connecting `START` -> `extract_expense` -> routing to `auto_approve` or `llm_reviewer` -> `human_approval` -> `record_outcome`.
- Expose the workflow instance.

#### [MODIFY] [expense_agent/__init__.py](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/expense_agent/__init__.py)
Update the `App` definition to use the new workflow and the correct `expense_agent` name.

## Verification Plan

### Automated Tests
- None strictly required for this prototyping phase, but I will use the `agents-cli run` command to test the workflow locally with sample JSON inputs (both under and over $100).

### Manual Verification
- We will execute the workflow with a `$50` expense to confirm the `auto_approve` fast path.
- We will execute the workflow with a `$150` expense to confirm the LLM review and the `RequestInput` human-in-the-loop pause.
