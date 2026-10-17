.PHONY: install samples test eval lint demo serve mcp docker
install:  ; pip install -e ".[dev,groq]"
samples:  ; python scripts/make_samples.py
test:     ; python -m pytest
eval:     ; python evals/run_evals.py
lint:     ; ruff check src scripts tests evals && ruff format --check src scripts tests evals
demo:     ; python -m invoiceguard demo
serve:    ; uvicorn invoiceguard.api:app --reload
mcp:      ; python -m invoiceguard.mcp_server
docker:   ; docker compose up --build
