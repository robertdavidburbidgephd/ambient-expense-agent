import re
from typing import Any
from google.adk.workflow import node
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from .schemas import ExpenseEvent, Expense, RiskAnalysis, ApprovalOutcome
from .config import AUTO_APPROVE_THRESHOLD

@node
def extract_expense(node_input: Any) -> Event:
    if hasattr(node_input, "parts"):
        # Passed as types.Content
        text_data = node_input.parts[0].text
        expense_event = ExpenseEvent(data=text_data)
    else:
        # Standard dictionary/ExpenseEvent casting
        # If it's a dict representing Expense, wrap it in 'data'
        if isinstance(node_input, dict) and "amount" in node_input:
            expense_event = ExpenseEvent(data=node_input)
        else:
            expense_event = ExpenseEvent.model_validate(node_input)
        
    data = expense_event.get_parsed_data()
    expense = Expense.model_validate(data)
    
    if expense.amount < AUTO_APPROVE_THRESHOLD:
        return Event(output=expense, route="auto_approve", state={"expense": expense.model_dump()})
    else:
        return Event(output=expense, route="security_checkpoint", state={"expense": expense.model_dump()})

@node
def security_checkpoint(ctx: Context, node_input: Expense) -> Event:
    expense = node_input.model_copy()
    redactions = []
    
    # 1. Scrub PII
    ssn_pattern = r'\b\d{3}-\d{2}-\d{4}\b'
    cc_pattern = r'\b\d{4}-\d{4}-\d{4}-\d{4}\b'
    
    if re.search(ssn_pattern, expense.description):
        expense.description = re.sub(ssn_pattern, '[REDACTED SSN]', expense.description)
        redactions.append("SSN")
        
    if re.search(cc_pattern, expense.description):
        expense.description = re.sub(cc_pattern, '[REDACTED CC]', expense.description)
        redactions.append("Credit Card")
        
    ctx.state["redactions"] = redactions
    ctx.state["expense"] = expense.model_dump()
    
    # 2. Defend against prompt injection
    lower_desc = expense.description.lower()
    injection_keywords = ["ignore", "bypass", "auto-approve", "system prompt", "instruction"]
    if any(keyword in lower_desc for keyword in injection_keywords):
        # Flag as security event and route directly to human
        ctx.state["security_flags"] = ["Prompt Injection Detected"]
        risk = RiskAnalysis(
            is_high_risk=True,
            reasoning="Security Event: Prompt Injection Detected. Automated review bypassed."
        )
        return Event(output=risk, route="human_approval")
        
    # Clean, proceed to LLM review
    return Event(output=expense, route="llm_review")

@node
async def human_approval(ctx: Context, node_input: RiskAnalysis):
    # node_input is the RiskAnalysis from the LLM or security checkpoint
    expense_data = ctx.state.get("expense", {})
    expense = Expense.model_validate(expense_data)
    security_flags = ctx.state.get("security_flags", [])
    redactions = ctx.state.get("redactions", [])
    
    if not ctx.resume_inputs:
        msg = f"Expense of ${expense.amount} by {expense.submitter} for '{expense.description}'."
        if redactions:
            msg += f"\n[!] Redacted data types: {', '.join(redactions)}"
        if security_flags:
            msg += f"\n[SECURITY ALERT]: {', '.join(security_flags)}"
        msg += f"\nRisk: {node_input.reasoning}. Approve or Reject?"
        
        yield RequestInput(
            interrupt_id="human_decision",
            message=msg
        )
        return
        
    decision = ctx.resume_inputs.get("human_decision", "").lower()
    status = "APPROVED" if "approve" in decision else "REJECTED"
    
    outcome = ApprovalOutcome(
        expense=expense,
        status=status,
        reason=node_input.reasoning,
        security_flags=security_flags if security_flags else None
    )
    yield Event(output=outcome)

@node
def auto_approve(node_input: Expense) -> ApprovalOutcome:
    return ApprovalOutcome(
        expense=node_input,
        status="APPROVED",
        reason="Amount under threshold."
    )

@node
def record_outcome(node_input: ApprovalOutcome) -> ApprovalOutcome:
    # In a real system, you would save this to a database
    print(f"Outcome recorded: {node_input.model_dump_json(indent=2)}")
    return node_input
