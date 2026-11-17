import base64
import json
from typing import Any, Optional
from pydantic import BaseModel, Field

class ExpenseEvent(BaseModel):
    data: str | dict[str, Any]

    def get_parsed_data(self) -> dict[str, Any]:
        if isinstance(self.data, dict):
            if "data" in self.data:
                return ExpenseEvent(data=self.data["data"]).get_parsed_data()
            return self.data
        try:
            # Try to base64 decode if it's a string
            decoded = base64.b64decode(self.data).decode("utf-8")
            parsed = json.loads(decoded)
            if isinstance(parsed, dict):
                if "data" in parsed:
                    return ExpenseEvent(data=parsed["data"]).get_parsed_data()
                return parsed
        except Exception:
            pass
        try:
            # Or try direct JSON parse
            parsed = json.loads(self.data)
            if isinstance(parsed, dict):
                if "data" in parsed:
                    return ExpenseEvent(data=parsed["data"]).get_parsed_data()
                return parsed
        except Exception:
            pass
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
