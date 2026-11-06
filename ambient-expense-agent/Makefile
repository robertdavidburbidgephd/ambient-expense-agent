.PHONY: install playground serve generate-traces grade

install:
	uv sync

playground:
	agents-cli playground

serve:
	uv run uvicorn fast_api_app:app --host 0.0.0.0 --port 8081

generate-traces:
	uv run python tests/eval/generate_traces.py

grade:
	agents-cli eval grade --traces artifacts/traces/generated_traces.json --config tests/eval/eval_config.yaml

