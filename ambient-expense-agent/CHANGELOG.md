4_1 -> 5_1
agents-cli scaffold enhance --deployment-target agent_runtime
uv lock
agents-cli deploy --dry-run --no-confirm-project --region=europe-west4

