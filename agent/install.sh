#!/usr/bin/env bash
# ServerPulse Agent Installer for Linux
# Usage: curl -fsSL https://raw.githubusercontent.com/FloBaMedia/Monitoring-Client/main/agent/install.sh | bash
set -euo pipefail

GITHUB_RAW="https://raw.githubusercontent.com/FloBaMedia/Monitoring-Client/main/agent/agent.py"
INSTALL_DIR="/etc/serverpulse"
AGENT_PATH="$INSTALL_DIR/agent.py"
CONF_PATH="$INSTALL_DIR/agent.conf"
CRON_MARKER="serverpulse/agent.py"

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()     { error "$*"; exit 1; }

# ── 1. Root check ────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    die "This installer must be run as root. Try: sudo bash install.sh"
fi

info "ServerPulse Agent Installer"
echo "─────────────────────────────────────────────"

# ── 2. Python 3.6+ check ─────────────────────────────────────────────────────
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &>/dev/null; then
        if "$cmd" -c "import sys; sys.exit(0 if sys.version_info >= (3,6) else 1)" 2>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    die "Python 3.6+ not found. Install it with your package manager (e.g. apt install python3)"
fi

PY_VER=$("$PYTHON" -c "import sys; v=sys.version_info; print('{}.{}'.format(v.major, v.minor))")
info "Found Python $PY_VER at $(command -v "$PYTHON")"

# ── 3. Create install directory ───────────────────────────────────────────────
info "Creating $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"

# ── 4. Download agent.py ──────────────────────────────────────────────────────
info "Downloading agent.py ..."
if command -v curl &>/dev/null; then
    curl -fsSL "$GITHUB_RAW" -o "$AGENT_PATH"
elif command -v wget &>/dev/null; then
    wget -qO "$AGENT_PATH" "$GITHUB_RAW"
else
    die "Neither curl nor wget found. Install one and re-run."
fi
chmod 755 "$AGENT_PATH"
info "Agent installed at $AGENT_PATH"

# ── 5. Config (env vars or interactive) ───────────────────────────────────────
# Accept API_URL / API_KEY from environment so the installer can run non-interactively:
#   curl -sSL <url> | SERVERPULSE_URL=https://... SERVERPULSE_KEY=sp_live_... bash
API_URL="${SERVERPULSE_URL:-}"
API_KEY="${SERVERPULSE_KEY:-}"

# Strip trailing slash if provided via env
API_URL="${API_URL%/}"

if [[ -n "$API_URL" && -n "$API_KEY" ]]; then
    info "Using API URL and API Key from environment variables."
else
    echo ""
    echo "Please enter your ServerPulse configuration:"

    if [[ -z "$API_URL" ]]; then
        while true; do
            read -rp "  API URL (e.g. https://api.yourdomain.com): " API_URL
            API_URL="${API_URL%/}"
            if [[ "$API_URL" =~ ^https?:// ]]; then
                break
            fi
            warn "URL must start with http:// or https://"
        done
    else
        info "Using API URL from environment: $API_URL"
    fi

    if [[ -z "$API_KEY" ]]; then
        while true; do
            read -rsp "  API Key (sp_live_...): " API_KEY
            echo
            if [[ ${#API_KEY} -ge 8 ]]; then
                break
            fi
            warn "API key seems too short. Please try again."
        done
    else
        info "Using API Key from environment."
    fi
fi

# ── 6. Write config ───────────────────────────────────────────────────────────
cat > "$CONF_PATH" <<EOF
[serverpulse]
api_url = $API_URL
api_key = $API_KEY
EOF
chmod 600 "$CONF_PATH"
info "Config written to $CONF_PATH (mode 600)"

# ── 7. Crontab entry (idempotent) ─────────────────────────────────────────────
CRON_LINE="* * * * * $PYTHON $AGENT_PATH"

if crontab -l 2>/dev/null | grep -qF "$CRON_MARKER"; then
    info "Crontab entry already exists – skipping."
else
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
    info "Added crontab entry: $CRON_LINE"
fi

# ── 8. First test run ─────────────────────────────────────────────────────────
echo ""
info "Running first test (dry-run, no HTTP request) ..."
echo "─────────────────────────────────────────────"
"$PYTHON" "$AGENT_PATH" --dry-run
echo "─────────────────────────────────────────────"

echo ""
info "Installation complete!"
info "The agent will run every minute via crontab."
info "Logs: /var/log/serverpulse-agent.log"
echo ""
echo "To test a live run now (sends real data to your API):"
echo "  sudo $PYTHON $AGENT_PATH"
echo ""
echo "To view logs:"
echo "  tail -f /var/log/serverpulse-agent.log"
