#!/bin/bash

# setup-dev.sh - Development Environment Setup for Linux/macOS
# Calls setup.sh for production setup, then adds dev-specific tools
# Usage: ./scripts/setup-dev.sh [--force|-f]

set -e

FORCE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --force|-f)
            FORCE=true
            shift
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Usage: $0 [--force|-f]" >&2
            exit 1
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Color codes
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
MAGENTA='\033[0;35m'
WHITE='\033[0;37m'
GRAY='\033[0;90m'
NC='\033[0m'

# Test whether a value is a placeholder (not a real configured value).
# Pre-filled team values (like a real Supabase URL) do NOT match these patterns.
is_placeholder_value() {
    local value="$1"
    if [ -z "$value" ]; then
        return 0
    fi
    case "$value" in
        your[-_]*)        return 0 ;;
        example*)         return 0 ;;
        CHANGE_ME*)       return 0 ;;
        change[-_]me*)    return 0 ;;
        \<team-*)         return 0 ;;
        replace[-_]me*)   return 0 ;;
        TODO*)            return 0 ;;
        "")               return 0 ;;
        *)                return 1 ;;
    esac
}

echo -e "${CYAN}========================================"
echo -e "   Collab Development Setup"
echo -e "========================================${NC}\n"

ERROR_COUNT=0

# --- IDE helpers: PATH + default install paths; detection via env, process tree, workspace ---

setup_dev_ps_comm() {
    ps -p "$1" -o comm= 2>/dev/null | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' || true
}

setup_dev_get_ancestor_base_names() {
    pid=$$
    i=0
    while [ "$i" -lt 30 ]; do
        comm=$(setup_dev_ps_comm "$pid")
        if [ -z "$comm" ]; then
            break
        fi
        basename=${comm##*/}
        printf '%s\n' "$basename"
        ppid=$(ps -p "$pid" -o ppid= 2>/dev/null | tr -d ' ' || true)
        if [ -z "$ppid" ] || [ "$ppid" = "0" ] || [ "$ppid" = "$pid" ]; then
            break
        fi
        pid=$ppid
        i=$((i + 1))
    done
}

setup_dev_detect_ide_kind() {
    ancestors_lc="$(setup_dev_get_ancestor_base_names | tr '[:upper:]' '[:lower:]')"
    if echo "$ancestors_lc" | grep -qE '^(cursor|code|code - insiders|antigravity|vscodium|codium)$'; then
        echo "vscode_family"
        return
    fi
    if echo "$ancestors_lc" | grep -qE 'idea64|pycharm|rider64|webstorm|phpstorm|clion|goland|rubymine|devenv'; then
        echo "jetbrains"
        return
    fi
    case "${TERMINAL_EMULATOR:-}" in
        *JetBrains*)
            echo "jetbrains"
            return
            ;;
    esac
    case "${TERM_PROGRAM:-}" in
        vscode|Antigravity|cursor|Cursor)
            echo "vscode_family"
            return
            ;;
    esac
    if [ -n "${CURSOR_TRACE_ID:-}" ] || [ -n "${CURSOR_AGENT:-}" ]; then
        echo "vscode_family"
        return
    fi
    if [ -d ".idea" ]; then
        echo "jetbrains"
        return
    fi
    if [ -d ".vscode" ] || [ -d ".cursor" ]; then
        echo "vscode_family"
        return
    fi
    echo "unknown"
}

setup_dev_collect_editor_clis() {
    seen_sp=" "
    for name in code code-insiders cursor codium antigravity; do
        if command -v "$name" >/dev/null 2>&1; then
            p=$(command -v "$name")
            case "$seen_sp" in
                *" $p "*) ;;
                *)
                    printf '%s\n' "$p"
                    seen_sp="$seen_sp$p "
                    ;;
            esac
        fi
    done
    case "$(uname -s)" in
        Darwin)
            for p in \
                "/Applications/Cursor.app/Contents/Resources/app/bin/cursor" \
                "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code" \
                "/Applications/Visual Studio Code - Insiders.app/Contents/Resources/app/bin/code-insiders" \
                "/Applications/VSCodium.app/Contents/Resources/app/bin/codium" \
                "/Applications/Antigravity.app/Contents/Resources/app/bin/antigravity"; do
                if [ -x "$p" ]; then
                    case "$seen_sp" in
                        *" $p "*) ;;
                        *)
                            printf '%s\n' "$p"
                            seen_sp="$seen_sp$p "
                            ;;
                    esac
                fi
            done
            ;;
        Linux)
            for p in /usr/bin/code /usr/bin/cursor /usr/local/bin/code /usr/local/bin/cursor \
                "${HOME}/.local/bin/code" "${HOME}/.local/bin/cursor"; do
                if [ -x "$p" ]; then
                    case "$seen_sp" in
                        *" $p "*) ;;
                        *)
                            printf '%s\n' "$p"
                            seen_sp="$seen_sp$p "
                            ;;
                    esac
                fi
            done
            ;;
    esac
}

setup_dev_install_collab_locks_vsix() {
    cli_list=$(setup_dev_collect_editor_clis)
    if [ -z "$cli_list" ]; then
        echo -e "     ${GRAY}- No editor CLI for VSIX install (install Cursor/VS Code or add to PATH)${NC}"
        return 0
    fi
    if ! command -v curl >/dev/null 2>&1; then
        echo -e "     ${YELLOW}- curl not found; skipping VSIX download${NC}"
        return 0
    fi
    TEMP_VSIX="${TMPDIR:-/tmp}/collab-locks-latest.vsix"
    VSIX_URL=$(curl -sL -H 'User-Agent: collab-setup-dev' \
        https://api.github.com/repos/KirilMT/collab/releases/latest \
        | grep "browser_download_url.*vsix" | cut -d '"' -f 4 | head -n 1)
    if [ -z "$VSIX_URL" ]; then
        echo -e "     ${YELLOW}- No .vsix on latest release (non-fatal)${NC}"
        return 0
    fi
    if ! curl -sL "$VSIX_URL" -o "$TEMP_VSIX"; then
        echo -e "     ${YELLOW}- VSIX download failed (non-fatal)${NC}"
        return 0
    fi
    while IFS= read -r cli; do
        [ -z "$cli" ] && continue
        echo -e "     ${GRAY}- Installing Collab Locks VSIX via ${WHITE}$cli${GRAY}...${NC}"
        if "$cli" --install-extension "$TEMP_VSIX" --force >/dev/null 2>&1; then
            echo -e "       ${GREEN}OK${NC}"
        else
            echo -e "       ${YELLOW}WARN${NC}"
        fi
    done <<EOF
$cli_list
EOF
    rm -f "$TEMP_VSIX"
    return 0
}

# ============================================================================
# STEP 1: RUN PRODUCTION SETUP
# ============================================================================
echo -e "${MAGENTA}========================================"
echo -e "   PRODUCTION SETUP"
echo -e "========================================${NC}\n"

if [ ! -f "scripts/setup.sh" ]; then
    echo -e "${RED}Error: scripts/setup.sh not found.${NC}"
    exit 1
fi

echo -e "${YELLOW}Running production setup (setup.sh)...${NC}\n"
SETUP_ARGS=(--called-from-dev)
if [ "$FORCE" = true ]; then
    SETUP_ARGS+=(--force)
fi
./scripts/setup.sh "${SETUP_ARGS[@]}"

echo -e "\n${GREEN}========================================"
echo -e "   Production Setup Complete"
echo -e "========================================${NC}\n"

# ============================================================================
# STEP 2: DEVELOPMENT TOOLS SETUP
# ============================================================================
echo -e "${MAGENTA}========================================"
echo -e "   DEVELOPMENT TOOLS SETUP"
echo -e "========================================${NC}\n"

# Step 1: Check for Node.js
echo -e "${YELLOW}[Dev Step 1/6] Checking Node.js...${NC}"
if command -v npm >/dev/null 2>&1; then
    NPM_VERSION=$(npm --version)
    echo -e "   Found: ${WHITE}npm $NPM_VERSION${NC} ${GREEN}OK${NC}"
else
    echo -e "   ${RED}Node.js/npm not found. Please install Node.js (LTS recommended) from https://nodejs.org${NC}"
    exit 1
fi

# Step 2: Check for GitHub CLI
echo -e "\n${YELLOW}[Dev Step 2/6] Checking GitHub CLI...${NC}"
if command -v gh >/dev/null 2>&1; then
    GH_VERSION=$(gh --version | head -n 1 | awk '{print $3}')
    echo -e "   Found: ${WHITE}gh $GH_VERSION${NC} ${GREEN}OK${NC}"
else
    echo -e "   ${GRAY}GitHub CLI not found (optional). Install from https://cli.github.com${NC}"
fi

# Step 3: Python Development Tools
echo -e "\n${YELLOW}[Dev Step 3/6] Installing Python development tools...${NC}"
if [ -d ".venv/bin" ]; then
    PIP_CMD=".venv/bin/pip"
else
    PIP_CMD=".venv/Scripts/pip"
fi

if [ -f "requirements-dev.txt" ]; then
    echo -e "   Ensuring all dev dependencies are installed and up-to-date..."
    if "$PIP_CMD" install --upgrade --upgrade-strategy only-if-needed -r requirements-dev.txt >/dev/null 2>&1; then
        echo -e "   ${WHITE}Python dev dependencies are present and up-to-date${NC} ${GREEN}OK${NC}"
    else
        echo -e "   ${RED}Python dev dependencies installation FAILED${NC}"
        ERROR_COUNT=$((ERROR_COUNT + 1))
    fi
else
    echo -e "   ${YELLOW}Warning: requirements-dev.txt not found.${NC}"
    ERROR_COUNT=$((ERROR_COUNT + 1))
fi

# Step 4: JavaScript Development Tools
echo -e "\n${YELLOW}[Dev Step 4/6] Setting up JavaScript development tools...${NC}"
if [ ! -f "package.json" ]; then
    echo -en "   Initializing ${MAGENTA}package.json${NC}... "
    npm init -y >/dev/null 2>&1
    echo -e "${GREEN}OK${NC}"
else
    echo -e "   package.json already exists ${GREEN}OK${NC}"
fi

echo -en "   Installing ${MAGENTA}prettier + prettier-plugin-yaml${NC}... "
if npm install --save-dev prettier prettier-plugin-yaml >/dev/null 2>&1; then
    echo -e "${GREEN}OK${NC}"
else
    echo -e "${RED}FAILED${NC}"
    ERROR_COUNT=$((ERROR_COUNT + 1))
fi

# Install Playwright Chromium (required for E2E tests / validate_code.py)
echo -en "   Installing ${MAGENTA}Playwright Chromium${NC} (E2E test browser)... "
if npx playwright install chromium >/dev/null 2>&1; then
    echo -e "${GREEN}OK${NC}"
else
    echo -e "${YELLOW}FAILED (non-fatal — E2E tests will need manual browser install)${NC}"
fi

# Step 5: Git Template + Pre-commit Hooks
echo -e "\n${YELLOW}[Dev Step 5/6] Setting up Conventional Commit template and hooks...${NC}"
if [ -f ".gitmessage" ]; then
    git config --local commit.template .gitmessage
    echo -e "   ${GREEN}[OK] .gitmessage set as commit template${NC}"
else
    echo -e "   ${YELLOW}[WARN] .gitmessage not found, skipping commit template setup${NC}"
fi

if [ -d ".venv/bin" ]; then
    PRECOMMIT_CMD=".venv/bin/pre-commit"
else
    PRECOMMIT_CMD=".venv/Scripts/pre-commit"
fi

if [ -f "$PRECOMMIT_CMD" ]; then
    VERSION=$("$PRECOMMIT_CMD" --version)
    echo -e "   Using: ${WHITE}$VERSION${NC} ${GREEN}OK${NC}"
    echo -e "   Installing repository hooks (framework mode)..."
    for hook in pre-commit pre-push commit-msg; do
        echo -en "     - Installing $hook hook... "
        "$PRECOMMIT_CMD" install --hook-type "$hook" --overwrite >/dev/null 2>&1
        echo -e "${GREEN}OK${NC}"
    done
else
    echo -e "   ${YELLOW}Pre-commit not found. Skipping hook installation.${NC}"
fi

# Step 6: Supabase Setup
echo -e "\n${YELLOW}[Dev Step 6/6] Configure Supabase locking settings...${NC}"
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "   ${GREEN}Created .env from .env.example${NC}"
    fi
fi

if [ -f ".env" ]; then
    echo -e "   Supabase configuration is required for live collaborative locks."

    SUPABASE_URL_DEV=$(grep -E '^SUPABASE_URL=' .env | head -n 1 | cut -d '=' -f 2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    SUPABASE_ANON_DEV=$(grep -E '^SUPABASE_ANON_KEY=' .env | head -n 1 | cut -d '=' -f 2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

    url_dev_ok=1
    anon_dev_ok=1
    if [ -z "$SUPABASE_URL_DEV" ] || is_placeholder_value "$SUPABASE_URL_DEV"; then
        url_dev_ok=0
    fi
    if [ -z "$SUPABASE_ANON_DEV" ] || is_placeholder_value "$SUPABASE_ANON_DEV"; then
        anon_dev_ok=0
    fi

    if [ $url_dev_ok -eq 1 ] && [ $anon_dev_ok -eq 1 ]; then
        echo -e "   ${WHITE}SUPABASE_URL: using pre-configured team value${NC} ${GREEN}OK${NC}"
    else
        echo -e "   ${YELLOW}Missing or placeholder Supabase entries in .env${NC}"
        ERROR_COUNT=$((ERROR_COUNT + 1))
    fi
fi

DETECTED_IDE=unknown
echo -e "\n${YELLOW}   Detecting IDE environment...${NC}"
DETECTED_IDE=$(setup_dev_detect_ide_kind || echo unknown)
echo -e "     ${GRAY}- IDE kind: ${WHITE}${DETECTED_IDE}${NC}"

if [ "$DETECTED_IDE" = "vscode_family" ]; then
    ancestors_lc="$(setup_dev_get_ancestor_base_names | tr '[:upper:]' '[:lower:]')"
    if echo "$ancestors_lc" | grep -qE '^cursor$'; then
        echo -e "     ${GRAY}- Cursor / VS Code-compatible IDE detected${NC}"
    else
        echo -e "     ${GRAY}- VS Code-compatible IDE detected${NC}"
    fi
    setup_dev_install_collab_locks_vsix || true
    if [ -f "editors/vscode/collab-locks/package.json" ]; then
        if (cd editors/vscode/collab-locks && npm install --silent >/dev/null 2>&1); then
            echo -e "     ${WHITE}VS Code extension workspace deps (npm)${NC} ${GREEN}OK${NC}"
        else
            echo -e "     ${YELLOW}VS Code extension npm install failed (non-fatal)${NC}"
        fi
    fi
elif [ "$DETECTED_IDE" = "jetbrains" ]; then
    echo -e "     ${GRAY}- JetBrains IDE detected${NC}"
    if [ -f "editors/pycharm/Collab_Lock_Watcher.xml" ]; then
        mkdir -p .idea/runConfigurations
        if cp -f editors/pycharm/Collab_Lock_Watcher.xml .idea/runConfigurations/Collab_Lock_Watcher.xml; then
            echo -e "     ${WHITE}PyCharm run configuration installed${NC} ${GREEN}OK${NC}"
            echo -e "     ${GRAY}- Open Run > Collab Lock Watcher in the IDE.${NC}"
        else
            echo -e "     ${YELLOW}PyCharm run config install failed (non-fatal)${NC}"
        fi
    fi
else
    echo -e "     ${GRAY}- No specific IDE detected from env / process / workspace hints${NC}"
    if setup_dev_collect_editor_clis | grep -q .; then
        echo -e "     ${GRAY}- Editor CLI(s) found; installing VSIX anyway${NC}"
        setup_dev_install_collab_locks_vsix || true
    fi
fi

echo -e "\n${CYAN}========================================"
if [ $ERROR_COUNT -eq 0 ]; then
    echo -e "   Development Setup Complete!"
    echo -e "   ${GRAY}(Production + Dev Tools + Daemon Active)${NC}"
else
    echo -e "   Setup completed with ${YELLOW}$ERROR_COUNT warning(s)${NC}"
fi
echo -e "========================================${NC}\n"

echo -e "\n${CYAN}================================================================"
echo -e "                        NEXT STEPS                              "
echo -e "================================================================${NC}"
echo ""
if [ "$DETECTED_IDE" = "vscode_family" ]; then
    echo -e "${WHITE}  1. Collab Locks VSIX and workspace extension deps were applied when possible.${NC}"
    echo -e "${GRAY}     Press F1 > 'Developer: Reload Window' if locks don't appear.${NC}"
else
    echo -e "${WHITE}  1. Collaborative daemon should be active (Core Step 9).
     Use 'collab active' to verify.
${NC}"
fi
echo ""
echo -e "${WHITE}  2. Activate the virtual environment (if not already active):${NC}"
if [ -d ".venv/bin" ]; then
    echo -e "     source .venv/bin/activate"
    echo -e "     ${GRAY}Agent shells often skip activation; prefer ./.venv/bin/python when PATH is wrong.${NC}"
else
    echo -e "     source .venv/Scripts/activate"
    echo -e "     ${GRAY}Agent shells often skip activation; prefer .venv/Scripts/python.exe when PATH is wrong.${NC}"
fi
echo ""
echo -e "${WHITE}  3. Run quality checks:${NC}"
echo -e "     python scripts/format_code.py"
echo -e "     python scripts/validate_code.py --quick"
echo ""
echo -e "${CYAN}================================================================${NC}\n"
