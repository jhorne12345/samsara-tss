.PHONY: install demo demo-plain test test-fast test-chaos lint typecheck format clean

PYTHON := .venv/bin/python
TSS := .venv/bin/tss
PORT := 8080

install:
	uv sync --extra dev

demo:
	@command -v tmux >/dev/null 2>&1 || { echo "tmux not found; falling back to demo-plain"; $(MAKE) demo-plain; exit 0; }
	./scripts/demo.sh

demo-plain:
	@mkdir -p .tss
	@echo "Starting dispatcher on http://localhost:$(PORT)..."
	@$(TSS) serve --port $(PORT) > .tss/dispatcher.log 2>&1 & echo $$! > .tss/dispatcher.pid
	@sleep 2
	@echo "Spawning 5 mock agents..."
	@$(TSS) agent --name vg-01 --caps vehicle_gateway > .tss/vg-01.log 2>&1 & echo $$! > .tss/vg-01.pid
	@$(TSS) agent --name vg-02 --caps vehicle_gateway > .tss/vg-02.log 2>&1 & echo $$! > .tss/vg-02.pid
	@$(TSS) agent --name ag-01 --caps asset_gateway > .tss/ag-01.log 2>&1 & echo $$! > .tss/ag-01.pid
	@$(TSS) agent --name ag-02 --caps asset_gateway > .tss/ag-02.log 2>&1 & echo $$! > .tss/ag-02.pid
	@$(TSS) agent --name combo-01 --caps vehicle_gateway,asset_gateway > .tss/combo-01.log 2>&1 & echo $$! > .tss/combo-01.pid
	@echo ""
	@echo "Dispatcher: http://localhost:$(PORT)"
	@echo "Logs: .tss/*.log"
	@echo "Stop with: make demo-stop"

demo-stop:
	@for pidfile in .tss/*.pid; do \
		if [ -f $$pidfile ]; then \
			kill $$(cat $$pidfile) 2>/dev/null || true; \
			rm $$pidfile; \
		fi; \
	done
	@echo "All processes stopped."

test:
	$(PYTHON) -m pytest -v

test-fast:
	$(PYTHON) -m pytest -v -m "not chaos"

test-chaos:
	$(PYTHON) -m pytest -v -m chaos

lint:
	.venv/bin/ruff check tss tests
	.venv/bin/ruff format --check tss tests

format:
	.venv/bin/ruff format tss tests
	.venv/bin/ruff check --fix tss tests

typecheck:
	.venv/bin/mypy tss

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov .tss
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
