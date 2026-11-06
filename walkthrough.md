# Ambient Expense Agent Walkthrough

The ADK 2.0 graph workflow has been fully implemented. Here is how the wiring works step-by-step:

## 1. Defining the Core Structures

We created Pydantic models in [schemas.py](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/expense_agent/schemas.py) for data validation. `ExpenseEvent` handles parsing the JSON or base64 input, and `RiskAnalysis` forces the LLM to output a structured judgment.

We also placed the constants (`AUTO_APPROVE_THRESHOLD = 100.0` and `LLM_MODEL = "gemini-3.1-flash-lite"`) in [config.py](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/expense_agent/config.py) so they can be tweaked without touching graph logic.

## 2. Wiring the Nodes

The nodes are implemented in [nodes.py](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/expense_agent/nodes.py).

### The Extractor
The workflow starts here. It parses the incoming event, extracts the `Expense`, and makes the routing decision purely in Python. Note how it captures the expense in the context `state` so it can be retrieved later by the human approval node.

```python
@node
def extract_expense(node_input: ExpenseEvent) -> Event:
    data = node_input.get_parsed_data()
    expense = Expense.model_validate(data)
    
    if expense.amount < AUTO_APPROVE_THRESHOLD:
        return Event(output=expense, route="auto_approve", state={"expense": expense.model_dump()})
    else:
        return Event(output=expense, route="llm_review", state={"expense": expense.model_dump()})
```

### Human In The Loop (HITL)
If the route is `llm_review`, the LLM agent analyzes the expense and outputs a `RiskAnalysis`. Then the flow reaches `human_approval`. It checks if `ctx.resume_inputs` exists. If not, it pauses execution and asks for a decision via `RequestInput`. Once the human replies, the graph resumes and evaluates the decision.

```python
@node
async def human_approval(ctx: Context, node_input: RiskAnalysis):
    # Retrieve the original expense from state
    expense_data = ctx.state.get("expense", {})
    expense = Expense.model_validate(expense_data)
    
    # Pause if no human input has been provided yet
    if not ctx.resume_inputs:
        yield RequestInput(
            interrupt_id="human_decision",
            message=f"Expense of ${expense.amount} by {expense.submitter} for '{expense.description}'. Risk: {node_input.reasoning}. Approve or Reject?"
        )
        return
        
    # Process the human's decision upon resumption
    decision = ctx.resume_inputs.get("human_decision", "").lower()
    status = "APPROVED" if "approve" in decision else "REJECTED"
    
    # Yield the final outcome
    yield Event(output=ApprovalOutcome(...))
```

## 3. The Graph Definition

In [agent.py](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/expense_agent/agent.py), the `Workflow` ties all these nodes together. We use a dictionary format `{"route_name": next_node}` to conditionally fork paths based on the `extract_expense` routing output.

```python
    edges=[
        # extraction & conditional routing
        ("START", extract_expense),
        (extract_expense, {
            "auto_approve": auto_approve,
            "llm_review": llm_reviewer
        }),
        
        # High value -> LLM review -> Human Approval -> Record Outcome
        (llm_reviewer, human_approval),
        (human_approval, record_outcome),
        
        # Low value (Auto-approve) -> Record Outcome
        (auto_approve, record_outcome)
    ]
```

## Testing Locally
To test the flow without deploying, you can pass a JSON event directly using the CLI:

**Low Amount (Auto-approves):**
```bash
agents-cli run '{"data": "{\"amount\": 50, \"submitter\": \"Alice\", \"category\": \"Meals\", \"description\": \"Lunch\", \"date\": \"2023-10-01\"}"}'
```

**High Amount (Triggers LLM & pauses):**
```bash
agents-cli run '{"data": "{\"amount\": 150, \"submitter\": \"Bob\", \"category\": \"Electronics\", \"description\": \"Keyboard\", \"date\": \"2023-10-01\"}"}'
```
*(When it pauses, the CLI will prompt you for input to resolve the `RequestInput`)*
