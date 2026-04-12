#!/usr/bin/env bash
# ServerPulse Agent Uninstaller for Linux
# Usage: sudo bash uninstall.sh
set -euo pipefail

INSTALL_DIR="/etc/serverpulse"
AGENT_PATH="$INSTALL_DIR/agent.py"
CONF_PATH="$INSTALL_DIR/agent.conf"
LOG_PATH="/var/log/serverpulse-agent.log"
CRON_MARKER="serverpulse/agent.py"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
confirm() {
    read -rp "$1 [y/N] " ans
    [[ "${ans,,}" == "y" ]]
}

# ── Root check ────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}[ERROR]${NC} Run as root: sudo bash uninstall.sh" >&2
    exit 1
fi

echo ""
echo -e "${CYAN}ServerPulse Agent Uninstaller${NC}"
echo "─────────────────────────────────────────────"
echo ""
echo "This will remove:"
echo "  • Crontab entry"
echo "  • Agent files in $INSTALL_DIR"
echo ""

confirm "Continue with uninstallation?" || { echo "Aborted."; exit 0; }
echo ""

# ── 1. Remove crontab entry ───────────────────────────────────────────────────
if crontab -l 2>/dev/null | grep -qF "$CRON_MARKER"; then
    crontab -l 2>/dev/null | grep -vF "$CRON_MARKER" | crontab -
    info "Crontab entry removed."
else
    info "No crontab entry found – skipping."
fi

# ── 2. Remove agent files ─────────────────────────────────────────────────────
if [[ -d "$INSTALL_DIR" ]]; then
    rm -rf "$INSTALL_DIR"
    info "Removed $INSTALL_DIR"
else
    info "$INSTALL_DIR not found – skipping."
fi

# ── 3. Optionally remove log file ─────────────────────────────────────────────
if [[ -f "$LOG_PATH" ]]; then
    echo ""
    if confirm "Also delete log file $LOG_PATH?"; then
        rm -f "$LOG_PATH" "$LOG_PATH.1"
        info "Log file removed."
    else
        info "Log file kept at $LOG_PATH"
    fi
fi

echo ""
info "ServerPulse Agent has been uninstalled."
echo ""
