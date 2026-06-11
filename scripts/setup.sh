#!/bin/bash

# setup.sh - Enhanced Collab Installation Script
# Provides detailed feedback and error handling for Unix/Linux/macOS environments
# Supports non-interactive mode for automation and CI provisioning

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

ERROR_COUNT=0

# Color codes for output
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
MAGENTA='\033[0;35m'
WHITE='\033[0;37m'
GRAY='\033[0;90m'
NC='\033[0m' # No Color

print_banner() {
    echo -e "\n${CYAN}========================================"
    echo -e "   Collab Installation Script"
    echo -e "========================================${NC}\n"
}

print_step() {
    echo -e "${YELLOW}[Step $1/$2] $3${NC}"
}

print_error() {
    echo -e "${RED}Error: $1${NC}" >&2
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

# Test whether a value is a placeholder (not a real configured value).
# Pre-filled team values (like a real Supabase URL) do NOT match these patterns.
is_placeholder_value() {
    local value="$1"
    if [ -z "$value" ]; then
        return 0
    fi
    # Standard placeholder patterns that indicate an unconfigured value.
    case "$value" in
        your[-_]*)        return 0 ;;
        example*)         return 0 ;;
        CHANGE_ME*)       return 0 ;;
        change[-_]me*)    return 0 ;;
        \<team-*)         return 0 ;;  # angle-bracket template placeholders
        replace[-_]me*)   return 0 ;;
        TODO*)            return 0 ;;
        "")               return 0 ;;
        *)                return 1 ;;
    esac
}

# Parse command line arguments
NON_INTERACTIVE=false
CALLED_FROM_DEV=false
FORCE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --non-interactive|-n)
            NON_INTERACTIVE=true
            shift
            ;;
        --called-from-dev)
            CALLED_FROM_DEV=true
            shift
            ;;
        --force|-f)
            FORCE=true
            shift
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
done

setup_collab_site_packages() {
    "$1" -c "import site; print(site.getsitepackages()[0])" 2>/dev/null || true
}

setup_collab_has_pip_orphans() {
    local site_pkgs="$1"
    local orphan
    if [ -z "$site_pkgs" ] || [ ! -d "$site_pkgs" ]; then
        return 1
    fi
    for orphan in "$site_pkgs"/~ollab* "$site_pkgs"/~collab*; do
        if [ -e "$orphan" ]; then
            return 0
        fi
    done
    return 1
}

setup_collab_remove_pip_orphans() {
    local site_pkgs="$1"
    local orphan
    if [ -z "$site_pkgs" ] || [ ! -d "$site_pkgs" ]; then
        return 0
    fi

    # Remove stale non-editable copy that takes priority over .pth files
    if [ -d "$site_pkgs/collab" ]; then
        echo "   Removing stale non-editable copy: collab/..." >&2
        rm -rf "$site_pkgs/collab" 2>/dev/null || true
    fi

    # Remove pip rename orphans (~ollab_runtime-*.dist-info, ~collab-*.dist-info)
    for orphan in "$site_pkgs"/~ollab* "$site_pkgs"/~collab*; do
        if [ -e "$orphan" ]; then
            echo "   Removing broken pip artifact: $(basename "$orphan")..." >&2
            rm -rf "$orphan" 2>/dev/null || true
        fi
    done
}

setup_collab_install_healthy() {
    local expect_editable="$1"
    local collab_bin="$2"
    local site_pkgs
    site_pkgs=$(setup_collab_site_packages "$VENV_PYTHON")

    if setup_collab_has_pip_orphans "$site_pkgs"; then
        return 1
    fi
    if ! "$VENV_PYTHON" -c "import collab.lock_client" 2>/dev/null; then
        return 1
    fi
    if [ "$expect_editable" = true ]; then
        if ! "$VENV_PYTHON" -c "import json, importlib.metadata; dist = importlib.metadata.distribution('collab-runtime'); data = dist.read_text('direct_url.json'); exit(0 if data and json.loads(data).get('dir_info', {}).get('editable', False) else 1)" 2>/dev/null; then
            return 1
        fi
    fi
    if ! "$VENV_PYTHON" -m pip show collab-runtime >/dev/null 2>&1; then
        return 1
    fi
    if "$VENV_PYTHON" -m pip show collab >/dev/null 2>&1; then
        return 1
    fi
    if [ ! -x "$collab_bin" ] && [ ! -f "$collab_bin" ]; then
        return 1
    fi
    if ! "$collab_bin" --help >/dev/null 2>&1; then
        return 1
    fi
    return 0
}

setup_collab_stop_daemon_for_reinstall() {
    local collab_bin="$1"
    if [ ! -x "$collab_bin" ] && [ ! -f "$collab_bin" ]; then
        return 0
    fi
    echo "   Stopping collab daemon (unlock for package upgrade)..." >&2
    "$collab_bin" daemon-stop >/dev/null 2>&1 || "$VENV_PYTHON" -m collab.lock_client daemon-stop >/dev/null 2>&1 || true
    sleep 1
}

# Only show header if not called from dev script
if [ "$CALLED_FROM_DEV" = false ]; then
    print_banner
fi

print_step 1 10 "Checking prerequisites..."

# Check Python (prefer python3, fallback to python if version output is valid)
PYTHON_CMD=""
PYTHON_VERSION=""

for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        VERSION_OUTPUT=$($candidate --version 2>&1 || true)
        VERSION_VALUE=$(echo "$VERSION_OUTPUT" | awk '{print $2}')
        if echo "$VERSION_VALUE" | grep -Eq '^[0-9]+\.[0-9]+'; then
            PYTHON_CMD="$candidate"
            PYTHON_VERSION="$VERSION_VALUE"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    print_error "Python 3 not found. Please install Python 3.10+ from https://www.python.org"
    exit 1
fi

echo "   Found: Python $PYTHON_VERSION" >&2

MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$MAJOR" -lt 3 ] || ([ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 10 ]); then
    print_error "Python 3.10+ required. Found Python $PYTHON_VERSION"
    exit 1
fi

print_success "Python $PYTHON_VERSION OK"

# Check Git
if ! command -v git &> /dev/null; then
    print_error "Git not found. Please install Git from https://git-scm.com"
    exit 1
fi

GIT_VERSION=$(git --version)
echo "   Found: $GIT_VERSION" >&2
print_success "Git OK"

print_step 2 10 "Setting up virtual environment..."

if [ ! -d ".venv" ]; then
    echo "   Creating ${MAGENTA}.venv${NC}..." >&2
    "$PYTHON_CMD" -m venv .venv
    if [ $? -eq 0 ]; then
        print_success ".venv created"
    else
        print_error "Failed to create virtual environment"
        exit 1
    fi
else
    echo "   Virtual environment already exists" >&2
    print_success ".venv exists"
fi

print_step 3 10 "Installing core dependencies..."

VENV_LAYOUT=""
if [ -f ".venv/bin/python" ] && [ -f ".venv/bin/pip" ]; then
    VENV_PYTHON=".venv/bin/python"
    VENV_PIP=".venv/bin/pip"
    VENV_LAYOUT="posix"
elif [ -f ".venv/Scripts/python.exe" ] && [ -f ".venv/Scripts/pip.exe" ]; then
    VENV_PYTHON=".venv/Scripts/python.exe"
    VENV_PIP=".venv/Scripts/pip.exe"
    VENV_LAYOUT="windows"
else
    print_error "Could not locate virtual environment Python/pip under .venv/bin or .venv/Scripts"
    exit 1
fi

if [ ! -f "$VENV_PIP" ]; then
    print_error "pip not found at $VENV_PIP"
    exit 1
fi

echo "   Ensuring pip is up to date..." >&2
"$VENV_PYTHON" -m pip install --upgrade pip --quiet

if [ -f "requirements.txt" ]; then
    echo "   Installing core dependencies from ${MAGENTA}requirements.txt${NC}..." >&2
    echo ""
    # Quiet installation
    "$VENV_PIP" install -r requirements.txt --quiet --no-warn-script-location >/dev/null 2>&1

    if [ $? -eq 0 ]; then
        print_success "Core dependencies installed"
    else
        print_error "Core dependencies installation failed"
        exit 1
    fi
else
    echo "   ${YELLOW}Warning: requirements.txt not found. Skipping core dependencies.${NC}" >&2
    ((ERROR_COUNT++))
fi

print_step 4 10 "Installing collab package..."

EXPECT_EDITABLE=false
if [ -f "collab/lock_client.py" ]; then
    EXPECT_EDITABLE=true
    PACKAGE_SPEC="-e ."
elif [ -n "$COLLAB_VERSION" ]; then
    PACKAGE_SPEC="collab-runtime==$COLLAB_VERSION"
else
    PACKAGE_SPEC="collab-runtime"
fi

if [ -n "$COLLAB_RUNTIME_SPEC" ]; then
    PACKAGE_SPEC="$COLLAB_RUNTIME_SPEC"
fi

if [ "$VENV_LAYOUT" = "posix" ]; then
    COLLAB_BIN=".venv/bin/collab"
else
    COLLAB_BIN=".venv/Scripts/collab.exe"
fi

SITE_PKGS=$(setup_collab_site_packages "$VENV_PYTHON")
SKIP_COLLAB_REINSTALL=false
if [ "$FORCE" = false ] && [ -z "$COLLAB_RUNTIME_SPEC" ] && [ -z "$COLLAB_VERSION" ]; then
    if setup_collab_install_healthy "$EXPECT_EDITABLE" "$COLLAB_BIN"; then
        SKIP_COLLAB_REINSTALL=true
    fi
fi

if [ "$SKIP_COLLAB_REINSTALL" = true ]; then
    echo "   collab-runtime already installed and healthy (use --force to reinstall)" >&2
    print_success "collab-runtime OK (skipped reinstall)"
else
    setup_collab_stop_daemon_for_reinstall "$COLLAB_BIN"
    setup_collab_remove_pip_orphans "$SITE_PKGS"

    "$VENV_PIP" uninstall collab-runtime -y --quiet 2>/dev/null || true

    if [ -n "$COLLAB_RUNTIME_SPEC" ]; then
        echo "   Installing defined spec: $PACKAGE_SPEC..." >&2
    elif [ "$EXPECT_EDITABLE" = true ]; then
        echo "   Detected collab source repository. Using editable install..." >&2
    elif [ -n "$COLLAB_VERSION" ]; then
        echo "   Installing pinned version: $COLLAB_VERSION..." >&2
    else
        echo "   Installing latest version from registry..." >&2
    fi

    echo "   Checking for conflicting 'collab' package..." >&2
    "$VENV_PIP" uninstall collab -y --quiet 2>/dev/null || true

    if "$VENV_PIP" install $PACKAGE_SPEC --quiet --no-warn-script-location; then
        print_success "collab installed"
    else
        print_error "collab package installation failed"
        ((ERROR_COUNT++))
    fi
fi

print_step 5 10 "Configuring environment..."

if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp ".env.example" ".env"
        echo "   Created ${MAGENTA}.env${NC} from ${MAGENTA}.env.example${NC}" >&2
        print_success ".env created"
    else
        echo "   ${YELLOW}Warning: .env.example not found. You will need to create .env manually.${NC}" >&2
        ((ERROR_COUNT++))
    fi
else
    echo "   ${MAGENTA}.env${NC} already exists" >&2
    print_success ".env exists"
fi

print_step 6 10 "Validating collaborative locking prerequisites..."

echo "   Checking supabase-py import..." >&2
if "$VENV_PYTHON" -c "import supabase" 2>/dev/null; then
    print_success "supabase-py import OK"
else
    echo "   Installing supabase and python-dotenv..." >&2
    if "$VENV_PIP" install supabase python-dotenv --quiet; then
        if "$VENV_PYTHON" -c "import supabase" 2>/dev/null; then
            print_success "supabase-py installed"
        else
            print_error "supabase-py installation verification failed"
            ((ERROR_COUNT++))
        fi
    else
        print_error "Failed to install supabase/python-dotenv"
        ((ERROR_COUNT++))
    fi
fi

ENV_FILE="$PROJECT_ROOT/.env"
if [ -f "$ENV_FILE" ]; then
    HAS_URL=0
    HAS_ANON=0

    SUPABASE_URL_VALUE=$(grep -E '^SUPABASE_URL=' "$ENV_FILE" | head -n 1 | cut -d '=' -f 2-)
    SUPABASE_ANON_VALUE=$(grep -E '^SUPABASE_ANON_KEY=' "$ENV_FILE" | head -n 1 | cut -d '=' -f 2-)

    # Trim leading/trailing whitespace
    SUPABASE_URL_VALUE="$(echo "$SUPABASE_URL_VALUE" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    SUPABASE_ANON_VALUE="$(echo "$SUPABASE_ANON_VALUE" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"

    if [ -n "$SUPABASE_URL_VALUE" ] && ! is_placeholder_value "$SUPABASE_URL_VALUE"; then
        HAS_URL=1
    fi
    if [ -n "$SUPABASE_ANON_VALUE" ] && ! is_placeholder_value "$SUPABASE_ANON_VALUE"; then
        HAS_ANON=1
    fi

    if [ $HAS_URL -eq 1 ] && [ $HAS_ANON -eq 1 ]; then
        echo -e "   SUPABASE_URL: using pre-configured team value ${GREEN}OK${NC}" >&2
    elif [ $HAS_URL -eq 1 ] || [ $HAS_ANON -eq 1 ]; then
        # Partial config
        if [ $HAS_URL -eq 0 ]; then
            echo -e "   ${YELLOW}Warning: SUPABASE_URL is still a placeholder or missing.${NC}" >&2
        fi
        if [ $HAS_ANON -eq 0 ]; then
            echo -e "   ${YELLOW}Warning: SUPABASE_ANON_KEY is still a placeholder or missing.${NC}" >&2
        fi
        if [ "$CALLED_FROM_DEV" = false ]; then
            ((ERROR_COUNT++))
        fi
    else
        echo -e "   ${YELLOW}Warning: .env exists but Supabase values look missing or placeholders.${NC}" >&2
        echo "   Set SUPABASE_URL and SUPABASE_ANON_KEY to real values." >&2
        if [ "$CALLED_FROM_DEV" = false ]; then
            ((ERROR_COUNT++))
        fi
    fi
fi

if command -v pre-commit >/dev/null 2>&1; then
    echo "   Installing git hooks via pre-commit..." >&2
    HOOK_INSTALL_FAILED=0
    for hook_type in pre-commit pre-push commit-msg; do
        if ! pre-commit install --hook-type "$hook_type" --overwrite >/dev/null 2>&1; then
            HOOK_INSTALL_FAILED=1
            break
        fi
    done

    if [ $HOOK_INSTALL_FAILED -eq 0 ]; then
        print_success "Git hooks installed"
        if [ -f "$PROJECT_ROOT/scripts/install_hooks.sh" ]; then
            # Ensure post-merge and post-checkout are also handled by the overlay
            if sh "$PROJECT_ROOT/scripts/install_hooks.sh" >/dev/null 2>&1; then
                print_success "Collab hook overlay installed"
            else
                echo "   ${YELLOW}Warning: collab hook overlay installation failed.${NC}" >&2
                ((ERROR_COUNT++))
            fi
        else
            echo "   ${YELLOW}Warning: scripts/install_hooks.sh not found.${NC}" >&2
            ((ERROR_COUNT++))
        fi
    else
        echo "   ${YELLOW}Warning: pre-commit hook installation failed.${NC}" >&2
        ((ERROR_COUNT++))
    fi
else
    echo "   ${YELLOW}Warning: pre-commit not found. Run ./scripts/setup-dev.sh to install repository hooks.${NC}" >&2
fi

print_step 7 10 "Installing VS Code extension (optional)..."

echo "   Fetching extension from GitHub Releases..." >&2

IDE_COMMANDS=("code" "code-insiders" "cursor" "codium" "antigravity")
CLI_FOUND=false
for ide in "${IDE_COMMANDS[@]}"; do
    if command -v "$ide" >/dev/null 2>&1; then
        CLI_FOUND=true
        break
    fi
done

if [ "$CLI_FOUND" = true ]; then
    TEMP_VSIX="/tmp/collab-locks-latest.vsix"

    if command -v curl >/dev/null 2>&1; then
        VSIX_URL=$(curl -s https://api.github.com/repos/KirilMT/collab/releases/latest | grep "browser_download_url.*vsix" | cut -d '"' -f 4 | head -n 1)

        if [ -n "$VSIX_URL" ]; then
            if curl -sL "$VSIX_URL" -o "$TEMP_VSIX"; then
                for ide in "${IDE_COMMANDS[@]}"; do
                    if command -v "$ide" >/dev/null 2>&1; then
                        echo "   Installing into $ide..." >&2
                        if "$ide" --install-extension "$TEMP_VSIX" --force >/dev/null 2>&1; then
                            print_success "Extension installed for $ide"
                        else
                            echo "   ${YELLOW}Warning: Extension installation failed for $ide.${NC}" >&2
                        fi
                    fi
                done
                rm -f "$TEMP_VSIX"
            else
                echo "   ${YELLOW}Warning: Failed to download .vsix from GitHub release.${NC}" >&2
            fi
        else
            echo "   ${YELLOW}Warning: No .vsix asset found on latest GitHub release.${NC}" >&2
        fi
    else
        echo "   ${YELLOW}Warning: curl is required to download the extension.${NC}" >&2
    fi
else
    echo "   ${YELLOW}No supported IDE CLIs found. Extension must be installed manually:${NC}" >&2
    echo "     1. Open your IDE (VS Code, Cursor, Antigravity)" >&2
    echo "     2. Go to Extensions -> '...' -> 'Install from VSIX'" >&2
fi

print_step 8 10 "Running smoke tests..."

SMOKE_TESTS_PASSED=true

echo "   Testing collab command availability..." >&2
if [ "$VENV_LAYOUT" = "windows" ]; then
    COLLAB_CMD=".venv/Scripts/collab.exe"
else
    COLLAB_CMD=".venv/bin/collab"
fi
if [ -f "$COLLAB_CMD" ]; then
    # Use --help for health check because collab CLI does not expose --version.
    if "$COLLAB_CMD" --help >/dev/null 2>&1; then
        print_success "collab command available"
    else
        echo "   ${YELLOW}Warning: collab command available but failed health check${NC}" >&2
        SMOKE_TESTS_PASSED=false
    fi
else
    echo "   ${YELLOW}Warning: collab command not found${NC}" >&2
    SMOKE_TESTS_PASSED=false
fi

echo "   Validating Supabase configuration..." >&2
if [ -f ".env" ]; then
    SUPABASE_URL_SMOKE=$(grep -E '^SUPABASE_URL=' .env | head -n 1 | cut -d '=' -f 2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    SUPABASE_ANON_SMOKE=$(grep -E '^SUPABASE_ANON_KEY=' .env | head -n 1 | cut -d '=' -f 2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

    if [ -n "$SUPABASE_URL_SMOKE" ] && [ -n "$SUPABASE_ANON_SMOKE" ] && \
       ! is_placeholder_value "$SUPABASE_URL_SMOKE" && \
       ! is_placeholder_value "$SUPABASE_ANON_SMOKE"; then
        print_success "Supabase configuration present"
    else
        echo "   ${YELLOW}Warning: Supabase credentials not set${NC}" >&2
        SMOKE_TESTS_PASSED=false
    fi
fi

if [ "$SMOKE_TESTS_PASSED" = true ]; then
    print_success "All smoke tests passed"
fi

print_step 9 10 "Ensuring Collaborative Daemon is running..."
if [ -f "$VENV_PYTHON" ]; then
    COLLAB_BIN="$(dirname "$VENV_PYTHON")/collab"
    if ! "$COLLAB_BIN" daemon-status >/dev/null 2>&1; then
        echo "   Starting daemon in background..." >&2
        if "$COLLAB_BIN" daemon-start; then
            print_success "Daemon started successfully"
        else
            echo -e "   ${YELLOW}Warning: Failed to start daemon${NC}" >&2
        fi
    else
        print_success "Daemon is already running"
    fi
fi

print_step 10 10 "Final verification..."
"$COLLAB_BIN" daemon-status

if [ "$CALLED_FROM_DEV" = false ]; then
    echo ""
    echo -e "${CYAN}========================================"
    if [ $ERROR_COUNT -eq 0 ]; then
        echo -e "   Installation Complete!"
        echo -e "   ${GRAY}(Production + Daemon Active)${NC}"
    else
        echo -e "   Installation completed with ${YELLOW}$ERROR_COUNT warning(s)${NC}"
    fi
    echo -e "========================================${NC}\n"

    echo ""
    echo -e "${CYAN}================================================================"
    echo -e "                        NEXT STEPS                              "
    echo -e "================================================================${NC}"
    echo ""
    echo -e "${WHITE}  1. Activate the virtual environment:${NC}"
    if [ "$VENV_LAYOUT" = "windows" ]; then
        echo -e "     source .venv/Scripts/activate"
    else
        echo -e "     source .venv/bin/activate"
    fi
    echo ""
    echo -e "${WHITE}  2. Verify collab is installed and working:${NC}"
    echo -e "     collab active"
    echo ""
    echo -e "${WHITE}  3. (Optional) Setup development environment:${NC}"
    echo -e "     ./scripts/setup-dev.sh"
    echo ""
    echo -e "  ${GRAY}Locking works out of the box — no manual Supabase setup needed.${NC}"
    echo -e "  ${GRAY}Force-release via dashboard requires SUPABASE_SERVICE_ROLE_KEY${NC}"
    echo -e "  ${GRAY}in your .env (obtain from a maintainer; never commit it).${NC}"
    echo ""
    echo -e "${CYAN}================================================================${NC}"
    echo ""

    if [ $ERROR_COUNT -ne 0 ]; then
        exit 1
    fi
fi
