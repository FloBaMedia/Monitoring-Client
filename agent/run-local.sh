#!/usr/bin/env bash
# ServerPulse – Local Test Runner (Linux/macOS)
# Runs the agent once from the current directory without installing anything.
# Config is stored in ./agent.conf (gitignored).
#
# Usage:
#   bash run-local.sh           # real POST to the API
#   bash run-local.sh --dry-run # print metrics JSON, no HTTP request
#   bash run-local.sh --debug   # verbose output
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT="$SCRIPT_DIR/agent.py"
CONF="$SCRIPT_DIR/agent.conf"
DEFAULT_API_URL="https://api.yourdomain.com"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

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

# ── Run the agent ─────────────────────────────────────────────────────────────
echo ""
info "Running agent (no crontab, no system files) ..."
echo "─────────────────────────────────────────────"
"$PYTHON" "$AGENT" --config "$CONF" "$@"
