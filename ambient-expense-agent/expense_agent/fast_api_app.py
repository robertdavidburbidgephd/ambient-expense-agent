# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import base64
import json
import logging
import os
import uuid

import google.auth
from fastapi import FastAPI, HTTPException
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from expense_agent.agent import root_agent
from expense_agent.app_utils.telemetry import setup_telemetry
from expense_agent.app_utils.typing import Feedback

# Setup standard Python logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

setup_telemetry()
_, project_id = google.auth.default()
allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)

# Artifact bucket for ADK (created by Terraform, passed via env var)
logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# In-memory session configuration - no persistent storage
session_service_uri = None

artifact_service_uri = f"gs://{logs_bucket_name}" if logs_bucket_name else None

app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=False,
    artifact_service_uri=artifact_service_uri,
    allow_origins=allow_origins,
    session_service_uri=session_service_uri,
    otel_to_cloud=False,
)
app.title = "ambient-expense-agent"
app.description = "API for interacting with the Agent ambient-expense-agent"

# Setup Runner and Session Service for the workflow
session_service = InMemorySessionService()
runner = Runner(agent=root_agent, session_service=session_service, app_name="ambient-expense-agent")

# Models for Pub/Sub push subscription messages
class PubSubMessage(BaseModel):
    data: str
    attributes: dict[str, str] | None = None
    messageId: str | None = None
    publishTime: str | None = None

class PubSubRequest(BaseModel):
    message: PubSubMessage
    subscription: str | None = None


@app.post("/")
@app.post("/pubsub")
async def trigger_pubsub(req: PubSubRequest):
    # Normalize subscription path down to the short name
    subscription = req.subscription or "pubsub-caller"
    short_subscription = subscription.split("/")[-1] if "/" in subscription else subscription

    try:
        decoded_data = base64.b64decode(req.message.data).decode("utf-8")
        logger.info(f"Received Pub/Sub message from subscription: {short_subscription}")
        logger.info(f"Decoded message data: {decoded_data}")
    except Exception as e:
        logger.error(f"Failed to decode base64 message data: {e}")
        raise HTTPException(status_code=400, detail="Failed to decode base64 message data")

    session_id = str(uuid.uuid4())

    session = await session_service.get_session(
        app_name="ambient-expense-agent",
        user_id=short_subscription,
        session_id=session_id
    )
    if not session:
        session = await session_service.create_session(
            app_name="ambient-expense-agent",
            user_id=short_subscription,
            session_id=session_id
        )

    try:
        try:
            parsed_json = json.loads(decoded_data)
            input_text = json.dumps({"data": parsed_json})
        except json.JSONDecodeError:
            input_text = json.dumps({"data": decoded_data})

        new_message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=input_text)]
        )

        events = []
        async for event in runner.run_async(
            user_id=short_subscription,
            session_id=session.id,
            new_message=new_message,
        ):
            events.append(event)

        logger.info(f"Workflow finished successfully. Emitted {len(events)} events.")

    except Exception as e:
        logger.error(f"Workflow execution failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Workflow execution failed: {str(e)}")

    return {"status": "success"}


@app.post("/feedback")
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    """Collect and log feedback.

    Args:
        feedback: The feedback data to log

    Returns:
        Success message
    """
    logger.info(f"Feedback received: {feedback.model_dump()}")
    return {"status": "success"}


# Main execution
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8081)
