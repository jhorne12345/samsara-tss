#!/usr/bin/env bash
# Spin up the full TSS demo in a tmux session.
#
# Pane layout:
#   ┌──────────────────────────────────────────┐
#   │ pane 0: dispatcher (uvicorn)             │
#   ├──────────────────────────────────────────┤
#   │ pane 1: agent vg-01   │ pane 2: agent vg-02 │
#   ├───────────────────────┼──────────────────┤
#   │ pane 3: agent ag-01   │ pane 4: agent ag-02 │
#   ├───────────────────────┴──────────────────┤
#   │ pane 5: agent combo-01 (both products)   │
#   ├──────────────────────────────────────────┤
#   │ pane 6: operator REPL (cwd in repo root) │
#   └──────────────────────────────────────────┘
#
# Use `tmux kill-session -t tss` to stop everything.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SESSION="${TSS_SESSION:-tss}"
PORT="${TSS_PORT:-8080}"
DISPATCHER_URL="http://127.0.0.1:${PORT}"
TSS="${REPO_ROOT}/.venv/bin/tss"
PYTHON="${REPO_ROOT}/.venv/bin/python"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux not found. Use 'make demo-plain' for the non-tmux fallback."
  exit 1
fi

if ! [ -x "$TSS" ]; then
  echo "tss not installed. Run 'uv sync --extra dev' first."
  exit 1
fi

# Tear down any previous session
tmux kill-session -t "$SESSION" 2>/dev/null || true

cd "$REPO_ROOT"

# Pane 0: dispatcher
tmux new-session -d -s "$SESSION" -n demo \
  "$TSS serve --port $PORT --verbose"

# Wait for dispatcher to be up
for _ in $(seq 1 30); do
  if curl -fs "${DISPATCHER_URL}/api/fleet/status" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

# Split for agents
tmux split-window -t "$SESSION:demo.0" -v -p 70 \
  "$TSS agent --name vg-01 --caps vehicle_gateway --dispatcher $DISPATCHER_URL"
tmux split-window -t "$SESSION:demo.1" -h -p 50 \
  "$TSS agent --name vg-02 --caps vehicle_gateway --dispatcher $DISPATCHER_URL"
tmux split-window -t "$SESSION:demo.1" -v -p 66 \
  "$TSS agent --name ag-01 --caps asset_gateway --dispatcher $DISPATCHER_URL"
tmux split-window -t "$SESSION:demo.2" -v -p 50 \
  "$TSS agent --name ag-02 --caps asset_gateway --dispatcher $DISPATCHER_URL"
tmux split-window -t "$SESSION:demo.3" -v -p 50 \
  "$TSS agent --name combo-01 --caps vehicle_gateway,asset_gateway --dispatcher $DISPATCHER_URL"

# Operator pane
tmux split-window -t "$SESSION:demo.5" -v -p 35 "
echo 'TSS demo ready. Dashboard: ${DISPATCHER_URL}/'
echo
echo 'Try:'
echo '  $TSS submit-job --product vehicle_gateway --duration 8'
echo '  $TSS submit-job --product asset_gateway --duration 12 --crash-at 0.5'
echo '  $TSS agents'
echo '  $TSS jobs'
echo
exec \$SHELL
"

tmux select-pane -t "$SESSION:demo.6"
tmux attach -t "$SESSION"
