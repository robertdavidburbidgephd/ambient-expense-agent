import asyncio
import json
import os
from pathlib import Path

# Provide standard ADK env vars for silent local test execution
os.environ["ADK_OTEL_TO_CLOUD"] = "False"

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from expense_agent.agent import root_agent

from google.adk.events.request_input import RequestInput

async def main():
    dataset_path = Path("tests/eval/datasets/basic-dataset.json")
    with open(dataset_path) as f:
        dataset = json.load(f)

    traces = []
    
    for case in dataset["eval_cases"]:
        case_id = case["eval_case_id"]
        prompt = case["prompt"]
        payload_str = prompt["parts"][0]["text"]
        
        session_service = InMemorySessionService()
        await session_service.create_session(app_name="expense_agent", user_id="eval", session_id=case_id)
        
        runner = Runner(agent=root_agent, app_name="expense_agent", session_service=session_service)
        
        print(f"Generating trace for: {case_id}")
        
        try:
            # First pass
            async for event in runner.run_async(
                user_id="eval", 
                session_id=case_id,
                new_message=types.Content.model_validate(prompt)
            ):
                if isinstance(event, RequestInput):
                    msg_text = event.message.lower() if event.message else ""
                    
                    # Automate human logic for eval
                    decision = "reject" if ("injection" in msg_text or "security alert" in msg_text) else "approve"
                    print(f"  -> Intercepted RequestInput. Decision: {decision}")
                    
                    # Resume
                    async for sub_event in runner.run_async(
                        user_id="eval",
                        session_id=case_id,
                        resume_inputs={"human_decision": decision}
                    ):
                        pass
        except Exception as e:
            print(f"  -> Error executing {case_id}: {e}")
                    
        # Extract the trace
        session = await session_service.get_session(app_name="expense_agent", user_id="eval", session_id=case_id)
        
        # We need to map ADK Event objects to standard ConversationTurn/AgentEvent schema
        filtered_events = []
        for evt in session.events:
            # We must include the event if it has content, or if we want to trace it.
            # Some workflow events don't have content. Let's create a synthetic content if missing so grading can see it.
            # But the schema requires 'author' and 'content' (Content).
            if evt.content:
                filtered_events.append({
                    "author": evt.author,
                    "content": json.loads(evt.content.model_dump_json(exclude_none=True))
                })
            else:
                # If there's no content, but there's output/actions, we can serialize that into a synthetic content part
                txt = f"Route: {evt.actions.route}" if evt.actions and evt.actions.route else str(evt.output)
                filtered_events.append({
                    "author": evt.author,
                    "content": {
                        "role": "model",
                        "parts": [{"text": txt}]
                    }
                })
        
        traces.append({
            "eval_case_id": case_id,
            "agent_data": {
                "turns": [
                    {
                        "turn_index": 0,
                        "events": filtered_events
                    }
                ]
            },
            "responses": [
                {
                    "response": {
                        "role": "model",
                        "parts": [{"text": "DUMMY RESPONSE"}]
                    }
                }
            ]
        })
        
    out_dir = Path("artifacts/traces")
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "generated_traces.json", "w") as f:
        json.dump({"eval_cases": traces}, f, indent=2)
    print(f"\nGenerated traces for {len(traces)} cases at artifacts/traces/generated_traces.json")

if __name__ == "__main__":
    asyncio.run(main())
