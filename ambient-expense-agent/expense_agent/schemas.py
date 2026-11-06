import base64
import json
from typing import Any, Optional
from pydantic import BaseModel, Field

class ExpenseEvent(BaseModel):
    data: str | dict[str, Any]

    def get_parsed_data(self) -> dict[str, Any]:
        if isinstance(self.data, dict):
            return self.data
        try:
            # Try to base64 decode if it's a string
            decoded = base64.b64decode(self.data).decode("utf-8")
            return json.loads(decoded)
        except Exception:
            try:
                # Or try direct JSON parse
                return json.loads(self.data)
            except Exception:
                raise ValueError("Could not parse event data as JSON")

class Expense(BaseModel):
    amount: float
    submitter: str
    category: str
    description: str
    date: str

class RiskAnalysis(BaseModel):
    is_high_risk: bool = Field(description="Whether the expense is considered high risk.")
    reasoning: str = Field(description="Explanation of the risk factors or why it's safe.")

class ApprovalOutcome(BaseModel):
    expense: Expense
    status: str
    reason: Optional[str] = None
    security_flags: Optional[list[str]] = None
