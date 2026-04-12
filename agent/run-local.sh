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
AGENT_ARGS+=("--debug")

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

# ── Create local config if missing ───────────────────────────────────────────
if [[ ! -f "$CONF" ]]; then
    echo ""
    echo -e "${CYAN}No local config found – creating $CONF${NC}"
    echo ""

    while true; do
        read -rsp "  API Key (sp_live_...): " API_KEY; echo
        [[ ${#API_KEY} -ge 8 ]] && break
        warn "API key too short. Try again."
    done

    cat > "$CONF" <<EOF
[serverpulse]
api_url = $DEFAULT_API_URL
api_key = $API_KEY
EOF
    info "Config saved to $CONF"
    echo ""
fi

# ── Run ───────────────────────────────────────────────────────────────────────
run_once() {
    echo ""
    echo "─────────────────────────────────────────────"
    "$PYTHON" "$AGENT" --config "$CONF" "${AGENT_ARGS[@]}"
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
