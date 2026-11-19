Let me check the exact Agent Runtime endpoint format before walking through the commands.

Ran confirm Agent Runtime resource name

Good — all the values I need are confirmed from `deployment_metadata.json`. Here's the full plan, explained command-by-command.

---

## Five commands, explained in order

There are **5 commands** in total. Here's the dependency chain before we run anything:

```
[1] Create SA  →  [2] Grant SA aiplatform.user
                →  [3] Grant Pub/Sub SA publisher on dead-letter topic
                →  [4] Create subscription
                →  [5] Grant Pub/Sub SA subscriber on subscription (dead-letter policy requirement)
```

---

### Command 1 — Create the `pubsub-invoker` service account

```bash
gcloud iam service-accounts create pubsub-invoker \
  --display-name "Pub/Sub Push Invoker (Agent Runtime)" \
  --project weather-assistant-500219
```

**What it does:** Creates a dedicated identity that Pub/Sub will impersonate when it makes the authenticated HTTPS push to the Agent Runtime endpoint. Using a dedicated SA (rather than a shared one) means you can audit, scope, and revoke its access independently.

Ran create pubsub-invoker service account

✅ SA created: `pubsub-invoker@weather-assistant-500219.iam.gserviceaccount.com`

---

### Command 2 — Grant `aiplatform.user` to `pubsub-invoker`

```bash
gcloud projects add-iam-policy-binding weather-assistant-500219 \
  --member="serviceAccount:pubsub-invoker@weather-assistant-500219.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user" \
  --condition=None
```

**What it does:** Gives the invoker SA permission to call the Vertex AI Agent Runtime REST API — specifically the `:query` endpoint that processes incoming expense events. Without this, the authenticated push would receive a `403 Permission Denied` from Vertex AI.

Ran grant aiplatform.user to pubsub-invoker

✅ `pubsub-invoker` now has `roles/aiplatform.user` on the project.

---

### Command 3 — Grant the Pub/Sub service agent `publisher` on the dead-letter topic

```bash
gcloud pubsub topics add-iam-policy-binding expense-reports-dead-letter \
  --member="serviceAccount:service-1006218893512@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role="roles/pubsub.publisher" \
  --project weather-assistant-500219
```

**What it does:** This grants the **Pub/Sub infrastructure's own service agent** (not your SA) the ability to publish to the dead-letter topic. When a message exceeds `--max-delivery-attempts`, Pub/Sub itself forwards it to the dead-letter topic on your behalf — it needs publisher rights there to do so. Without this, dead-lettering silently breaks.

Ran grant Pub/Sub SA publisher on dead-letter topic

✅ Pub/Sub service agent can publish to `expense-reports-dead-letter`.

---

### Command 4 — Create the push subscription

```bash
gcloud pubsub subscriptions create expense-reports-push \
  --topic expense-reports \
  --push-endpoint "https://europe-west4-aiplatform.googleapis.com/v1/projects/weather-assistant-500219/locations/europe-west4/reasoningEngines/8214064343021518848:query" \
  --push-auth-service-account "pubsub-invoker@weather-assistant-500219.iam.gserviceaccount.com" \
  --push-auth-token-audience "https://europe-west4-aiplatform.googleapis.com/" \
  --push-no-wrapper \
  --ack-deadline 600 \
  --dead-letter-topic expense-reports-dead-letter \
  --max-delivery-attempts 5 \
  --project weather-assistant-500219
```

**What each flag does:**

| Flag | Why |
|------|-----|
| `--push-endpoint` | The Agent Runtime `:query` REST URL — expense events are POSTed here directly |
| `--push-auth-service-account` | Pub/Sub generates an OIDC token for this SA and attaches it as `Authorization: Bearer …` |
| `--push-auth-token-audience` | The audience claim in the OIDC token; must match what Vertex AI expects to validate the token |
| `--push-no-wrapper` | Sends the raw message bytes as the HTTP body instead of wrapping in a `{"message": {...}}` Pub/Sub envelope — the Agent Runtime expects a clean JSON body |
| `--ack-deadline 600` | 10 minutes; gives the agent time to run LLM review + human-approval interrupt without Pub/Sub re-delivering mid-flight |
| `--dead-letter-topic` | After 5 failed deliveries, the message is forwarded here instead of being dropped |
| `--max-delivery-attempts 5` | Failure threshold before dead-lettering |

Ran create OIDC push subscription to Agent Runtime

✅ Subscription created.

---

### Command 5 — Grant the Pub/Sub service agent `subscriber` on the subscription

```bash
gcloud pubsub subscriptions add-iam-policy-binding expense-reports-push \
  --member="serviceAccount:service-1006218893512@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role="roles/pubsub.subscriber" \
  --project weather-assistant-500219
```

**What it does:** The other half of dead-letter plumbing. When Pub/Sub's service agent wants to forward a failed message, it must also be able to **acknowledge and modify** the message on the source subscription, which requires `subscriber` rights on it. Without this, dead-letter forwarding fails silently.

Ran grant Pub/Sub SA subscriber on push subscription

✅ All five steps complete.

---

## ✅ Full pipeline is wired up

```
Publisher
  │
  ▼
[Topic] expense-reports
  │
  ▼
[Subscription] expense-reports-push
  │  OIDC Bearer token (pubsub-invoker SA)
  │  --push-no-wrapper  |  ack-deadline: 600s
  │
  ▼
Agent Runtime :query
  https://europe-west4-aiplatform.googleapis.com/v1/
  projects/weather-assistant-500219/locations/europe-west4/
  reasoningEngines/8214064343021518848:query
  │
  │  (on 5th failure)
  ▼
[Topic] expense-reports-dead-letter
```

**IAM summary**

| Principal | Role | Scope |
|-----------|------|-------|
| `pubsub-invoker` SA | `aiplatform.user` | Project — signs OIDC tokens for Agent Runtime calls |
| Pub/Sub service agent | `pubsub.publisher` | `expense-reports-dead-letter` topic |
| Pub/Sub service agent | `pubsub.subscriber` | `expense-reports-push` subscription |

You can now publish an expense JSON to `expense-reports` and it will flow end-to-end into the agent. Want me to send a test message to smoke-test the pipeline?