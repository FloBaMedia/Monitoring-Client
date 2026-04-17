#!/usr/bin/env bash
# ServerPulse Agent Installer for Linux
# Usage: curl -fsSL https://raw.githubusercontent.com/FloBaMedia/Monitoring-Client/main/agent/install.sh | bash
set -euo pipefail

GITHUB_BASE="https://raw.githubusercontent.com/FloBaMedia/Monitoring-Client/main/agent"
INSTALL_DIR="/etc/serverpulse"
AGENT_PATH="$INSTALL_DIR/agent.py"
CONF_PATH="$INSTALL_DIR/agent.conf"
CRON_MARKER="serverpulse/agent.py"
DEFAULT_API_URL="https://sp-api.floba-media.de"

# All module files that must be present alongside agent.py
MODULE_FILES=(
    "client/__init__.py"
    "client/api.py"
    "models/__init__.py"
    "models/constants.py"
    "services/__init__.py"
    "services/config_applier.py"
    "services/linux.py"
    "services/darwin.py"
    "services/windows.py"
    "utils/__init__.py"
    "utils/config.py"
    "utils/logging.py"
)

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()     { error "$*"; exit 1; }

_download() {
    local url="$1" dest="$2"
    if command -v curl &>/dev/null; then
        curl -fsSL "$url" -o "$dest"
    else
        wget -qO "$dest" "$url"
    fi
}

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
mkdir -p "$INSTALL_DIR" \
         "$INSTALL_DIR/client" \
         "$INSTALL_DIR/models" \
         "$INSTALL_DIR/services" \
         "$INSTALL_DIR/utils"

if ! command -v curl &>/dev/null && ! command -v wget &>/dev/null; then
    die "Neither curl nor wget found. Install one and re-run."
fi

# ── 4. Download agent files ───────────────────────────────────────────────────
info "Downloading agent files ..."
_download "$GITHUB_BASE/agent.py" "$AGENT_PATH"
chmod 755 "$AGENT_PATH"

for mod in "${MODULE_FILES[@]}"; do
    _download "$GITHUB_BASE/$mod" "$INSTALL_DIR/$mod"
done
info "Agent installed in $INSTALL_DIR"

# ── 5. Config (env vars → existing config → interactive) ──────────────────────
# Priority: env vars > existing agent.conf > interactive prompt with defaults.
# Running the installer again acts as an update — existing values are offered
# as defaults so the user only needs to press Enter to keep them.

API_URL="${SERVERPULSE_URL:-}"
API_KEY="${SERVERPULSE_KEY:-}"
API_URL="${API_URL%/}"

# Read values from an existing config file (update / reinstall scenario)
CONF_URL=""
CONF_KEY=""
if [[ -f "$CONF_PATH" ]]; then
    CONF_URL=$(grep -E '^\s*api_url\s*=' "$CONF_PATH" 2>/dev/null \
               | sed 's/.*=\s*//' | tr -d ' \r' || true)
    CONF_KEY=$(grep -E '^\s*api_key\s*=' "$CONF_PATH" 2>/dev/null \
               | sed 's/.*=\s*//' | tr -d ' \r' || true)
fi

if [[ -n "$API_URL" && -n "$API_KEY" ]]; then
    info "Using API URL and API Key from environment variables."
else
    [[ -n "$CONF_URL" || -n "$CONF_KEY" ]] && \
        info "Existing config found – press Enter to keep current values."
    echo ""
    echo "Please enter your ServerPulse configuration:"

    # When piped through `curl | bash`, stdin is the pipe — redirect reads
    # from /dev/tty so the user can still type interactively.
    if [[ -z "$API_URL" ]]; then
        URL_DEFAULT="${CONF_URL:-$DEFAULT_API_URL}"
        while true; do
            read -rp "  API URL [${URL_DEFAULT}]: " API_URL </dev/tty
            API_URL="${API_URL%/}"
            [[ -z "$API_URL" ]] && API_URL="$URL_DEFAULT"
            if [[ "$API_URL" =~ ^https?:// ]]; then
                break
            fi
            warn "URL must start with http:// or https://"
        done
    else
        info "Using API URL from environment: $API_URL"
    fi

    if [[ -z "$API_KEY" ]]; then
        if [[ -n "$CONF_KEY" ]]; then
            MASKED="${CONF_KEY:0:10}***"
            read -rsp "  API Key [${MASKED}]: " API_KEY </dev/tty
            echo
            [[ -z "$API_KEY" ]] && API_KEY="$CONF_KEY" && info "Keeping existing API Key."
        else
            while true; do
                read -rsp "  API Key (sp_live_...): " API_KEY </dev/tty
                echo
                if [[ ${#API_KEY} -ge 8 ]]; then
                    break
                fi
                warn "API key seems too short. Please try again."
            done
        fi
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
    # `|| true` prevents set -e from aborting when no crontab exists yet (exit 1)
    { crontab -l 2>/dev/null || true; echo "$CRON_LINE"; } | crontab -
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
