import os
import sys
import json
import logging
from fastapi import Request
from google.adk.cli.fast_api import get_fast_api_app

# Disable ADK cloud telemetry as per checklist
os.environ["ADK_OTEL_TO_CLOUD"] = "False"

# Configure standard Python logging for console logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout
)

logger = logging.getLogger(__name__)

# Initialize the ADK FastAPI App with pubsub trigger enabled
app = get_fast_api_app(
    agents_dir="expense_agent",
    web=False,
    trigger_sources=["pubsub"],
    otel_to_cloud=False
)

async def set_body(request: Request, body: bytes):
    """Helper to rewrite the body of a FastAPI Request."""
    async def receive():
        return {"type": "http.request", "body": body}
    request._receive = receive

@app.middleware("http")
async def normalize_pubsub_subscription(request: Request, call_next):
    """
    Middleware to intercept Pub/Sub triggers and normalize the fully-qualified 
    subscription path down to a short name to keep session records readable.
    """
    if request.url.path.endswith("/trigger/pubsub") and request.method == "POST":
        body = await request.body()
        try:
            data = json.loads(body)
            sub = data.get("subscription")
            if sub and "/" in sub:
                short_name = sub.split("/")[-1]
                logger.info(f"Normalizing Pub/Sub subscription '{sub}' -> '{short_name}'")
                data["subscription"] = short_name
                
                # Update request body
                new_body = json.dumps(data).encode("utf-8")
                await set_body(request, new_body)
        except Exception as e:
            logger.warning(f"Failed to parse Pub/Sub payload for normalization: {e}")
            # Reset body so the endpoint can still consume it
            await set_body(request, body)
            
    response = await call_next(request)
    return response
