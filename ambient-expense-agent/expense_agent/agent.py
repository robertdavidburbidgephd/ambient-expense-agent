from typing import Any
from google.adk.workflow import Workflow
from google.adk.agents import LlmAgent
from .schemas import ExpenseEvent, ApprovalOutcome, RiskAnalysis
from .nodes import extract_expense, human_approval, auto_approve, record_outcome, security_checkpoint
from .config import LLM_MODEL

## >>
import os
import google.auth

_, project_id = google.auth.default()  # from SETUP.sh
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
## <<

llm_reviewer = LlmAgent(
    name="llm_reviewer",
    model=LLM_MODEL,
    instruction="Review the following expense for risk factors (e.g. unusual amounts, non-compliant categories like alcohol or entertainment). Explain your reasoning and determine if it is high risk.\n\nExpense details: {expense}",
    include_contents='none',
    output_schema=RiskAnalysis,
    output_key="risk_analysis",
)

root_agent = Workflow(
    name="ambient_expense_agent",
    description="Agent for reviewing and approving expenses based on value and LLM risk assessment.",
    input_schema=Any,
    output_schema=ApprovalOutcome,
    edges=[
        # extraction & routing
        ("START", extract_expense),
        (extract_expense, {
            "auto_approve": auto_approve,
            "security_checkpoint": security_checkpoint
        }),
        
        # Security routing
        (security_checkpoint, {
            "llm_review": llm_reviewer,
            "human_approval": human_approval
        }),
        
        # LLM review path -> Human Approval -> Record Outcome
        (llm_reviewer, human_approval),
        (human_approval, record_outcome),
        
        # Auto-approve path -> Record Outcome
        (auto_approve, record_outcome)
    ]
)
