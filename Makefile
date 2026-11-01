.PHONY: demo

# Record docs/assets/demo.gif. Needs VHS (https://github.com/charmbracelet/vhs) and uv, plus an
# API key for the provider you want in the recording, for example:
#   OPENAI_API_KEY=... make demo
#   GITCOMMIT_AI_PROVIDER=anthropic ANTHROPIC_API_KEY=... make demo
#   GITCOMMIT_AI_PROVIDER=ollama make demo
demo:
	@command -v vhs >/dev/null || { echo "vhs is not installed: https://github.com/charmbracelet/vhs"; exit 1; }
	@command -v uv >/dev/null || { echo "uv is not installed: https://docs.astral.sh/uv/"; exit 1; }
	@test -n "$$OPENAI_API_KEY$$ANTHROPIC_API_KEY$$GOOGLE_API_KEY$$GITCOMMIT_AI_PROVIDER" || { \
		echo "Set a provider key (OPENAI_API_KEY, ANTHROPIC_API_KEY or GOOGLE_API_KEY) or GITCOMMIT_AI_PROVIDER=ollama"; exit 1; }
	bash scripts/demo/prepare.sh
	vhs docs/demo.tape
