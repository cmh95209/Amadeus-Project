#!/usr/bin/env bash
# ============================================================================
#  AMADEUS - ONE-SHOT INSTALLER FOR MAC OS   v1.1  (resumable / safe to re-run)
#  This is the official macOS installer, served from the fork to new Mac
#  installs. (Any local test copy made outside the repo during development
#  is never committed.)
#
#  Installs everything Amadeus needs to run on a Mac:
#    - the app itself (a copy of the project, straight from the fork)
#    - her three Python toolboxes (the app, her long-term memory, her voice)
#    - the voice engine and its voice models
#    - the web page (Node.js)
#
#  Nothing is installed system-wide: everything lands in your home folder,
#  so no administrator password is needed. The one exception: if your Mac
#  has never had Apple's developer tools, macOS itself will ask you to
#  install them (that dialog is normal - just click Install).
#
#  HOW TO RUN:
#     curl -fsSL "<installer URL from the fork>" -o install_macos.sh
#     bash install_macos.sh                 # lands in ~/Amadeus
#     bash install_macos.sh ~/Some/Folder   # or anywhere you like
#
#  WHAT'S NEW IN v1.0 (October 2026) - first Mac release:
#   - Mirrors the Windows installer v4.11 step for step: same toolboxes,
#     same package lists, same voice patches, same pre-downloads.
#   - On Apple Silicon Macs her voice engine uses the chip's built-in
#     accelerator (MPS); on Intel Macs it runs on the CPU.
#   - The package steps talk to each toolbox's own Python directly (the
#     same reliability fix as the Windows v4.11 installer), with the old
#     helper route kept only as a fallback.
#   - If the right PyTorch build is already in the voice toolbox, the
#     multi-GB download is skipped (re-runs move on in seconds).
#
#  WHAT'S NEW IN v1.1 (October 2026) - field fix from the first real-Mac test:
#   - On a brand-new Mac, the 'unzip' tool is often missing the moment this
#     installer installs Apple's developer tools; Git-LFS now falls back to
#     the archive tool built into every Mac (bsdtar).
#   - Every Git-LFS failure now shows its real reason in the install log
#     (plus what the downloaded archive contains) instead of a generic line.
#   - Step 1 now prints the actual chip name (the first version had a typo).
#
#  BEFORE RUNNING, HAVE AT HAND (optional, but makes it faster):
#   - nothing required; the installer fetches everything it needs.
#   - A model for her brain: a cloud service (OpenRouter, OpenAI, ...) or a
#     local model server you already run on this Mac (Ollama, LM Studio,
#     llama.cpp). You connect her to it AFTER install, from her page.
#
#  AFTER IT FINISHES:
#   Double-click  ~/Amadeus/Amadeus-Project/start_macos.command  (or the
#   equivalent in your chosen folder) and follow her introduction message.
# ============================================================================

set -uo pipefail

# ----------------------------------------------------------------------------
#  Pretty output
# ----------------------------------------------------------------------------
if [ -t 1 ]; then
    C_RESET=$'\033[0m'; C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'
    C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'; C_BLU=$'\033[34m'
else
    C_RESET=""; C_DIM=""; C_BOLD=""; C_RED=""; C_GRN=""; C_YEL=""; C_BLU=""
fi

INSTALL_LOG=""   # set once INSTALL_DIR is known; every line is tee'd to it
tee_to() { if [ -n "$INSTALL_LOG" ]; then tee -a "$INSTALL_LOG"; else cat; fi; }

log()  { printf '%s%s%s\n' "$C_DIM" "$1" "$C_RESET" | tee_to; }
step() {
    printf '%s\n' "============================================================" | tee_to
    printf '%s %sSTEP %s: %s%s\n' "$C_BOLD" "$C_BLU" "$1" "$2" "$C_RESET" | tee_to
    printf '%s\n' "============================================================" | tee_to
}
die() {
    printf '%s%s%s\n' "$C_BOLD$C_RED" "STOPPED: $1" "$C_RESET" | tee_to
    if [ -n "${2:-}" ]; then
        printf '%s%s%s\n' "$C_DIM" "$2" "$C_RESET" | tee_to
    fi
    exit 1
}

# ----------------------------------------------------------------------------
#  Configuration (keep in sync with the Windows installer)
# ----------------------------------------------------------------------------
REPO_URL="https://github.com/cmh95209/Amadeus-Project"
BRANCH="feature/character-memory-spike"
GPT_REPO_URL="https://github.com/RVC-Boss/GPT-SoVITS.git"
HF_REPO_URL="https://huggingface.co/lj1995/GPT-SoVITS"

# Miniforge: the community-maintained conda build (no license conditions).
# Stable "latest release" links - they always point at the newest build.
MINIFORGE_URL_ARM="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-arm64.sh"
MINIFORGE_URL_X64="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-x86_64.sh"

# Git-LFS: the voice models in the Hugging Face repo are stored with Git-LFS;
# without it the clone would contain tiny placeholder files instead of the
# real models. Pinned version (the file name carries the version).
GITLFS_VER="v3.8.0"
GITLFS_URL_ARM="https://github.com/git-lfs/git-lfs/releases/download/$GITLFS_VER/git-lfs-darwin-arm64-$GITLFS_VER.zip"
GITLFS_URL_X64="https://github.com/git-lfs/git-lfs/releases/download/$GITLFS_VER/git-lfs-darwin-amd64-$GITLFS_VER.zip"

# Node.js (for the web page). Pinned LTS release; the official tarball is
# extracted into the user's home folder - no admin password needed.
NODE_VER="v24.21.0"
NODE_DIST_BASE="https://nodejs.org/dist/$NODE_VER"

# The Hugging Face file that must be present for her voice to work at all.
HF_MARKER="s2G488k.pth"

# The ~130 MB language-identification model her voice engine uses to tell
# which language a line is in. Pre-downloaded so her very first line never
# waits on a network fetch.
LID_URL="https://storage.googleapis.com/models-research/20221103_fast_langdetect/lid.176.bin"
LID_DEST_REL="GPT_SoVITS/pretrained_models/fast_langdetect"

NLTK_RESOURCES="averaged_perceptron_tagger_eng averaged_perceptron_tagger cmudict"

# ----------------------------------------------------------------------------
#  Machine layout (detected up front)
# ----------------------------------------------------------------------------
ARCH="$(uname -m)"
IS_SILICON=0
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ]; then IS_SILICON=1; fi

# A scratch area that exists on every Mac (set by the system, or /tmp).
TMP="${TMPDIR:-/tmp}"

# Tools the installer may place in the user's home folder (never system-wide).
TOOLS_DIR="$HOME/.amadeus-tools"

# Where Amadeus itself lands (first argument, or the user's Amadeus folder).
INSTALL_DIR="${1:-$HOME/Amadeus}"
PROJ="$INSTALL_DIR/Amadeus-Project"
INSTALL_LOG="$INSTALL_DIR/install_macos.log"

CONDA_HOME=""     # resolved in step 2 (existing conda or the one we install)
CONDA=""          # the conda executable itself
NODE_BIN_DIR=""   # directory holding node+npm (system one or ours)
NPM=""

# ----------------------------------------------------------------------------
#  Helpers
# ----------------------------------------------------------------------------
ensure_tools_dir() { mkdir -p "$TOOLS_DIR" "$TOOLS_DIR/bin"; }

# Create a conda environment if it does not exist (name, python version).
# Mirrors the Windows installer's Ensure-Env: a re-run reuses the toolbox.
ensure_env() {
    local name="$1" py="${2:-3.10}"
    if "$CONDA" env list 2>/dev/null | awk '{print $1}' | grep -qx "$name"; then
        log "  toolbox '$name' already present - reusing it."
    else
        log "  building toolbox '$name' (Python $py) - this can take a minute..."
        if ! "$CONDA" create -n "$name" "python=$py" -y -q; then
            die "Could not build the '$name' toolbox." \
                "See the install log for details: $INSTALL_LOG"
        fi
    fi
}

# Run pip inside a conda environment. Talks to the toolbox's OWN python
# directly (the Windows v4.11 reliability fix); the 'conda run' route is
# kept only as a fallback for machines whose toolboxes live somewhere
# unusual, so a re-run can never be worse than the old installer.
pip_in_env() {
    local name="$1"; shift
    local py="$CONDA_HOME/envs/$name/bin/python"
    if [ -x "$py" ]; then
        "$py" -m pip "$@"
    else
        "$CONDA" run -n "$name" python -m pip "$@"
    fi
}

# Run a python snippet with a given env's python (same direct-first rule).
# Extra arguments after the snippet are passed to the snippet via sys.argv.
run_in_env() {
    local name="$1" code="$2"
    shift 2
    local py="$CONDA_HOME/envs/$name/bin/python"
    if [ -x "$py" ]; then
        "$py" -c "$code" "$@"
    else
        "$CONDA" run -n "$name" python -c "$code" "$@"
    fi
}

# git clone (or refresh) a repo. $1 url  $2 dest  $3 branch ('' = default)
clone_or_update() {
    local url="$1" dest="$2" branch="$3"
    if [ -d "$dest/.git" ]; then
        log "  repo already present at $dest - updating it (best effort)..."
        (
            cd "$dest" || exit 0
            git fetch --quiet origin 2>/dev/null || true
            if [ -n "$branch" ]; then
                git checkout --quiet "$branch" 2>/dev/null || true
                git merge --ff-only --quiet "origin/$branch" 2>/dev/null || true
            else
                git pull --ff-only --quiet 2>/dev/null || true
            fi
        )
    else
        if [ -n "$branch" ]; then
            git clone --quiet --branch "$branch" "$url" "$dest" \
                || die "Could not download the project files from $url." \
                       "Check your internet connection, then re-run the installer."
        else
            git clone --quiet "$url" "$dest" \
                || die "Could not download the project files from $url." \
                       "Check your internet connection, then re-run the installer."
        fi
    fi
}

# ----------------------------------------------------------------------------
#  BANNER
# ----------------------------------------------------------------------------
printf '%s%s%s\n' "$C_BOLD" "============================================================" "$C_RESET" | tee_to
printf '%s AMADEUS INSTALLER for MAC OS  v1.1%s\n' "$C_BOLD" "$C_RESET" | tee_to
printf '%s This installs everything Amadeus needs on this Mac:%s\n' "$C_BOLD" "$C_RESET" | tee_to
printf '%s\n' "   - the app (into: $INSTALL_DIR)" | tee_to
printf '%s\n' "   - three Python toolboxes: the app, her memory, her voice" | tee_to
printf '%s\n' "   - her voice engine + voice models + the web page" | tee_to
printf '%s\n' "  Everything lands in your user folder - no admin password." | tee_to
printf '%s It is safe to re-run this script at any time: finished steps are%s\n' "$C_BOLD" "$C_RESET" | tee_to
printf '%s skipped and missing pieces are repaired (downloads resume).%s\n' "$C_RESET" | tee_to
printf '%s%s%s\n' "$C_BOLD" "============================================================" "$C_RESET" | tee_to

mkdir -p "$INSTALL_DIR"
: > "$INSTALL_LOG"   # fresh log for this run

# ----------------------------------------------------------------------------
#  STEP 1: sanity checks
# ----------------------------------------------------------------------------
step 1 "Checking your Mac"

if [ "$(uname -s)" != "Darwin" ]; then
    die "This installer is for Mac OS (this machine is: $(uname -s))." \
        "Windows uses the other installer (install_windows.ps1)."
fi

MAC_VER="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
MAC_MAJOR="$(printf '%s' "$MAC_VER" | cut -d. -f1)"
log "  macOS version : $MAC_VER"
log "  Chip          : $([ $IS_SILICON = 1 ] && echo "Apple Silicon ($ARCH)" || echo "Intel ($ARCH)")"
if [ "$MAC_MAJOR" -lt 12 ] 2>/dev/null; then
    log "  WARNING: macOS $MAC_VER is older than macOS 12 - Amadeus is known to work best on macOS 12 or newer. Continuing, but you may hit issues."
fi
if [ $IS_SILICON = 1 ]; then
    log "  Voice engine mode: this Mac has an Apple chip - her voice will use its built-in accelerator (MPS)."
else
    log "  Voice engine mode: this is an Intel Mac - her voice will run on the CPU (a little slower than on Apple chips)."
fi

# ----------------------------------------------------------------------------
#  STEP 2: prerequisites (Apple tools, git, conda, node, ffmpeg)
# ----------------------------------------------------------------------------
step 2 "Making sure the basic tools are present"

# --- 2a. Apple's command-line developer tools (provides git + the compiler)
if xcode-select -p >/dev/null 2>&1; then
    log "  Apple developer tools: present."
else
    log "  Apple developer tools are not installed yet - asking macOS for them..."
    log "  A dialog will appear: click 'Install' and wait for it to finish."
    xcode-select --install >/dev/null 2>&1 || true
    waited=0
    while ! xcode-select -p >/dev/null 2>&1; do
        if [ $waited -ge 1800 ]; then
            die "Still waiting for Apple's developer tools." \
                "Finish the 'Install Command Line Developer Tools' dialog, then re-run this installer (everything else is saved and will be skipped)."
        fi
        sleep 5; waited=$((waited + 5))
    done
    log "  Apple developer tools installed - continuing."
fi
if ! command -v git >/dev/null 2>&1; then
    die "git was not found (it comes with Apple's developer tools)." \
        "If you just installed them, open a NEW terminal window and re-run the installer."
fi
log "  git: $(git --version 2>/dev/null | head -1)"

# --- 2b. Git-LFS (the voice models are stored with Git-LFS)
if command -v git-lfs >/dev/null 2>&1; then
    log "  Git-LFS: present."
else
    log "  Installing Git-LFS (needed so the voice models download as real files)..."
    lfs_err="$INSTALL_DIR/git-lfs-err.log"
    : > "$lfs_err"
    lfs_zip="$TOOLS_DIR/git-lfs.zip"
    if command -v brew >/dev/null 2>&1; then
        # Make sure Homebrew's tools folder is on the path for this session.
        case ":$PATH:" in
            *":$(brew --prefix 2>/dev/null)/bin:"*) ;;
            *) export PATH="$(brew --prefix 2>/dev/null)/bin:$PATH" ;;
        esac
        hash -r
        log "  (trying your Homebrew first...)"
        brew install git-lfs >>"$lfs_err" 2>&1 || true
    fi
    if ! command -v git-lfs >/dev/null 2>&1; then
        ensure_tools_dir
        lfs_url=$([ $IS_SILICON = 1 ] && echo "$GITLFS_URL_ARM" || echo "$GITLFS_URL_X64")
        curl -L --fail --progress-bar -o "$lfs_zip" "$lfs_url" \
            || die "Could not download Git-LFS." "Check your internet connection and re-run the installer."
        # A brand-new Mac - one that got Apple's developer tools installed by
        # THIS very installer minutes ago - may not have the 'unzip' tool on
        # its path yet. macOS's built-in bsdtar opens zip files too, so use
        # whichever extractor exists.
        if command -v unzip >/dev/null 2>&1; then
            ( cd "$TOOLS_DIR" && unzip -o -q git-lfs.zip ) >>"$lfs_err" 2>&1
        else
            log "  (this Mac does not offer 'unzip' yet - using the built-in bsdtar...)"
            ( cd "$TOOLS_DIR" && /usr/bin/bsdtar -x -f git-lfs.zip ) >>"$lfs_err" 2>&1
        fi
        chmod +x "$TOOLS_DIR/git-lfs" 2>/dev/null || true
        export PATH="$TOOLS_DIR:$PATH"
        hash -r
    fi
    # Prove the tool actually RUNS (catches a download for the wrong chip
    # type or a broken archive) before declaring success.
    lfs_probe="$(git lfs version 2>&1 | tail -2)"
    if printf '%s' "$lfs_probe" | grep -q "git-lfs/"; then
        git lfs install >/dev/null 2>&1 || true
        rm -f "$lfs_zip"
        log "  Git-LFS ready."
    else
        if [ -s "$lfs_err" ]; then
            log "  (what the setup attempts reported:)"
            tail -8 "$lfs_err" | tee_to
        fi
        if [ -n "$lfs_probe" ]; then
            log "  (asking the tool to identify itself said: $lfs_probe)"
        fi
        if [ -f "$lfs_zip" ]; then
            log "  (the downloaded archive contains:)"
            /usr/bin/bsdtar -tf "$lfs_zip" 2>/dev/null | tee_to || true
        fi
        die "Could not set up Git-LFS (the details above explain why)." \
            "Re-run the installer - it retries just this step. If it fails again, send the install log."
    fi
fi

# --- 2c. Conda (the manager that builds her Python toolboxes).
#         Reuses an existing Miniconda/Anaconda/Miniforge if present.
conda_candidates=(
    "$HOME/miniforge3"
    "$HOME/miniconda3"
    "$HOME/anaconda3"
    "$HOME/opt/anaconda3"
    "/opt/miniforge3"
    "/opt/miniconda3"
    "/opt/anaconda3"
    "/opt/homebrew/Caskroom/miniforge/base"
    "/opt/homebrew/Caskroom/miniconda/base"
)
CONDA_HOME=""
for cand in "${conda_candidates[@]}"; do
    if [ -x "$cand/bin/conda" ]; then CONDA_HOME="$cand"; break; fi
done
CONDA_INSTALLED_NEW=0
if [ -n "$CONDA_HOME" ]; then
    log "  Conda: found your existing install at $CONDA_HOME - reusing it."
else
    log "  Conda not found - installing Miniforge (the community conda build, ~85 MB)..."
    mf_url=$([ $IS_SILICON = 1 ] && echo "$MINIFORGE_URL_ARM" || echo "$MINIFORGE_URL_X64")
    curl -L --fail --progress-bar -o "$TMP/miniforge-setup.sh" "$mf_url" \
        || die "Could not download Miniforge." "Check your internet connection and re-run the installer."
    if ! bash "$TMP/miniforge-setup.sh" -b -p "$HOME/miniforge3" > "$INSTALL_LOG.tmp" 2>&1; then
        tail -5 "$INSTALL_LOG.tmp" | tee_to
        die "The Miniforge install step failed." "See: $INSTALL_LOG.tmp - then re-run this installer."
    fi
    rm -f "$TMP/miniforge-setup.sh"
    CONDA_HOME="$HOME/miniforge3"
    CONDA_INSTALLED_NEW=1
    log "  Miniforge installed at $CONDA_HOME."
fi
CONDA="$CONDA_HOME/bin/conda"
# shellcheck disable=SC1091
[ -f "$CONDA_HOME/etc/profile.d/conda.sh" ] && . "$CONDA_HOME/etc/profile.d/conda.sh"
# The license-acceptance dance the Windows installer does (a no-op on
# community builds); keeping it so behavior is identical across platforms.
"$CONDA" tos accept --override-channels \
    --channel https://repo.anaconda.com/pkgs/main \
    --channel https://repo.anaconda.com/pkgs/r --all >/dev/null 2>&1 || true
"$CONDA" config --set notify_outdated_conda false >/dev/null 2>&1 || true
if [ $CONDA_INSTALLED_NEW = 1 ]; then
    # Also register conda with your terminal (zsh) so you can use it directly.
    # Undo anytime with: conda init --reverse
    "$CONDA" init zsh >/dev/null 2>&1 || true
    log "  (Conda was also added to your terminal's startup file - undo with 'conda init --reverse'.)"
fi

# --- 2d. Node.js (runs the web page). Prefers what you already have.
node_ok() {
    command -v node >/dev/null 2>&1 || return 1
    local major
    major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
    [ "${major:-0}" -ge 18 ] 2>/dev/null
}
if node_ok; then
    NODE_BIN_DIR="$(dirname "$(command -v node)")"
    log "  Node.js: found yours ($(node --version)) - reusing it."
elif command -v brew >/dev/null 2>&1; then
    log "  Node.js not found - installing it with Homebrew..."
    brew install node >/dev/null 2>&1 || true
    hash -r
    if node_ok; then
        NODE_BIN_DIR="$(dirname "$(command -v node)")"
        log "  Node.js ready ($(node --version))."
    fi
fi
if ! node_ok; then
    log "  Node.js not found - installing the official build for this Mac (no admin password needed)..."
    ensure_tools_dir
    node_arch=$([ $IS_SILICON = 1 ] && echo "darwin-arm64" || echo "darwin-x64")
    curl -L --fail --progress-bar -o "$TOOLS_DIR/node.tar.gz" \
        "$NODE_DIST_BASE/node-$NODE_VER-$node_arch.tar.gz" \
        || die "Could not download Node.js." "Check your internet connection and re-run the installer."
    rm -rf "$TOOLS_DIR/node"
    mkdir -p "$TOOLS_DIR/node"
    tar -xzf "$TOOLS_DIR/node.tar.gz" -C "$TOOLS_DIR/node" --strip-components=1
    rm -f "$TOOLS_DIR/node.tar.gz"
    export PATH="$TOOLS_DIR/node/bin:$PATH"
    hash -r
    NODE_BIN_DIR="$TOOLS_DIR/node/bin"
    log "  Node.js ready ($(node --version), in your home folder)."
fi
NPM="$NODE_BIN_DIR/npm"

# --- 2e. FFmpeg (used by the voice engine for some audio conversions).
if command -v ffmpeg >/dev/null 2>&1; then
    log "  FFmpeg: present."
elif command -v brew >/dev/null 2>&1; then
    log "  FFmpeg not found - installing it with Homebrew..."
    brew install ffmpeg >/dev/null 2>&1 || true
    hash -r
    if command -v ffmpeg >/dev/null 2>&1; then
        log "  FFmpeg ready."
    else
        log "  WARNING: FFmpeg could not be installed - her voice may not work for some sounds. (brew install ffmpeg fixes this.)"
    fi
else
    log "  WARNING: FFmpeg is not installed (and Homebrew is not available either)."
    log "  Her voice engine works without it for normal chat, but some sounds may fail."
    log "  If that bothers you: install Homebrew (brew.sh) and run: brew install ffmpeg"
fi

# ----------------------------------------------------------------------------
#  STEP 3: the app itself (from the fork, on the feature branch)
# ----------------------------------------------------------------------------
step 3 "Getting Amadeus (the project files)"

if [ -d "$PROJ/.git" ]; then
    log "  Found an existing Amadeus at $PROJ - updating it..."
    if ! git -C "$PROJ" checkout --quiet "$BRANCH" 2>/dev/null; then
        log "  (note: the local copy is not on the expected branch - continuing on what it has)"
    fi
    # Personal live-value files (her saved connection, personality) are
    # excluded from the update - the same rule as the Windows installer.
    git -C "$PROJ" stash push -m "amadeus-installer-personal" \
        -- backend/data/llm_model.txt backend/data/llm_server.txt \
           backend/data/personality.txt backend/data/personality_ja.txt \
        >/dev/null 2>&1 || true
    git -C "$PROJ" fetch --quiet origin \
        || die "Could not reach the project repository." \
               "Check your internet connection, then re-run the installer."
    if ! git -C "$PROJ" merge --ff-only --quiet "origin/$BRANCH"; then
        die "Your existing Amadeus copy has diverged and cannot be updated automatically." \
            "Easiest fix: delete the folder $PROJ and re-run this installer (a fresh copy is created)."
    fi
    git -C "$PROJ" stash pop >/dev/null 2>&1 || true
    log "  Updated to the latest version on the $BRANCH branch."
else
    log "  Downloading the project (a few dozen MB)..."
    clone_or_update "$REPO_URL" "$PROJ" "$BRANCH"
    log "  Project files ready."
fi

# ----------------------------------------------------------------------------
#  STEP 4: the voice engine (GPT-SoVITS) + her voice patches
# ----------------------------------------------------------------------------
step 4 "Setting up the voice engine"

GPT="$PROJ/GPT-SoVITS"
clone_or_update "$GPT_REPO_URL" "$GPT" ""

VOICE_PATCH="$PROJ/scripts/voice-patch"
if [ -d "$VOICE_PATCH" ]; then
    # The patch files mirror a setup proven in daily use: a prebuilt/optional
    # Japanese text helper and pure-python text segmentation, so the Mac
    # never needs a C compiler for them. The copy preserves the folder
    # structure (GPT_SoVITS/text/*.py + requirements.txt).
    cp -R "$VOICE_PATCH/." "$GPT/"
    log "  Voice patches applied (no C compiler needed for them)."
else
    die "The voice patch files are missing from the project." \
        "Re-run the installer to get a fresh copy, or report this."
fi

# ----------------------------------------------------------------------------
#  STEP 5: the app's Python toolbox
# ----------------------------------------------------------------------------
step 5 "Setting up Amadeus's Python toolboxes"

ensure_env "amadeus"
# The memory sidecar's package list needs a newer Python (3.12+) than the
# app environment (3.10), so it gets its own toolbox. The launcher finds it
# on first start and builds the sidecar from it.
ensure_env "amadeus-cm" "3.13"

pip_in_env "amadeus" install --quiet --upgrade pip >/dev/null 2>&1 || true
# ddgs: the DuckDuckGo search library behind her web search (weather is
# separate - it uses a keyless Open-Meteo feed and needs no library).
pip_in_env "amadeus" install Flask flask-cors requests tqdm langchain langchain-openai pydantic ddgs \
    || die "Could not install the app's packages." "See the install log for details: $INSTALL_LOG"

# Smoke test: the whole backend must import cleanly in this toolbox.
# ddgs is imported lazily by the backend (web search), so also check it
# explicitly: a machine that lost the search library must never hide
# behind a passing smoke test.
smoke="$(cd "$PROJ/backend" && run_in_env "amadeus" "import api; import ddgs; print('SMOKE_OK')" 2>&1 | tail -1)"
if [ "${smoke}" = "SMOKE_OK" ]; then
    log "  App toolbox ready (startup smoke test passed)."
else
    log "  WARNING: the startup smoke test did not pass. If Amadeus fails to start later, send the log."
    printf '%s%s%s\n' "$C_DIM" "$smoke" "$C_RESET" | tee_to
fi

# ----------------------------------------------------------------------------
#  STEP 6: the voice toolbox (PyTorch first, then the rest)
# ----------------------------------------------------------------------------
step 6 "Setting up the voice engine's toolbox (big download, be patient)"

ensure_env "GPTSoVits"
PIP_LOG="$INSTALL_DIR/voice_pip.log"

# --- 6a. PyTorch FIRST, with the build that matches this Mac's hardware.
#         Mac builds come from the normal package index (no special address
#         needed, unlike Windows): Apple chips get the MPS-accelerated build,
#         Intel Macs get a CPU build.
# One fast local check (no download): what PyTorch build is already in the
# toolbox, and can it use this Mac's chip? If so, the multi-GB download is
# skipped entirely - a re-run moves on in seconds.
torch_probe='
import torch
try:
    mps = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.enabled)
except Exception:
    mps = False
print("T|" + torch.__version__ + "|" + ("mps" if mps else "cpu") + "|" + str(mps))
'
torch_info="$(run_in_env "GPTSoVits" "$torch_probe" 2>/dev/null | tail -1)"
torch_ready=0
case "$torch_info" in
    T\|*)
        tp_mode="$(printf '%s' "$torch_info" | cut -d'|' -f3)"
        tp_can="$(printf '%s' "$torch_info" | cut -d'|' -f4)"
        if [ $IS_SILICON = 1 ]; then
            [ "$tp_mode" = "mps" ] && [ "$tp_can" = "True" ] && torch_ready=1
        else
            torch_ready=1   # any torch works on Intel Macs (CPU mode)
        fi
        ;;
esac

if [ $torch_ready = 1 ]; then
    log "  PyTorch $(printf '%s' "$torch_info" | cut -d'|' -f2) is already in place for this Mac - skipping the download."
else
    if [ $IS_SILICON = 1 ]; then
        log "  Installing PyTorch (Apple-chip build, uses the MPS accelerator) - the biggest download..."
    else
        log "  Installing PyTorch (Intel/CPU build) - the biggest download..."
    fi
    log "  (a couple of GB - this is normal; keep the window open)"
    pip_in_env "GPTSoVits" uninstall -y torch torchvision torchaudio >/dev/null 2>&1 || true
    if ! pip_in_env "GPTSoVits" install torch torchvision torchaudio 2>&1 | tee "$PIP_LOG" >/dev/null; then
        tail -15 "$PIP_LOG" | tee_to
        die "Could not install PyTorch." "See: $PIP_LOG"
    fi
    log "  PyTorch installed."
fi

# --- 6b. The rest of the voice engine's packages (from its list, already
#         carrying Amadeus' voice patches).
log "  Installing the voice engine's remaining packages..."
if ! pip_in_env "GPTSoVits" install -r "$GPT/extra-req.txt" --no-deps 2>&1 | tee -a "$PIP_LOG" >/dev/null; then
    log "  WARNING: 'extra-req' install had issues - continuing (see $PIP_LOG)."
fi
if ! pip_in_env "GPTSoVits" install -r "$GPT/requirements.txt" 2>&1 | tee -a "$PIP_LOG" >/dev/null; then
    # Fallback: retry without the prebuilt Japanese text helper. Her voice
    # then runs without that one component (it is optional by design) -
    # better than a voice engine that never starts.
    log "  (full package list failed - retrying without the optional Japanese text helper)"
    grep -v '^pyopenjtalk-prebuilt' "$GPT/requirements.txt" > "$GPT/requirements-core.tmp.txt"
    pip_in_env "GPTSoVits" install -r "$GPT/requirements-core.tmp.txt" 2>&1 | tee -a "$PIP_LOG" >/dev/null \
        || { tail -15 "$PIP_LOG" | tee_to; die "Could not install the voice engine's packages." "See: $PIP_LOG"; }
fi

# Note: on a Mac, ONE package (python_mecab_ko, for Korean) is built from
# source and takes a few minutes the first time - normal, not a hang.

# ----------------------------------------------------------------------------
#  STEP 7: check the voice engine can use this Mac's chip
# ----------------------------------------------------------------------------
step 7 "Checking the voice engine's hardware support"

torch_check="$(run_in_env "GPTSoVits" "$torch_probe" 2>/dev/null | tail -1)"
case "$torch_check" in
    T\|*)
        tcm="$(printf '%s' "$torch_check" | cut -d'|' -f3)"
        tcanc="$(printf '%s' "$torch_check" | cut -d'|' -f4)"
        tcv="$(printf '%s' "$torch_check" | cut -d'|' -f2)"
        if [ "$tcm" = "mps" ] && [ "$tcanc" = "True" ]; then
            log "  Voice hardware: Apple-chip accelerator (MPS) - her voice will run at full speed."
        else
            log "  Voice hardware: CPU mode (PyTorch $tcv). On this Mac her voice works but takes a few extra seconds per line."
        fi
        ;;
    *)
        log "  WARNING: PyTorch could not be loaded in the voice toolbox - her voice will not work until this is fixed. Send the log."
        ;;
esac

# ----------------------------------------------------------------------------
#  STEP 8: her voice models + pre-downloads
# ----------------------------------------------------------------------------
step 8 "Downloading her voice models (a few GB the first time)"

GPT_PKG="$GPT/GPT_SoVITS"
PRETRAINED="$GPT_PKG/pretrained_models"
mkdir -p "$PRETRAINED"

hf_cache="$TMP/GPT-SoVITS-hf"
if [ -f "$hf_cache/$HF_MARKER" ]; then
    log "  Voice model files already downloaded - reusing them."
else
    rm -rf "$hf_cache"
    log "  Downloading the voice models from the Hugging Face mirror..."
    log "  (several GB on the first run - this is normal; keep the window open)"
    if ! git clone --depth 1 --quiet "$HF_REPO_URL" "$hf_cache" 2>>"$PIP_LOG"; then
        die "Could not download the voice models." \
            "Check your internet connection (or your connection to huggingface.co), then re-run the installer."
    fi
    # Git-LFS guard: if the clone came back as tiny placeholder stubs
    # instead of real files, fetch the real files now.
    if head -c 200 "$hf_cache/$HF_MARKER" 2>/dev/null | grep -q "git-lfs"; then
        log "  (the models arrived as Git-LFS placeholders - fetching the real files now...)"
        ( cd "$hf_cache" && git lfs pull 2>>"$PIP_LOG" ) \
            || die "Could not fetch the voice model files (Git-LFS step failed)." "Re-run the installer."
    fi
    hf_size="$(stat -f %z "$hf_cache/$HF_MARKER" 2>/dev/null || echo 0)"
    if [ ! -f "$hf_cache/$HF_MARKER" ] || [ "$hf_size" -lt 1000000 ] 2>/dev/null; then
        die "The voice models do not look complete." "Re-run the installer; if it fails again, send the log."
    fi
fi
# Mirror the downloaded models into the voice engine's expected folder.
# (The cache outside the app folder means a reinstall of the app does not
#  force a re-download of several GB.)
rsync -a --exclude .git "$hf_cache/" "$PRETRAINED/" >/dev/null 2>&1 \
    || cp -R "$hf_cache/." "$PRETRAINED/"
log "  Voice models are in place."

# Pre-download the ~130 MB language-identification model her voice engine
# uses to tell which language a line is in. (The engine's own downloader
# refuses to run without its cache directory; her startup code creates it,
# but downloading HERE means her very first line never waits on the network.)
LID_FILE="$PRETRAINED/$LID_DEST_REL/lid.176.bin"
mkdir -p "$PRETRAINED/$LID_DEST_REL"
if [ -f "$LID_FILE" ]; then
    log "  Voice language model already present."
else
    log "  Pre-downloading the voice language model (~130 MB)..."
    lid_code='
import socket, sys, urllib.request
socket.setdefaulttimeout(300)
dest, url = sys.argv[1], sys.argv[2]
req = urllib.request.Request(url, headers={"User-Agent": "amadeus-installer"})
with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as out:
    while True:
        chunk = resp.read(1048576)
        if not chunk:
            break
        out.write(chunk)
'
    if run_in_env "GPTSoVits" "$lid_code" "$LID_FILE" "$LID_URL" >/dev/null 2>&1 && [ -s "$LID_FILE" ]; then
        log "  Voice language model pre-downloaded."
    else
        log "  WARNING: could not pre-download the voice language model right now."
        log "  (If she is offline at first start, her voice will retry the download automatically.)"
    fi
fi

# Pre-fetch the Western-script text data her voice engine needs to pronounce
# a name the user typed, "AI", ... (the same self-heal the Windows
# installer performs; here it runs through the toolbox's OWN python with a
# bounded timeout - the v4.11 reliability pattern).
NLTK_DIR="$GPT/runtime/nltk_data"
nltk_code='
import os, socket, sys
socket.setdefaulttimeout(120)
os.environ.setdefault("NLTK_DATA", sys.argv[1])
os.makedirs(os.environ["NLTK_DATA"], exist_ok=True)
import nltk
ok = True
for res in sys.argv[2:]:
    try:
        nltk.data.find(res)
    except LookupError:
        try:
            nltk.download(res, quiet=True, download_dir=os.environ["NLTK_DATA"])
        except Exception:
            ok = False
print("NLTK_DATA_OK" if ok else "NLTK_DATA_PARTIAL")
'
# Note: $NLTK_RESOURCES is intentionally unquoted - it must arrive as
# separate argv items (one per resource).
# shellcheck disable=SC2086
nltk_out="$(run_in_env "GPTSoVits" "$nltk_code" "$NLTK_DIR" $NLTK_RESOURCES 2>/dev/null | tail -1)"
case "$nltk_out" in
    NLTK_DATA_OK)      log "  Voice text data for Western-script words is in place." ;;
    NLTK_DATA_PARTIAL) log "  (some Western-script text data could not be fetched right now - she will retry at first use.)" ;;
    *)                 log "  (the Western-script text data check was skipped - if a line with a Western word is later unvoiced, it self-heals at next start.)" ;;
esac

# ----------------------------------------------------------------------------
#  STEP 9: the web page (Node.js packages)
# ----------------------------------------------------------------------------
step 9 "Preparing the web page"

if [ -d "$PROJ/frontend/node_modules" ]; then
    log "  Web page already prepared - skipping."
else
    log "  Downloading the web page's packages..."
    if ( cd "$PROJ/frontend" && PATH="$NODE_BIN_DIR:$PATH" "$NPM" install --no-audit --no-fund ); then
        log "  Web page ready."
    else
        log "  WARNING: the web page's package download did not finish."
        log "  (If the page does not open later, re-run this installer - it resumes where it stopped.)"
    fi
fi

# ----------------------------------------------------------------------------
#  STEP 10: done
# ----------------------------------------------------------------------------
step 10 "All done"

printf '%s\n' "" | tee_to
printf '%s%s AMADEUS IS READY! %s%s\n' "$C_BOLD" "$C_GRN" "$C_BOLD" "$C_RESET" | tee_to
printf '%s\n' "" | tee_to
log "  Here is what to do next:"
log "  1) Double-click this file to START Amadeus:"
log "       $PROJ/start_macos.command"
log "  2) When she opens in your browser, read her introduction message -"
log "     it walks you through connecting your model, step by step."
log "  3) (Shortcut) You can also open Settings -> Connection and click"
log "     'Test connection' - the dot turns green when she can reach"
log "     your model. Pick your model from the chips (or type its name)"
log "     and paste its API key, if your provider has one."
log "     On a Mac, good choices: a cloud service (OpenRouter, OpenAI...)"
log "     or a local model server like Ollama, LM Studio or llama.cpp."
log ""
log "  Note: her long-term memory is set up on the very first start (a few"
log "  minutes, one time - she works fine while it finishes in the background)."
log ""
log "(Install log saved to: $INSTALL_LOG)"

printf '%s\n' "" | tee_to
read -r -p "Press Enter to close this window" _ || true
