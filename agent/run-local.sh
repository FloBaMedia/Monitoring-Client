#!/usr/bin/env bash
# ServerPulse – Local Test Runner (Linux/macOS)
# Runs the agent from the current directory without installing anything.
# Config is stored in ./agent.conf (gitignored).
#
# Usage:
#   bash run-local.sh                    # single run, real POST
#   bash run-local.sh --dry-run          # single run, print JSON only
#   bash run-local.sh --watch            # loop every 60s until Ctrl+C
#   bash run-local.sh --watch --interval 10   # loop every 10s
#   bash run-local.sh --watch --dry-run  # loop, no HTTP
#   bash run-local.sh --debug            # verbose output
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT="$SCRIPT_DIR/agent.py"
CONF="$SCRIPT_DIR/agent.conf"
DEFAULT_API_URL="https://api.yourdomain.com"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

# ── Parse args ────────────────────────────────────────────────────────────────
WATCH=false
INTERVAL=60
AGENT_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --watch)    WATCH=true; shift ;;
        --interval) INTERVAL="$2"; shift 2 ;;
        *)          AGENT_ARGS+=("$1"); shift ;;
    esac
done

# --debug is always on for local runs so output appears in the terminal
# Only pass --config if a local agent.conf exists; otherwise the agent
# uses its normal search order (system config, env vars, etc.)
AGENT_ARGS+=("--debug")
[[ -f "$CONF" ]] && AGENT_ARGS+=("--config" "$CONF")

# ── Find Python ───────────────────────────────────────────────────────────────
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        if "$cmd" -c "import sys; sys.exit(0 if sys.version_info >= (3,6) else 1)" 2>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    fi
done
[[ -z "$PYTHON" ]] && { echo "Python 3.6+ not found."; exit 1; }

# Info: if no local agent.conf exists the agent uses its system config
# (/etc/serverpulse/agent.conf) or prompts via ensure_config
if [[ -f "$CONF" ]]; then
    info "Using local config: $CONF"
else
    info "No local agent.conf found - using system config or prompting on first run"
fi

# ── Run ───────────────────────────────────────────────────────────────────────
run_once() {
    echo ""
    echo "─────────────────────────────────────────────"
    "$PYTHON" "$AGENT" "${AGENT_ARGS[@]}"
}

if [[ "$WATCH" == true ]]; then
    info "Watch mode – running every ${INTERVAL}s. Press Ctrl+C to stop."
    trap 'echo ""; info "Stopped."; exit 0' INT TERM
    RUN=1
    while true; do
        echo ""
        info "Run #${RUN}  $(date '+%Y-%m-%d %H:%M:%S')"
        run_once || true   # don't exit watch loop on agent error
        (( RUN++ ))
        info "Next run in ${INTERVAL}s ..."
        sleep "$INTERVAL"
    done
else
    info "Running agent (no crontab, no system files) ..."
    run_once
fi
