# Ambient Web Service Walkthrough

The expense agent has been transitioned from a conversational interface to a robust, event-driven web service. It now operates ambiently, responding to Pub/Sub triggers.

## 1. The FastAPI Application

We created a custom [fast_api_app.py](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/fast_api_app.py) that acts as the primary web server entrypoint.

Instead of running an interactive session, it initializes the core `get_fast_api_app` from the ADK framework and configures it for background processing:

```python
# Initialize the ADK FastAPI App with pubsub trigger enabled
app = get_fast_api_app(
    agents_dir="expense_agent",
    web=False,
    trigger_sources=["pubsub"],
    otel_to_cloud=False
)
```
*Notice we explicitly disabled `otel_to_cloud` as per your checklist, and standard Python logging has been wired up to stdout.*

## 2. Normalizing Pub/Sub Subscriptions

When Google Cloud Pub/Sub sends a push notification, the `subscription` parameter contains a long string like `projects/my-project/subscriptions/expense-topic-sub`. By default, ADK uses this string as the session ID, which makes session logs hard to read.

To fix this, we added an HTTP middleware that intercepts requests heading to the `/apps/expense_agent/trigger/pubsub` endpoint. It parses the JSON payload, extracts the subscription name, and splits it:

```python
@app.middleware("http")
async def normalize_pubsub_subscription(request: Request, call_next):
    if request.url.path.endswith("/trigger/pubsub") and request.method == "POST":
        body = await request.body()
        data = json.loads(body)
        sub = data.get("subscription")
        
        if sub and "/" in sub:
            short_name = sub.split("/")[-1]
            data["subscription"] = short_name
            # Re-inject the modified JSON into the request stream
            ...
```

Now, the session ID will simply be `expense-topic-sub`.

## 3. Makefile Targets

The [Makefile](file:///home/robert/work/agents/agents-cli-projs/day4_1/ambient-expense-agent/Makefile) was updated to include the new `serve` target.

### Running the Web Service

To start the ambient agent locally, open your terminal and run:
```bash
make serve
```

This will launch `uvicorn` on `0.0.0.0:8081`.

### Testing the Pub/Sub Endpoint

You can simulate a Pub/Sub trigger hitting the agent using `curl`. In a new terminal window while the server is running, execute:

```bash
curl -X POST http://127.0.0.1:8081/apps/expense_agent/trigger/pubsub \
  -H "Content-Type: application/json" \
  -d '{
    "subscription": "projects/my-project/subscriptions/test-sub",
    "message": {
      "messageId": "12345",
      "data": "eyJhbW91bnQiOiAxNTAuMCwgInN1Ym1pdHRlciI6ICJhbGljZUBjb21wYW55LmNvbSIsICJjYXRlZ29yeSI6ICJzb2Z0d2FyZSIsICJkZXNjcmlwdGlvbiI6ICJJREUgTGljZW5zZSIsICJkYXRlIjogIjIwMjYtMDYtMDYifQ=="
    }
  }'
```
*(The `data` field contains your previous test payload, Base64 encoded, which is how Pub/Sub transmits it. The agent will decode it automatically).*
