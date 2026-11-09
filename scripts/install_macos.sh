#!/usr/bin/env bash
# ============================================================================
#  AMADEUS - ONE-SHOT INSTALLER FOR MAC OS   v1.7  (resumable / safe to re-run)
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
#  TEST OPTIONS (for the project's own test installs; nobody else needs
#  them - they change only where the code comes from, never the steps):
#     --branch NAME           install that branch of the fork (default: main)
#     --simulate-ram-gb N     pretend this Mac has N GB of memory
#     --simulate-chip apple|intel   pretend the chip
#     --hardware-check-only [--json]   only show which model this Mac gets
#     --skip-llm-download     all screens, but no model downloads
#     --llm-screens-only      ONLY the model screens + its settings file
#
#  WHAT'S NEW IN v1.0 (October 2026) - first Mac release:
#   - Mirrors the Windows installer v4.12 step for step: same toolboxes,
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
#  WHAT'S NEW IN v1.7 (November 2026, app v3.0.3):
#   - Her voice models: only the files she uses are downloaded (about
#     1.2 GB, each checked against its official size and fingerprint) -
#     the same as on Windows. The old step copied the voice engine's whole
#     model page (every engine version) through the temp folder, which
#     needed ~13 GB there and stopped with "Could not download the voice
#     models" on PCs where that folder is small.
#
#  WHAT'S NEW IN v1.6 (November 2026, app v3.0):
#   - On an update that brings new versions of her personality and voice
#     rules (and yours differ from them), a second question: Y replaces
#     them with the new defaults after saving yours in
#     Amadeus-Project/backups/<date_time>/; N keeps yours (Settings still
#     offers the new default with one click). The same as on Windows.
#   - Her own built-in model on Macs with an Apple chip (M1 or newer, 16 GB
#     of memory or more), the same as on Windows: the first question is
#     how she should think - A) her own local model, set up automatically
#     (Gemma 4 12B + its vision file, run by llama.cpp's Metal build), or
#     B) your own model server or a cloud service. The model downloads in
#     the background while everything else installs; every file is checked
#     against its official size and fingerprint. Intel Macs and Macs with
#     less memory get option B.
#
#  WHAT'S NEW IN v1.5 (November 2026, app v3.0):
#   - Videos: FFmpeg (the video tool) is set up in your home folder when
#     this Mac has none - a signed, fingerprint-checked download, or
#     conda-forge's build when that server is down. Never stops the install.
#   - The speech model for videos is downloaded during the install.
#
#  WHAT'S NEW IN v1.4 (October 2026):
#   - Her memory (about 1.3 GB) is set up during the install, in step 5 -
#     her first start no longer begins with that download.
#   - The app's logs are in a normal "logs" folder inside the project
#     folder (Finder hides the old .runtime folder).
#
#  WHAT'S NEW IN v1.3.1 (October 2026, app v2.1.1):
#   - The voice engine (GPT-SoVITS) is pinned to the version Amadeus is
#     tested with (its October 2026 update made her voice take minutes per
#     line). An install that already got it is moved back on the next run.
#
#  WHAT'S NEW IN v1.3 (October 2026, app v2.1):
#   - Installs and updates the fork's main branch (it used to follow a
#     feature branch); an older install is moved to main automatically when
#     it holds nothing of your own. Your saved settings are protected during
#     the update (the old step listed a wrong file path and was skipped).
#   - Installs e2k, the small offline model her voice uses to say English
#     words the Japanese way.
#   - An existing install updates even though the project's published
#     history was cleaned up once (it used to stop with "has diverged"):
#     a copy holding nothing of your own is moved onto the new history.
#
#  WHAT'S NEW IN v1.2 (October 2026):
#   - The FFmpeg check no longer tries to install it or suggests Homebrew:
#     a code-level check of the voice engine proved Amadeus serves her voice
#     as WAV and never needs FFmpeg, so the check is now informational only.
#     (This also saves a few minutes on Macs that have Homebrew.)
#
#  BEFORE RUNNING, HAVE AT HAND (optional, but makes it faster):
#   - nothing required; the installer fetches everything it needs.
#   - Option A (Apple chip, 16 GB+): about 9 GB of free disk for her model.
#   - Option B: a cloud service (OpenRouter, OpenAI, ...) or a local model
#     server you already run on this Mac (Ollama, LM Studio, llama.cpp).
#     You connect her to it AFTER install, from her page.
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
# Since v2.1 every install follows main; installs from older installers sit
# on the old feature branch and are moved to main in step 3 (when they hold
# nothing of the user's own).
BRANCH="main"
INSTALLER_VERSION="v1.7"
LEGACY_BRANCH="feature/character-memory-spike"
GPT_REPO_URL="https://github.com/RVC-Boss/GPT-SoVITS.git"
# Her voice's model files: only the 8 she loads (about 1.2 GB), pinned to
# one version of the voice engine's model page, each checked against its
# published size + SHA-256 - the same list as the Windows installer. (The
# old step cloned the whole page - every engine version - which needed
# ~13 GB in the temp folder; on many Linux PCs /tmp lives in memory and is
# far smaller, so the download stopped half-way. Tester report 2026-11-09.)
VOICE_REPO_URL="https://huggingface.co/lj1995/GPT-SoVITS/resolve/8f8857563c040d542de108a2c18b0425abd6c9c5"
# One "path bytes sha256" per line.
VOICE_FILES="chinese-roberta-wwm-ext-large/config.json 963 3d57de2fd7e80d0e5c8ff194f0bbb6baa10df7e43fc262a0cc71298a78b0a3e5
chinese-roberta-wwm-ext-large/tokenizer.json 268962 173796956820ea27bd14f76bf28162607ff4254807e2948253eb5b46f5bb643b
chinese-roberta-wwm-ext-large/pytorch_model.bin 651225145 e53a693acc59ace251d143d068096ae0d7b79e4b1b503fa84c9dcf576448c1d8
chinese-hubert-base/config.json 1449 c3e5060a1277e0f078cc6be9da4528a605dba6ece93018981fe2c820e5c7b103
chinese-hubert-base/preprocessor_config.json 212 dcd684124d06722947939d41ea6ae58dbf10968c60a11a29f23ddc602c64a29b
chinese-hubert-base/pytorch_model.bin 188811417 24164f129c66499d1346e2aa55f183250c223161ec2770c0da3d3b08cf432d3c
gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt 155315150 732f94e63b148066e24c7f9d2637f3374083e637635f07fbdb695dee20ddbe1f
gsv-v2final-pretrained/s2G2333k.pth 106035259 924fdccaa3c574bf139c25c9759aa1ed3b3f99e19a7c529ee996c2bc17663695"

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

# The ~130 MB language-identification model her voice engine uses to tell
# which language a line is in. Pre-downloaded so her very first line never
# waits on a network fetch.
# (The same address as the Windows installer: the old Google Storage copy
# answers 404 since November 2026.)
LID_URL="https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin"
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

# Where Amadeus itself lands (the first argument that is not an option, or
# the user's Amadeus folder), plus the test options (see the top).
INSTALL_DIR=""
SIM_RAM_GB=-1
SIM_CHIP=""
HARDWARE_CHECK_ONLY=0
JSON_OUT=0
SKIP_LLM_DOWNLOAD=0
LLM_SCREENS_ONLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --branch)          BRANCH="${2:-}"; shift; [ $# -gt 0 ] && shift ;;
        --simulate-ram-gb) SIM_RAM_GB="${2:-}"; shift; [ $# -gt 0 ] && shift ;;
        --simulate-chip)   SIM_CHIP="${2:-}"; shift; [ $# -gt 0 ] && shift ;;
        --hardware-check-only) HARDWARE_CHECK_ONLY=1; shift ;;
        --json)            JSON_OUT=1; shift ;;
        --skip-llm-download) SKIP_LLM_DOWNLOAD=1; shift ;;
        --llm-screens-only) LLM_SCREENS_ONLY=1; SKIP_LLM_DOWNLOAD=1; shift ;;
        --*) printf 'Unknown option: %s\n' "$1"; exit 2 ;;
        *)   INSTALL_DIR="$1"; shift ;;
    esac
done
case "$BRANCH" in ''|-*|*[!A-Za-z0-9._/-]*) printf 'Invalid --branch: %s\n' "$BRANCH"; exit 2 ;; esac
case "$SIM_RAM_GB" in -1|[0-9]|[0-9][0-9]|[0-9][0-9][0-9]) ;; *) printf 'Invalid --simulate-ram-gb: %s\n' "$SIM_RAM_GB"; exit 2 ;; esac
case "$SIM_CHIP" in ''|apple|intel) ;; *) printf 'Invalid --simulate-chip: %s (apple or intel)\n' "$SIM_CHIP"; exit 2 ;; esac
INSTALL_DIR="${INSTALL_DIR:-$HOME/Amadeus}"
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

# >>> BUILT-IN MODEL FUNCTIONS (tests/test_installer_macos_llm.py runs them)
# ============================================================================
#  Her built-in local model (option A) - the same design as the Windows
#  installer (v4.13): the A/B question first, a look at this Mac, background
#  downloads, every file checked (size + SHA-256), llama.cpp's own start
#  test, and the app's settings file backend/data/local_llm.json.
#  Written for the bash every Mac has (3.2): no newer-only features.
# ============================================================================
# >>> MAC TIER TABLE (copy of the Mac parts of scripts/local_llm_tiers.json -
#     tests/test_llm_tiers.py keeps the two equal)
LLAMA_RELEASE="b11400"
MAC_BUILD="metal-arm64"
MAC_MIN_RAM_GB=16
DISK_HEADROOM_GB=2
# llama.cpp for Apple chips (Metal): name bytes sha256
MAC_BUILD_FILES="llama-b11400-bin-macos-arm64.tar.gz 11920419 91effd009b38c8f3feb60c2256ff10e8b560f0b40915e104ad23d3daa469e9b1"
# id min_ram_gb model context kv_cache history_default memory_need_gb
MAC_TIERS="mac-16 16 12B-Q3 16384 q4_0 6000 8.5
mac-24 24 12B 24576 q8_0 14000 11.6
mac-32 32 12B 32768 f16 22000 13.0
mac-48 48 12B 49152 f16 32000 13.5"
# key|label (the Mac table's own label where it has one)|repo|revision|file|bytes|sha256|vision file|vision saved as|vision bytes|vision sha256
MAC_MODELS="12B|Gemma 4 12B (QAT)|unsloth/gemma-4-12b-it-qat-GGUF|980b060c40a8539ac159e0501a3e0f66a6365af3|gemma-4-12B-it-qat-UD-Q4_K_XL.gguf|6716356800|90fd44e29e0d7cffeb0fd00dc73cfdab9ed0b0e95306ecf7821ea634c940c370|mmproj-F16.gguf|gemma-4-12B-it-qat-mmproj-F16.gguf|175115840|ecc4e93128da8363b7dbf2193eab98cf1142353f52ceaa0c95c0872997aaadd3
12B-Q3|Gemma 4 12B (3-bit)|unsloth/gemma-4-12b-it-GGUF|fc034cfff751157913579611efad8462ac1be606|gemma-4-12b-it-Q3_K_M.gguf|5693872960|dbf58f4c413b82b27106867eb42f7087e4172a172f6f201e2e11598a6582e504|mmproj-F16.gguf|gemma-4-12b-it-mmproj-F16.gguf|175115840|91f086971e56d7a7d8d39e271873fccdb49541bd259d6e02c401a4f1cb7a219e"
# <<< MAC TIER TABLE

# The model and llama.cpp live NEXT TO the project folder, not in it (the
# same reason as on Windows: a broken project folder may be deleted and
# downloaded again, which must never take a 6-7 GB model with it).
MODELS_DIR="$INSTALL_DIR/models"
LLM_CHOICE_FILE="$MODELS_DIR/local_llm_choice.json"
DOWNLOAD_RETRY_DELAY=15
LLM_PORT=8091

# Field N (1 = key) of one model's table line, e.g. model_field 12B 2 = label.
model_field() {
    local line
    line="$(printf '%s\n' "$MAC_MODELS" | grep "^$1|" | head -1)"
    printf '%s' "$line" | cut -d'|' -f"$2"
}

# Nearest whole GB, halves rounded up (the same rule as the Windows
# installer and scripts/llm_tiers.py round_gb).
round_gb_bytes() { echo $(( ($1 + 536870912) / 1073741824 )); }

# GB with one decimal, rounded UP (download sizes).
bytes_to_gb_up() { awk -v b="$1" 'BEGIN { g = b / 1073741824 * 10; i = int(g); if (g > i) i++; printf "%.1f", i / 10 }'; }

# The decision for this Mac - the same rules as scripts/llm_tiers.py
# choose_mac (the project's tests run both on the same inputs).
# Sets CH_OFFERED (1/0), CH_REASON, and when offered CH_TIER, CH_MODEL,
# CH_CONTEXT, CH_KV, CH_HISTORY, CH_NEED, CH_BUILD.
mac_choose() {
    local apple="$1" ram="$2" id min model ctx kv hist need
    CH_OFFERED=0; CH_REASON=""; CH_TIER=""; CH_MODEL=""; CH_CONTEXT=""; CH_KV=""
    CH_HISTORY=""; CH_NEED=""; CH_BUILD=""
    if [ "$apple" != "1" ]; then CH_REASON="intel_mac"; return 0; fi
    while read -r id min model ctx kv hist need; do
        [ -n "$id" ] || continue
        if [ "$ram" -ge "$min" ] 2>/dev/null; then
            CH_TIER="$id"; CH_MODEL="$model"; CH_CONTEXT="$ctx"; CH_KV="$kv"
            CH_HISTORY="$hist"; CH_NEED="$need"
        fi
    done <<< "$MAC_TIERS"
    if [ -z "$CH_TIER" ]; then CH_REASON="low_ram"; return 0; fi
    CH_OFFERED=1; CH_BUILD="$MAC_BUILD"
}

# Everything option A downloads (bytes): model + its vision file + llama.cpp.
llm_download_bytes() {
    local bytes
    bytes=$(( $(model_field "$1" 6) + $(model_field "$1" 10) ))
    bytes=$(( bytes + $(printf '%s' "$MAC_BUILD_FILES" | awk '{ s += $2 } END { print s }') ))
    echo "$bytes"
}

# Free space (GB, one decimal) where $1 is (or will be), -1 when unknown.
free_disk_gb() {
    local d="$1"
    while [ -n "$d" ] && [ ! -d "$d" ]; do d="$(dirname "$d")"; done
    df -Pk "${d:-/}" 2>/dev/null | awk 'NR == 2 { printf "%.1f", int($4 / 1048576 * 10) / 10; found = 1 } END { if (!found) print "-1" }'
}

# Look at this Mac (honouring the --simulate-* test options) and decide.
# Sets HW_APPLE, HW_CHIP, HW_RAM_GB, HW_DISK_GB, HW_SIMULATED, HW_DL_GB and
# the CH_* decision.
hardware_check() {
    local bytes
    HW_SIMULATED=0
    HW_APPLE="$IS_SILICON"
    HW_CHIP="$(sysctl -n machdep.cpu.brand_string 2>/dev/null || true)"
    [ -n "$HW_CHIP" ] || HW_CHIP="$([ "$IS_SILICON" = 1 ] && echo "Apple chip" || echo "Intel processor")"
    bytes="$(sysctl -n hw.memsize 2>/dev/null || echo "")"
    case "$bytes" in
        ''|*[!0-9]*) HW_RAM_GB=-1 ;;
        *) HW_RAM_GB="$(round_gb_bytes "$bytes")" ;;
    esac
    if [ -n "$SIM_CHIP" ]; then
        HW_SIMULATED=1
        if [ "$SIM_CHIP" = "apple" ]; then HW_APPLE=1; HW_CHIP="(pretend Apple chip)"
        else HW_APPLE=0; HW_CHIP="(pretend Intel processor)"; fi
    fi
    if [ "$SIM_RAM_GB" -ge 0 ] 2>/dev/null; then HW_SIMULATED=1; HW_RAM_GB="$SIM_RAM_GB"; fi
    HW_DISK_GB="$(free_disk_gb "$INSTALL_DIR")"
    mac_choose "$HW_APPLE" "$HW_RAM_GB"
    HW_DL_GB=0
    if [ "$CH_OFFERED" = 1 ]; then HW_DL_GB="$(bytes_to_gb_up "$(llm_download_bytes "$CH_MODEL")")"; fi
}

# Plain-language summary lines of a hardware check (screen + log).
format_hardware_check() {
    log "  Chip          : $HW_CHIP"
    if [ "$HW_RAM_GB" -ge 0 ] 2>/dev/null; then log "  Memory        : $HW_RAM_GB GB (shared by the processor and graphics)"
    else log "  Memory        : unknown"; fi
    log "  Free disk     : $HW_DISK_GB GB"
    if [ "$CH_OFFERED" = 1 ]; then
        log "  Local model   : $(model_field "$CH_MODEL" 2) ($HW_DL_GB GB download, with its helper program)"
        log "  Settings      : $CH_CONTEXT context, $CH_KV memory, voice on the Apple chip's accelerator, Metal build"
    elif [ "$CH_REASON" = "intel_mac" ]; then
        log "  Local model   : not offered - it needs a Mac with an Apple chip (option B only)"
    else
        log "  Local model   : not offered - it needs at least $MAC_MIN_RAM_GB GB of memory (option B only)"
    fi
    if [ "$HW_SIMULATED" = 1 ]; then log "  (TEST: pretend hardware values in use)"; fi
}

json_str() { printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"; }
json_bool() { if [ "$1" = 1 ]; then printf 'true'; else printf 'false'; fi; }

# One line of JSON (the project's tests read it): --hardware-check-only --json.
hardware_json() {
    local choice
    if [ "$CH_OFFERED" = 1 ]; then
        choice="{\"offered\": true, \"reason\": \"\", \"tier\": \"$CH_TIER\", \"model\": \"$CH_MODEL\", \"context\": $CH_CONTEXT, \"kv_cache\": \"$CH_KV\", \"voice_device\": \"mps\", \"history_default\": $CH_HISTORY, \"vram_need_gb\": $CH_NEED, \"warning\": false, \"build\": \"$CH_BUILD\"}"
    else
        choice="{\"offered\": false, \"reason\": \"$CH_REASON\"}"
    fi
    printf '{"apple_chip": %s, "chip": %s, "ram_gb": %s, "disk_free_gb": %s, "simulated": %s, "download_gb": %s, "choice": %s}\n' \
        "$(json_bool "$HW_APPLE")" "$(json_str "$HW_CHIP")" "$HW_RAM_GB" "$HW_DISK_GB" \
        "$(json_bool "$HW_SIMULATED")" "$HW_DL_GB" "$choice"
}

# A value from one of our flat JSON files (one "key": value per line - how
# this installer and the app's json.dump(indent=4) write them). Strings come
# back without their quotes; "" when the key is missing.
json_get() {
    [ -f "$1" ] || return 0
    sed -n "s/^[[:space:]]*\"$2\":[[:space:]]*\\(.*\\)\$/\\1/p" "$1" | head -1 \
        | sed -e 's/,[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e 's/\\"/"/g' -e 's/\\\\/\\/g'
}

# Ask until the answer is one of the valid ones (upper-cased): sets ANSWER.
# Gives up after many empty / wrong answers, so a closed input never loops.
read_answer() {
    local prompt="$1" a i v
    shift
    i=0
    while [ $i -lt 20 ]; do
        a=""
        read -r -p "$prompt: " a || a=""
        a="$(printf '%s' "$a" | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')"
        for v in "$@"; do
            if [ "$a" = "$v" ]; then ANSWER="$a"; log "  (answer: $a)"; return 0; fi
        done
        log "  Please type one of: $*"
        i=$((i + 1))
    done
    die "No answer was given."
}

# The chosen option (CHOICE_*), saved next to the project so a re-run asks
# only "keep it?".
set_choice_custom() { CHOICE_MODE="custom"; CHOICE_MODEL=""; CHOICE_CHOSEN="$(date '+%Y-%m-%dT%H:%M:%S')"; }
set_choice_from_check() {   # $1 = mode (builtin / custom), $2 = chosen time ("" = now)
    CHOICE_MODE="$1"; CHOICE_MODEL="$CH_MODEL"; CHOICE_TIER="$CH_TIER"; CHOICE_BUILD="$CH_BUILD"
    CHOICE_CONTEXT="$CH_CONTEXT"; CHOICE_KV="$CH_KV"; CHOICE_HISTORY="$CH_HISTORY"
    CHOICE_NEED="$CH_NEED"; CHOICE_SIMULATED="$HW_SIMULATED"
    CHOICE_CHOSEN="${2:-$(date '+%Y-%m-%dT%H:%M:%S')}"
}
write_choice_file() {
    mkdir -p "$MODELS_DIR"
    if [ "$CHOICE_MODE" = "builtin" ]; then
        printf '{\n    "mode": "builtin",\n    "model": "%s",\n    "tier": "%s",\n    "build": "%s",\n    "context": %s,\n    "kv_cache": "%s",\n    "voice_device": "mps",\n    "history_default": %s,\n    "vram_need_gb": %s,\n    "simulated": %s,\n    "chosen": "%s"\n}\n' \
            "$CHOICE_MODEL" "$CHOICE_TIER" "$CHOICE_BUILD" "$CHOICE_CONTEXT" "$CHOICE_KV" \
            "$CHOICE_HISTORY" "$CHOICE_NEED" "$(json_bool "$CHOICE_SIMULATED")" "$CHOICE_CHOSEN" > "$LLM_CHOICE_FILE"
    else
        printf '{\n    "mode": "custom",\n    "chosen": "%s"\n}\n' "$CHOICE_CHOSEN" > "$LLM_CHOICE_FILE"
    fi
}

# Option A on this Mac: show what was found, then confirm (or option B).
confirm_local_model() {
    local need
    log ""
    format_hardware_check
    log ""
    if [ "$CH_OFFERED" != 1 ]; then
        if [ "$CH_REASON" = "intel_mac" ]; then
            log "  Her built-in model needs a Mac with an Apple chip (M1 or newer). On this Mac she would think far too slowly, so Amadeus will use option B: you connect a model yourself (a cloud service such as OpenRouter, or your own model server)."
        else
            log "  Her built-in model needs at least $MAC_MIN_RAM_GB GB of memory (this Mac has $HW_RAM_GB GB). The smaller models that would fit don't sound like her, so Amadeus will use option B: you connect a model yourself (a cloud service such as OpenRouter, or your own model server)."
        fi
        read -r -p "Press Enter to continue with option B " _ || true
        set_choice_custom; return 0
    fi
    need="$(awk -v a="$HW_DL_GB" -v b="$DISK_HEADROOM_GB" 'BEGIN { printf "%.1f", a + b }')"
    if awk -v f="$HW_DISK_GB" -v n="$need" 'BEGIN { exit !(f >= 0 && f < n) }'; then
        log "  Not enough free disk space for the local model: it needs about $need GB, this disk has $HW_DISK_GB GB free."
        read_answer "Type B to continue with option B, or Q to stop here (free up space, then run the installer again)" B Q
        if [ "$ANSWER" = "Q" ]; then
            log "Stopped - nothing was changed. Free up some space, then run the installer again."
            read -r -p "Press Enter to close this window " _ || true
            exit 0
        fi
        set_choice_custom; return 0
    fi
    read_answer "Download $(model_field "$CH_MODEL" 2) ($HW_DL_GB GB) while everything else installs? Type Y (yes) or B (do it myself instead)" Y B
    if [ "$ANSWER" = "B" ]; then set_choice_custom; return 0; fi
    set_choice_from_check builtin ""
}

# STEP 0: the very first question, before anything is downloaded.
llm_choice_step() {
    local prev_mode prev_model prev_chosen
    step 0 "How should Amadeus think?"
    if [ -f "$LLM_CHOICE_FILE" ]; then
        prev_mode="$(json_get "$LLM_CHOICE_FILE" mode)"
        prev_model="$(json_get "$LLM_CHOICE_FILE" model)"
        prev_chosen="$(json_get "$LLM_CHOICE_FILE" chosen)"
        if [ "$prev_mode" = "custom" ] || { [ "$prev_mode" = "builtin" ] && [ -n "$(model_field "$prev_model" 1)" ]; }; then
            if [ "$prev_mode" = "builtin" ]; then log "  Last time you chose: A) her built-in local model - $(model_field "$prev_model" 2)"
            else log "  Last time you chose: B) you connect a model yourself"; fi
            read_answer "Keep that choice? Type Y (yes) or N (choose again)" Y N
            if [ "$ANSWER" = "Y" ]; then
                if [ "$prev_mode" = "builtin" ] && [ "$CH_OFFERED" = 1 ] && [ "$CH_MODEL" = "$prev_model" ]; then
                    # A kept choice takes this installer's settings for the
                    # same model (a newer table can change them).
                    set_choice_from_check builtin "$prev_chosen"
                elif [ "$prev_mode" = "builtin" ]; then
                    CHOICE_MODE="builtin"; CHOICE_MODEL="$prev_model"
                    CHOICE_TIER="$(json_get "$LLM_CHOICE_FILE" tier)"; CHOICE_BUILD="$(json_get "$LLM_CHOICE_FILE" build)"
                    CHOICE_CONTEXT="$(json_get "$LLM_CHOICE_FILE" context)"; CHOICE_KV="$(json_get "$LLM_CHOICE_FILE" kv_cache)"
                    CHOICE_HISTORY="$(json_get "$LLM_CHOICE_FILE" history_default)"; CHOICE_NEED="$(json_get "$LLM_CHOICE_FILE" vram_need_gb)"
                    CHOICE_SIMULATED="$([ "$(json_get "$LLM_CHOICE_FILE" simulated)" = "true" ] && echo 1 || echo 0)"
                    CHOICE_CHOSEN="$prev_chosen"
                else
                    CHOICE_MODE="custom"; CHOICE_MODEL=""; CHOICE_CHOSEN="$prev_chosen"
                fi
                write_choice_file
                return 0
            fi
        fi
    fi
    log "  How should Amadeus think?"
    log "    A) Install and set up a local model automatically (private, runs on this Mac, no account needed)"
    log "    B) I'll do it myself (choose this if using a cloud model or your own local model)"
    read_answer "Type A or B" A B
    if [ "$ANSWER" = "A" ]; then confirm_local_model; else set_choice_custom; fi
    write_choice_file
    if [ "$CHOICE_MODE" = "builtin" ]; then
        log "  Chosen: $(model_field "$CHOICE_MODEL" 2) - it downloads in the background while everything else installs."
    else
        log "  Chosen: option B - you connect a model yourself in Settings after the install."
    fi
}

file_size() { if [ -f "$1" ]; then wc -c < "$1" | tr -d ' '; else echo 0; fi; }
sha256_of() {
    if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
    else sha256sum "$1" | cut -d' ' -f1; fi
}

# Download one file and prove it is the official one (size + SHA-256). Safe
# to re-run: a finished, checked file is skipped (a ".verified" note next to
# it saves re-checking 7 GB every time); a half-finished ".part" continues
# where it stopped; a damaged download is deleted. Returns 1 with the reason
# in VF_ERROR.
get_verified_file() {
    local url="$1" dest="$2" bytes="$3" sha="$4" label="$5"
    local marker="$dest.verified" stamp="$4 $3" part="$2.part" try have got
    VF_ERROR=""
    if [ -f "$dest" ] && [ -f "$marker" ] && [ "$(cat "$marker")" = "$stamp" ] && [ "$(file_size "$dest")" = "$bytes" ]; then
        log "  Already have $label (checked) - skipping."; return 0
    fi
    mkdir -p "$(dirname "$dest")"
    if [ -f "$dest" ]; then
        # Present but not checked yet (e.g. an interrupted check): check it now.
        if [ "$(file_size "$dest")" = "$bytes" ] && [ "$(sha256_of "$dest")" = "$sha" ]; then
            printf '%s' "$stamp" > "$marker"
            log "  Already have $label (checked) - skipping."; return 0
        fi
        mv -f "$dest" "$part"
    fi
    if [ "$(file_size "$part")" = "$bytes" ]; then
        printf '%s\n' "  $label: already downloaded in the background, from $url" >> "$INSTALL_LOG"
    else
        log "  Downloading $label ($(bytes_to_gb_up "$bytes") GB)..."
        log "  from: $url"
    fi
    try=1
    while [ $try -le 4 ]; do
        have="$(file_size "$part")"
        if [ "$have" -gt "$bytes" ]; then rm -f "$part"; fi
        [ "$(file_size "$part")" = "$bytes" ] && break
        curl -L --fail --retry 5 --retry-delay 10 --connect-timeout 30 -C - --progress-bar -o "$part" "$url"
        printf '%s\n' "  download attempt $try ($label): curl exit $?, $(file_size "$part") of $bytes bytes" >> "$INSTALL_LOG"
        [ "$(file_size "$part")" = "$bytes" ] && break
        if [ $try -lt 4 ]; then
            log "  The download was interrupted - trying again shortly (it continues where it stopped)..."
            sleep "$DOWNLOAD_RETRY_DELAY"
        fi
        try=$((try + 1))
    done
    if [ "$(file_size "$part")" != "$bytes" ]; then
        VF_ERROR="Could not download $label right now. Check your internet connection, then run the installer again - it continues where it stopped."
        return 1
    fi
    log "  Checking the downloaded file (about a minute for the big ones)..."
    got="$(sha256_of "$part")"
    printf '%s\n' "  checksum $label: $got (expected $sha)" >> "$INSTALL_LOG"
    if [ "$got" != "$sha" ]; then
        rm -f "$part"
        VF_ERROR="$label was downloaded but is damaged (its checksum does not match the official one), so it was deleted. Run the installer again to download it fresh."
        return 1
    fi
    mv -f "$part" "$dest"
    printf '%s' "$stamp" > "$marker"
    log "  $label downloaded and checked."
}

hf_url() { printf 'https://huggingface.co/%s/resolve/%s/%s?download=true' "$1" "$2" "$3"; }
llama_url() { printf 'https://github.com/ggml-org/llama.cpp/releases/download/%s/%s' "$LLAMA_RELEASE" "$1"; }

# Unpack a .tar.gz into $2. Entries that would land outside it (an absolute
# path or "..") are refused before anything is unpacked.
expand_tar_safe() {
    local list
    list="$(tar -tzf "$1" 2>/dev/null)" || { VF_ERROR="The llama.cpp download could not be read."; return 1; }
    if printf '%s\n' "$list" | grep -E -q '^/|(^|/)\.\.(/|$)'; then
        VF_ERROR="Unsafe path in $(basename "$1") - not unpacked."; return 1
    fi
    mkdir -p "$2"
    tar -xzf "$1" -C "$2" || { VF_ERROR="The llama.cpp download could not be unpacked."; return 1; }
}

# Download + unpack llama.cpp's Apple-chip (Metal) build into
# models/llama.cpp/<release>-metal-arm64/ and prove its server program
# starts. Sets LLAMA_EXE (or returns 1 with VF_ERROR).
install_llama_build() {
    local dir="$MODELS_DIR/llama.cpp/$LLAMA_RELEASE-$MAC_BUILD" done_file dl name bytes sha found ver code
    done_file="$dir/install_complete.txt"
    LLAMA_EXE=""
    if [ -f "$done_file" ] && [ -x "$(cat "$done_file")" ]; then
        LLAMA_EXE="$(cat "$done_file")"
        log "  Already have llama.cpp $LLAMA_RELEASE ($MAC_BUILD) - skipping."; return 0
    fi
    dl="$MODELS_DIR/downloads"
    read -r name bytes sha <<< "$MAC_BUILD_FILES"
    get_verified_file "$(llama_url "$name")" "$dl/$name" "$bytes" "$sha" "llama.cpp $name" || return 1
    rm -rf "$dir"
    log "  Unpacking llama.cpp ($MAC_BUILD)..."
    expand_tar_safe "$dl/$name" "$dir" || return 1
    found="$(find "$dir" -type f -name llama-server | head -1)"
    if [ -z "$found" ]; then VF_ERROR="The llama.cpp download ($MAC_BUILD) has no llama-server."; return 1; fi
    chmod +x "$found" 2>/dev/null || true
    # Downloaded with curl, so macOS has no "downloaded from the internet"
    # mark on it - removed anyway, in case another tool added one.
    xattr -dr com.apple.quarantine "$dir" >/dev/null 2>&1 || true
    # Start test: the program must run (catches a missing library file).
    ver="$("$found" --version 2>&1)"; code=$?
    printf '%s\n' "  llama-server --version (exit $code): $ver" >> "$INSTALL_LOG"
    if [ $code -ne 0 ]; then VF_ERROR="llama.cpp ($MAC_BUILD) would not start on this Mac: $ver"; return 1; fi
    printf '%s' "$found" > "$done_file"
    rm -f "$dl/$name" "$dl/$name.verified"
    LLAMA_EXE="$found"
    log "  llama.cpp $LLAMA_RELEASE ($MAC_BUILD) ready."
}

# The app's built-in-model settings: backend/data/local_llm.json (the same
# fields as the Windows installer writes). Written even when a download
# failed (model_ok / server_ok false), so the app knows the model is
# missing. Option B on an Apple-chip Mac: the same file with mode "custom"
# (llama.cpp is installed, so Settings can switch to Built-in later). Option
# B elsewhere: an old file is kept, but switched to "custom". What the user
# changed in the app survives a re-run: the model file picked in Settings
# (selected_file, while it still exists) and the conversation memory number
# for the built-in model (history_budget).
# $1 project  $2 mode  $3 llama-server path  $4 server ok (1/0)  $5 model path  $6 model ok (1/0)
write_local_llm_config() {
    local proj="$1" mode="$2" exe="$3" server_ok="$4" model_path="$5" model_ok="$6"
    local cfg="$proj/backend/data/local_llm.json" sel="" budget="" extra=""
    mkdir -p "$proj/backend/data"
    if [ "$mode" = "custom" ] && [ -z "$CHOICE_MODEL" ]; then
        if [ -f "$cfg" ]; then
            sed 's/^\([[:space:]]*"mode":[[:space:]]*\)"builtin"/\1"custom"/' "$cfg" > "$cfg.tmp" && mv -f "$cfg.tmp" "$cfg"
        fi
        return 0
    fi
    if [ -f "$cfg" ]; then
        sel="$(json_get "$cfg" selected_file)"
        budget="$(json_get "$cfg" history_budget)"
    fi
    if [ -n "$sel" ] && [ -f "$sel" ]; then extra="$extra,
    \"selected_file\": $(json_str "$sel")"; fi
    case "$budget" in ''|*[!0-9]*) ;; *) extra="$extra,
    \"history_budget\": $budget" ;; esac
    cat > "$cfg.tmp" <<EOF
{
    "mode": "$mode",
    "model": "$CHOICE_MODEL",
    "model_label": $(json_str "$(model_field "$CHOICE_MODEL" 2)"),
    "model_file": $(json_str "$model_path"),
    "model_ok": $(json_bool "$model_ok"),
    "server_exe": $(json_str "$exe"),
    "server_ok": $(json_bool "$server_ok"),
    "llama_release": "$LLAMA_RELEASE",
    "build": "$CHOICE_BUILD",
    "tier": "$CHOICE_TIER",
    "context": $CHOICE_CONTEXT,
    "kv_cache": "$CHOICE_KV",
    "voice_device": "mps",
    "gpu_layers": 999,
    "port": $LLM_PORT,
    "history_default": $CHOICE_HISTORY,
    "vram_need_gb": $CHOICE_NEED,
    "simulated": $(json_bool "$CHOICE_SIMULATED"),
    "written": "$(date '+%Y-%m-%dT%H:%M:%S')",
    "installer": "mac-$INSTALLER_VERSION"$extra
}
EOF
    mv -f "$cfg.tmp" "$cfg"
    log "  Built-in model settings saved: $cfg"
}

# The model step for option A: llama.cpp + model + vision file. A failed
# download never stops the install - the summary says what to do.
# Returns 0 when everything she needs is in place.
install_builtin_llm() {
    local proj="$1" file model_path exe="" server_ok=0 model_ok=0 vision
    file="$(model_field "$CHOICE_MODEL" 5)"
    model_path="$MODELS_DIR/$file"
    if [ "$SKIP_LLM_DOWNLOAD" = 1 ]; then
        log "  (TEST: --skip-llm-download - nothing is downloaded; the settings file is written as 'missing'.)"
    else
        if install_llama_build; then exe="$LLAMA_EXE"; server_ok=1; else log "  $VF_ERROR"; fi
        if get_verified_file "$(hf_url "$(model_field "$CHOICE_MODEL" 3)" "$(model_field "$CHOICE_MODEL" 4)" "$file")" \
                "$model_path" "$(model_field "$CHOICE_MODEL" 6)" "$(model_field "$CHOICE_MODEL" 7)" \
                "$(model_field "$CHOICE_MODEL" 2)"; then
            model_ok=1
        else
            log "  $VF_ERROR"
        fi
        # Her vision file: lets her built-in model see the pictures and
        # video frames you show her. A failed download never stops the install.
        vision="$(model_field "$CHOICE_MODEL" 9)"
        if ! get_verified_file "$(hf_url "$(model_field "$CHOICE_MODEL" 3)" "$(model_field "$CHOICE_MODEL" 4)" "$(model_field "$CHOICE_MODEL" 8)")" \
                "$MODELS_DIR/$vision" "$(model_field "$CHOICE_MODEL" 10)" "$(model_field "$CHOICE_MODEL" 11)" \
                "$(model_field "$CHOICE_MODEL" 2) - vision file"; then
            log "  Vision file not downloaded - she works, but cannot see pictures or videos until you run the installer again: $VF_ERROR"
        fi
    fi
    write_local_llm_config "$proj" builtin "$exe" "$server_ok" "$model_path" "$model_ok"
    [ "$server_ok" = 1 ] && [ "$model_ok" = 1 ]
}

# Option B on an Apple-chip Mac that could run her model: llama.cpp too
# (12 MB, no model), so Settings -> Connection -> Built-in can run a GGUF of
# the user's own later, with this Mac's tier settings.
install_local_model_runner() {
    local proj="$1" exe="" model_path model_ok=0
    local keep_mode="$CHOICE_MODE" keep_chosen="$CHOICE_CHOSEN"
    set_choice_from_check custom "$keep_chosen"
    mkdir -p "$MODELS_DIR"
    if [ "$SKIP_LLM_DOWNLOAD" = 1 ]; then log "  (TEST: --skip-llm-download - llama.cpp is not downloaded.)"
    elif install_llama_build; then exe="$LLAMA_EXE"
    else log "  $VF_ERROR"; fi
    model_path="$MODELS_DIR/$(model_field "$CHOICE_MODEL" 5)"
    # Her own tier model, if an earlier option-A install left it (checked).
    if [ -f "$model_path" ] && [ -f "$model_path.verified" ]; then model_ok=1; fi
    write_local_llm_config "$proj" custom "$exe" "$([ -n "$exe" ] && echo 1 || echo 0)" "$model_path" "$model_ok"
    CHOICE_MODE="$keep_mode"
    [ -n "$exe" ]
}

# ---------- the big downloads start in the background ----------
# Right after the A/B question, macOS's own curl (no window) fetches
# llama.cpp + her model + its vision file into the same ".part" files
# get_verified_file uses: when the install reaches the model step it finds
# them finished (or half-finished, and continues them) and checks each one
# exactly as before. A re-run first stops a download an earlier run left.
bg_pid_file() { printf '%s' "$MODELS_DIR/downloads/background_$1.pid"; }

# Every file the chosen option downloads at its model step, one per line:
# url|dest|bytes|sha256 (already downloaded and checked ones left out).
local_llm_files() {
    local name bytes sha file
    { [ "$CHOICE_MODE" = "builtin" ] || [ "$CH_OFFERED" = 1 ]; } || return 0
    {
    if [ ! -f "$MODELS_DIR/llama.cpp/$LLAMA_RELEASE-$MAC_BUILD/install_complete.txt" ]; then
        read -r name bytes sha <<< "$MAC_BUILD_FILES"
        printf '%s|%s|%s|%s\n' "$(llama_url "$name")" "$MODELS_DIR/downloads/$name" "$bytes" "$sha"
    fi
    if [ "$CHOICE_MODE" = "builtin" ]; then
        file="$(model_field "$CHOICE_MODEL" 5)"
        printf '%s|%s|%s|%s\n' "$(hf_url "$(model_field "$CHOICE_MODEL" 3)" "$(model_field "$CHOICE_MODEL" 4)" "$file")" \
            "$MODELS_DIR/$file" "$(model_field "$CHOICE_MODEL" 6)" "$(model_field "$CHOICE_MODEL" 7)"
        printf '%s|%s|%s|%s\n' "$(hf_url "$(model_field "$CHOICE_MODEL" 3)" "$(model_field "$CHOICE_MODEL" 4)" "$(model_field "$CHOICE_MODEL" 8)")" \
            "$MODELS_DIR/$(model_field "$CHOICE_MODEL" 9)" "$(model_field "$CHOICE_MODEL" 10)" "$(model_field "$CHOICE_MODEL" 11)"
    fi
    } | while IFS='|' read -r u d b s; do
        if [ -f "$d" ] && [ -f "$d.verified" ] && [ "$(cat "$d.verified")" = "$s $b" ]; then continue; fi
        printf '%s|%s|%s|%s\n' "$u" "$d" "$b" "$s"
    done
}

# Is this pid a download we started (curl) and still running?
bg_alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null && ps -p "$1" -o comm= 2>/dev/null | grep -q curl; }

stop_background_downloads() {
    local pf id
    for pf in "$MODELS_DIR"/downloads/background_*.pid; do
        [ -f "$pf" ] || continue
        id="$(cat "$pf" 2>/dev/null)"
        if bg_alive "$id"; then kill "$id" 2>/dev/null; sleep 1; fi
        rm -f "$pf"
    done
}

start_background_downloads() {
    local list u d b s todo=0 total=0 log_file
    local args
    args=()
    [ "$SKIP_LLM_DOWNLOAD" = 1 ] && return 0
    stop_background_downloads
    list="$(local_llm_files)"
    [ -n "$list" ] || return 0
    log_file="$MODELS_DIR/downloads/background_model.log"
    mkdir -p "$MODELS_DIR/downloads"
    while IFS='|' read -r u d b s; do
        [ -n "$u" ] || continue
        [ -f "$d" ] && continue                              # unchecked file: the model step decides
        [ "$(file_size "$d.part")" -ge "$b" ] 2>/dev/null && continue
        mkdir -p "$(dirname "$d")"
        args+=(-o "$d.part" "$u"); todo=$((todo + 1)); total=$((total + b))
    done <<< "$list"
    [ $todo -gt 0 ] || return 0
    nohup curl -L --fail --retry 5 --retry-delay 10 --connect-timeout 30 -C - -s -S \
        "${args[@]}" >/dev/null 2>>"$log_file" &
    printf '%s' "$!" > "$(bg_pid_file model)"
    if [ "$CHOICE_MODE" = "builtin" ]; then
        log "  Her model files ($(bytes_to_gb_up "$total") GB) start downloading in the background now, while everything else installs."
    else
        log "  llama.cpp (to run a model of your own later) starts downloading in the background now."
    fi
    printf '%s\n' "  background download 'model' (curl pid $(cat "$(bg_pid_file model)")): $todo file(s)" >> "$INSTALL_LOG"
}

# When the model step needs them: let a still-running background download
# finish (showing its progress); get_verified_file then checks every file.
wait_background() {
    local pf id list total have u d b s
    pf="$(bg_pid_file model)"
    [ -f "$pf" ] || return 0
    id="$(cat "$pf" 2>/dev/null)"
    if bg_alive "$id"; then
        list="$(local_llm_files)"
        total="$(printf '%s\n' "$list" | awk -F'|' '{ s += $3 } END { print s + 0 }')"
        log "  Downloading her model - still going in the background, waiting for it to finish..."
        while bg_alive "$id"; do
            have=0
            while IFS='|' read -r u d b s; do
                [ -n "$d" ] || continue
                if [ -f "$d" ]; then have=$((have + $(file_size "$d")))
                else have=$((have + $(file_size "$d.part"))); fi
            done <<< "$list"
            printf '\r  %s of %s GB   ' "$(bytes_to_gb_up "$have")" "$(bytes_to_gb_up "$total")"
            sleep 2
        done
        printf '\n'
        printf '%s\n' "  background 'model' finished" >> "$INSTALL_LOG"
    fi
    rm -f "$pf"
}

# The model step for whichever option was chosen. Returns 0 when option
# A's model is ready (option B: when llama.cpp is in place).
install_local_llm() {
    local proj="$1"
    wait_background
    if [ "$CHOICE_MODE" = "builtin" ]; then install_builtin_llm "$proj"; return $?; fi
    if [ "$CH_OFFERED" = 1 ]; then install_local_model_runner "$proj"; return $?; fi
    write_local_llm_config "$proj" custom "" 0 "" 0   # switches an earlier option-A install off
    return 1
}
# <<< BUILT-IN MODEL FUNCTIONS

# >>> NEW DEFAULTS FUNCTIONS (tests/test_installer_update.py runs them)
# ---------- new default personality + voice rules (v3.0) ----------
# The same as the Windows installer (Confirm-NewDefaults / Apply-NewDefaults).
# On an update, right after the A/B question: when a NEWER version is coming
# (this copy's commit differs from the newest one) and her personality or
# voice rules differ from that version's defaults, the user is asked whether
# to replace them (Y: the current ones go to Amadeus-Project/backups/ first;
# N: kept - Settings still offers the new default with one click). Applied in
# step 3, only after the update really happened. The texts are compared like
# the app does (backend/defaults_refresh.py normalize: line ends, trailing
# spaces, outer blank lines).
REPLACE_DEFAULTS=0
DEFAULTS_OFFER=""

# stdin -> stdout, normalized like defaults_refresh.normalize.
normal_text() {
    tr -d '\r' | awk '
        { sub(/[[:space:]]+$/, ""); line[NR] = $0 }
        END {
            first = 0; last = 0
            for (i = 1; i <= NR; i++) if (line[i] != "") { if (!first) first = i; last = i }
            if (!first) exit
            sub(/^[[:space:]]+/, "", line[first])
            for (i = first; i <= last; i++) print line[i]
        }'
}

# The new version's default for one file ($1 = commit, $2 = personality /
# voice): the personality's default file, and the voice rules'
# DEFAULT_VOICE_BLOCK inside ja_voice.py. Fails when there is none.
new_default_text() {
    if [ "$2" = "personality" ]; then
        git -C "$PROJ" show "$1:backend/data/personality.default.txt" 2>/dev/null
        return
    fi
    git -C "$PROJ" show "$1:backend/ja_voice.py" 2>/dev/null | awk '
        BEGIN { q = "\"\"\""; mark = "DEFAULT_VOICE_BLOCK = r" q }
        done { next }
        !on {
            i = index($0, mark)
            if (!i) next
            on = 1
            $0 = substr($0, i + length(mark))
        }
        {
            j = index($0, q)
            if (j) { printf "%s\n", substr($0, 1, j - 1); done = 1; next }
            print
        }
        END { exit !done }'
}

# Which files a coming update would change (DEFAULTS_OFFER: "" or e.g. "her
# personality and her voice rules"). "" when there is no install yet, it is
# already the newest version, the check failed (offline), or nothing differs.
defaults_to_offer() {
    local head newest f user label tmp
    DEFAULTS_OFFER=""
    [ -d "$PROJ/.git" ] || return 0
    command -v git >/dev/null 2>&1 || return 0
    git -C "$PROJ" fetch --quiet origin "$BRANCH" >/dev/null 2>&1 || return 0
    head="$(git -C "$PROJ" rev-parse HEAD 2>/dev/null || true)"
    newest="$(git -C "$PROJ" rev-parse --verify --quiet "origin/$BRANCH" 2>/dev/null || true)"
    if [ -z "$newest" ] || [ "$head" = "$newest" ]; then return 0; fi
    tmp="${TMP:-/tmp}/amadeus_defaults_$$"
    for f in personality voice; do
        if [ "$f" = "personality" ]; then user="$PROJ/backend/data/personality.txt"; label="her personality"
        else user="$PROJ/backend/data/voice_ja.txt"; label="her voice rules"; fi
        [ -f "$user" ] || continue
        new_default_text "$newest" "$f" > "$tmp.new" || continue
        normal_text < "$tmp.new" > "$tmp.a"
        normal_text < "$user" > "$tmp.b"
        if ! cmp -s "$tmp.a" "$tmp.b"; then DEFAULTS_OFFER="${DEFAULTS_OFFER:+$DEFAULTS_OFFER and }$label"; fi
    done
    rm -f "$tmp.new" "$tmp.a" "$tmp.b"
}

notice() { printf '%s%s%s\n' "${C_BOLD:-}${C_BLU:-}" "$1" "${C_RESET:-}" | tee_to; }

# The question (after the A/B one). Sets REPLACE_DEFAULTS=1 for "Y".
confirm_new_defaults() {
    REPLACE_DEFAULTS=0
    defaults_to_offer
    [ -n "$DEFAULTS_OFFER" ] || return 0
    log ""
    notice "  ============================================================"
    notice "   IMPORTANT: this update brings new versions of $DEFAULTS_OFFER"
    notice "   with lots of improvements."
    notice ""
    notice "   If you choose Y, your current versions are REPLACED by the"
    notice "   new defaults. They are saved first in a new folder inside"
    notice "     $PROJ/backups"
    notice "   (copy anything you want back from there)."
    notice ""
    notice "   Choose N to keep your own versions. You can still switch to"
    notice "   the new defaults later in Settings, with one click."
    notice "  ============================================================"
    read_answer "Replace them with the new defaults? Type Y (yes) or N (no, keep mine)" Y N
    if [ "$ANSWER" = "Y" ]; then REPLACE_DEFAULTS=1; fi
}

# Step 3, after the update: save the current files, then put the new
# defaults in place (the voice rules are removed - the app rebuilds them
# from its new default, closeness levels included, when she starts).
apply_new_defaults() {
    local dir saved="" f
    dir="$PROJ/backups/$(date '+%Y-%m-%d_%H%M%S')"
    mkdir -p "$dir"
    for f in personality.txt voice_ja.txt; do
        if [ -f "$PROJ/backend/data/$f" ]; then
            cp -p "$PROJ/backend/data/$f" "$dir/$f"
            saved="${saved:+$saved, }$f"
        fi
    done
    cat > "$dir/README.txt" <<EOF
Saved by the Amadeus installer on $(date) before her new defaults were put in place.

  personality.txt -> backend/data/personality.txt  (her personality)
  voice_ja.txt    -> backend/data/voice_ja.txt     (her voice rules)

To go back to your version: close Amadeus, copy the file back to that place, start Amadeus again.
(Or copy just the parts you want into her Settings - Personality and mood.)
EOF
    if [ -f "$PROJ/backend/data/personality.default.txt" ]; then
        cp -f "$PROJ/backend/data/personality.default.txt" "$PROJ/backend/data/personality.txt"
    fi
    rm -f "$PROJ/backend/data/voice_ja.txt"
    log "  Her new default personality and voice rules are in place. Your previous versions ($saved) are in: $dir"
}

# Step 3 could not update this copy: a "Y" from the start is NOT applied.
defaults_not_replaced() {
    if [ "${REPLACE_DEFAULTS:-0}" = 1 ]; then
        log "  Her personality and voice rules were NOT replaced: this copy could not be updated (see above)."
    fi
    return 0
}
# <<< NEW DEFAULTS FUNCTIONS

# Test: only look at this Mac and say which model it would get (installs
# nothing, creates no folder).
if [ "$HARDWARE_CHECK_ONLY" = 1 ]; then
    INSTALL_LOG=""
    hardware_check
    if [ "$JSON_OUT" = 1 ]; then hardware_json
    else printf 'Amadeus hardware check (nothing is installed):\n'; format_hardware_check; fi
    exit 0
fi

# The folder first: the banner below already goes to the install log.
mkdir -p "$INSTALL_DIR"

# ----------------------------------------------------------------------------
#  BANNER
# ----------------------------------------------------------------------------
printf '%s%s%s\n' "$C_BOLD" "============================================================" "$C_RESET" | tee_to
printf '%s AMADEUS INSTALLER for MAC OS  %s%s\n' "$C_BOLD" "$INSTALLER_VERSION" "$C_RESET" | tee_to
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

if [ "$BRANCH" != "main" ] || [ "$SIM_RAM_GB" != "-1" ] || [ -n "$SIM_CHIP" ] || [ "$SKIP_LLM_DOWNLOAD" = 1 ]; then
    log "TEST OPTIONS in use:"
    if [ "$BRANCH" != "main" ]; then log "  branch            : $BRANCH"; fi
    if [ "$SIM_RAM_GB" != "-1" ]; then log "  pretend memory    : $SIM_RAM_GB GB"; fi
    if [ -n "$SIM_CHIP" ]; then log "  pretend chip      : $SIM_CHIP"; fi
    if [ "$SKIP_LLM_DOWNLOAD" = 1 ]; then log "  no local-model downloads (screens + settings file only)"; fi
fi
if [ "$(uname -s)" != "Darwin" ] && [ "$LLM_SCREENS_ONLY" != 1 ]; then
    die "This installer is for Mac OS (this machine is: $(uname -s))." \
        "Windows uses the other installer (install_windows.ps1)."
fi

# ----------------------------------------------------------------------------
#  STEP 0: how should she think? (before anything is downloaded)
# ----------------------------------------------------------------------------
hardware_check
llm_choice_step
if [ "$LLM_SCREENS_ONLY" = 1 ]; then
    install_local_llm "$PROJ" || true
    log "TEST: --llm-screens-only - stopping here (nothing else was installed)."
    exit 0
fi
# The second question: new default personality + voice rules (only on an
# update that brings a newer version and when they differ from it). Only
# with Apple's developer tools in place: without them "git" is just a stub
# that opens an install dialog (an existing install always has them).
if xcode-select -p >/dev/null 2>&1; then confirm_new_defaults; fi
log ""
log "Everything else installs now. It downloads several gigabytes, so give it time."
read -r -p "Press Enter to begin " _ || true
start_background_downloads

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
        # The archive puts the program inside a versioned folder
        # (git-lfs-3.8.0/git-lfs). Copy it up to the tools folder so the
        # PATH line below can find it.
        if [ ! -x "$TOOLS_DIR/git-lfs" ]; then
            found_lfs="$(find "$TOOLS_DIR" -type f -name git-lfs 2>/dev/null | head -1)"
            if [ -n "$found_lfs" ]; then
                cp "$found_lfs" "$TOOLS_DIR/git-lfs" 2>>"$lfs_err"
            fi
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

# --- 2e. FFmpeg for videos (video plan, 2026-11-06). Her voice never needs
# it (checked 2026-10-09: served as WAV), but videos do - the app prepares
# every video with ffmpeg + ffprobe. A Mac that already has both keeps its
# own. Otherwise: (A) the signed build 9.0.2 from ffmpeg.martin-riedl.de (the
# Windows installer's version), pinned and fingerprint-checked; (B) when that
# server is down, conda-forge's ffmpeg in its own small toolbox. Both land in
# ~/.amadeus-tools/ffmpeg/bin, where the launcher and the app look for it.
# Never fatal: without it only videos are missing (a re-run adds it).
FFMPEG_BIN="$TOOLS_DIR/ffmpeg/bin"
FFMPEG_ENV="$TOOLS_DIR/ffmpeg/conda"
FFMPEG_BASE="https://ffmpeg.martin-riedl.de/download/macos"
if [ $IS_SILICON = 1 ]; then
    FFMPEG_FILES=(
        "arm64/1789931890_9.0.2/ffmpeg.zip 28395699 c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924"
        "arm64/1789931890_9.0.2/ffprobe.zip 28317701 fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6")
else
    FFMPEG_FILES=(
        "amd64/1789931006_9.0.2/ffmpeg.zip 33816391 7c6b4125b191cbf773832dc51f424cf2b6bb7da43007d1e066f95909e47cacd4"
        "amd64/1789931006_9.0.2/ffprobe.zip 33719233 2322438ed2f6319a691291b247d09c69dcaa3a982460d1f269a7e1af335cfdfd")
fi
ffmpeg_works() {
    "$1/ffmpeg" -hide_banner -version >/dev/null 2>&1 \
        && "$1/ffprobe" -hide_banner -version >/dev/null 2>&1
}
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
    log "  FFmpeg: present on this Mac - videos use it."
elif ffmpeg_works "$FFMPEG_BIN"; then
    log "  FFmpeg for videos: already set up."
else
    log "  FFmpeg for videos: downloading (about 60 MB)..."
    ff_err="$INSTALL_DIR/ffmpeg-err.log"
    : > "$ff_err"
    mkdir -p "$FFMPEG_BIN"
    ff_ok=1
    for spec in "${FFMPEG_FILES[@]}"; do
        read -r ff_path _ff_size ff_sha <<< "$spec"
        ff_name="$(basename "$ff_path" .zip)"
        ff_zip="$TOOLS_DIR/ffmpeg/$ff_name.zip"
        rm -f "$ff_zip"
        curl -L --fail --retry 3 --retry-delay 5 --connect-timeout 20 --progress-bar \
            -o "$ff_zip" "$FFMPEG_BASE/$ff_path" 2>>"$ff_err" || true
        if [ -f "$ff_zip" ] && [ "$(shasum -a 256 "$ff_zip" | cut -d' ' -f1)" = "$ff_sha" ]; then
            rm -f "$FFMPEG_BIN/$ff_name"
            # The same unzip-or-bsdtar choice as Git-LFS above (a brand-new
            # Mac may not have 'unzip' on its path yet).
            if command -v unzip >/dev/null 2>&1; then
                unzip -o -q "$ff_zip" -d "$FFMPEG_BIN" >>"$ff_err" 2>&1
            else
                /usr/bin/bsdtar -x -f "$ff_zip" -C "$FFMPEG_BIN" >>"$ff_err" 2>&1
            fi
            chmod +x "$FFMPEG_BIN/$ff_name" 2>/dev/null || true
        else
            echo "FFmpeg: $ff_path missing or its fingerprint did not match." >>"$ff_err"
            ff_ok=0
        fi
        rm -f "$ff_zip"
        [ $ff_ok = 1 ] || break
    done
    if [ $ff_ok = 1 ] && ffmpeg_works "$FFMPEG_BIN"; then
        log "  FFmpeg for videos ready (9.0.2, in your home folder)."
    else
        # (B) conda-forge in its own toolbox; small scripts in the bin
        # folder hand over to it, so the app finds it in the usual place.
        log "  (the FFmpeg download did not work - trying conda-forge instead, a few minutes...)"
        rm -f "$FFMPEG_BIN/ffmpeg" "$FFMPEG_BIN/ffprobe"
        if [ ! -x "$FFMPEG_ENV/bin/ffmpeg" ] || [ ! -x "$FFMPEG_ENV/bin/ffprobe" ]; then
            rm -rf "$FFMPEG_ENV"
            # The "gpl" build has the H.264 encoder the app falls back on;
            # any conda-forge ffmpeg if that one is not offered.
            "$CONDA" create -y -q -p "$FFMPEG_ENV" -c conda-forge --override-channels \
                "ffmpeg=*=gpl*" >>"$ff_err" 2>&1 \
                || "$CONDA" create -y -q -p "$FFMPEG_ENV" -c conda-forge --override-channels \
                    ffmpeg >>"$ff_err" 2>&1 || true
        fi
        if [ -x "$FFMPEG_ENV/bin/ffmpeg" ] && [ -x "$FFMPEG_ENV/bin/ffprobe" ]; then
            for ff_name in ffmpeg ffprobe; do
                printf '#!/bin/sh\nexec "%s/bin/%s" "$@"\n' "$FFMPEG_ENV" "$ff_name" > "$FFMPEG_BIN/$ff_name"
                chmod +x "$FFMPEG_BIN/$ff_name"
            done
        fi
        if ffmpeg_works "$FFMPEG_BIN"; then
            log "  FFmpeg for videos ready (from conda-forge, in your home folder)."
        else
            rm -f "$FFMPEG_BIN/ffmpeg" "$FFMPEG_BIN/ffprobe"
            printf '%s  WARNING: FFmpeg could not be set up now - Amadeus works, but videos need it. Run the installer again later (details: %s).%s\n' \
                "$C_YEL" "$ff_err" "$C_RESET" | tee_to
        fi
    fi
fi

# --- 2f. Speech model for videos (video plan, MH 2026-11-03: part of every
# install, no question): faster-whisper "small", pinned (backend/transcribe.py
# lists the same files - a test keeps them equal). Never fatal: the app
# fetches it itself when it is missing.
SPEECH_REV="536b0662742c02347bc0e980a01041f333bce120"
SPEECH_DIR="$TOOLS_DIR/speech/faster-whisper-small"
speech_ok=1
mkdir -p "$SPEECH_DIR"
for spec in \
    "config.json 2370 b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828" \
    "model.bin 483546902 3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671" \
    "tokenizer.json 2203239 fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab" \
    "vocabulary.txt 459861 34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"; do
    read -r sp_name _sp_size sp_sha <<< "$spec"
    sp_file="$SPEECH_DIR/$sp_name"
    if [ -f "$sp_file" ] && [ "$(shasum -a 256 "$sp_file" | cut -d' ' -f1)" = "$sp_sha" ]; then
        continue
    fi
    curl -L --fail --retry 5 --retry-delay 10 -C - --progress-bar -o "$sp_file.part" \
        "https://huggingface.co/Systran/faster-whisper-small/resolve/$SPEECH_REV/$sp_name" || true
    if [ -f "$sp_file.part" ] && [ "$(shasum -a 256 "$sp_file.part" | cut -d' ' -f1)" = "$sp_sha" ]; then
        mv -f "$sp_file.part" "$sp_file"
    else
        rm -f "$sp_file.part"
        speech_ok=0
        break
    fi
done
if [ "$speech_ok" = 1 ]; then
    printf '%s' "$SPEECH_REV" > "$SPEECH_DIR/.verified"
    log "  Speech model for videos ready."
else
    log "  The speech model for videos could not be downloaded now - Amadeus downloads it herself later."
fi

# ----------------------------------------------------------------------------
#  STEP 3: the app itself (from the fork, main branch)
# ----------------------------------------------------------------------------
step 3 "Getting Amadeus (the project files)"

if [ -d "$PROJ/.git" ]; then
    log "  Found an existing Amadeus at $PROJ - updating it..."
    # Personal live-value files (her saved connection, personality) are kept
    # out of the update - the same rule as the Windows installer. Only files
    # that are actually changed are shelved, and only a shelf made here is
    # put back afterwards.
    personal="$(git -C "$PROJ" diff --name-only 2>/dev/null \
                | grep -E '(^|/)(llm_server|llm_model|personality)\.txt$' || true)"
    stashed=""
    if [ -n "$personal" ] && git -C "$PROJ" stash push --quiet -m "amadeus-installer-personal" -- $personal; then
        stashed="$(git -C "$PROJ" rev-parse --verify --quiet 'stash@{0}' || true)"
    fi
    # The user's own versions go back over whatever the update brought - the
    # same rule as the Windows installer. Not 'stash pop': when a release
    # changes the shipped personality.txt (v3.0 did), pop merged the two and
    # left conflict markers in her personality.
    restore_personal() {
        [ -n "$stashed" ] || return 0
        local f
        for f in $personal; do
            if git -C "$PROJ" cat-file -e "$stashed:$f" 2>/dev/null; then
                git -C "$PROJ" show "$stashed:$f" > "$PROJ/$f"
            fi
        done
        if [ "$(git -C "$PROJ" rev-parse --verify --quiet 'stash@{0}' || true)" = "$stashed" ]; then
            git -C "$PROJ" stash drop --quiet >/dev/null 2>&1 || true
        fi
        stashed=""
    }
    git -C "$PROJ" fetch --quiet origin \
        || { restore_personal; defaults_not_replaced; die "Could not reach the project repository." \
               "Check your internet connection, then re-run the installer."; }
    # Move onto origin/$BRANCH ONLY when this copy holds nothing of the user's
    # own: every commit the target lacks was published by this project, and
    # both share the original project's history. Covers (a) an install made
    # by an older installer on the old feature branch and (b) the one-time
    # history clean-up of v2.1 (old commits no longer fast-forward).
    only_project_commits() {
        own="$(git -C "$PROJ" log --format=%an "origin/$BRANCH..HEAD" 2>/dev/null \
               | grep -v -x -e 'cmh95209' -e 'Amadeus Local Build' || true)"
        git -C "$PROJ" merge-base HEAD "origin/$BRANCH" >/dev/null 2>&1 && [ -z "$own" ]
    }
    current="$(git -C "$PROJ" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
    if [ "$current" = "$LEGACY_BRANCH" ]; then
        if only_project_commits && git -C "$PROJ" checkout --quiet -B "$BRANCH" "origin/$BRANCH"; then
            git -C "$PROJ" branch --quiet --set-upstream-to "origin/$BRANCH" "$BRANCH" >/dev/null 2>&1 || true
            git -C "$PROJ" branch --quiet -D "$LEGACY_BRANCH" >/dev/null 2>&1 || true
            log "  (moved this copy from the old feature branch to main)"
        else
            restore_personal
            defaults_not_replaced
            die "Your existing Amadeus copy has changes of its own and cannot be updated automatically." \
                "Easiest fix: delete the folder $PROJ and re-run this installer (a fresh copy is created)."
        fi
    else
        if ! git -C "$PROJ" checkout --quiet "$BRANCH" 2>/dev/null; then
            log "  (note: the local copy is not on the expected branch - continuing on what it has)"
        fi
        if ! git -C "$PROJ" merge --ff-only --quiet "origin/$BRANCH" 2>/dev/null; then
            if only_project_commits && git -C "$PROJ" reset --quiet --keep "origin/$BRANCH"; then
                log "  (the project's history was cleaned up - moved this copy onto it)"
            else
                restore_personal
                defaults_not_replaced
                die "Your existing Amadeus copy has diverged and cannot be updated automatically." \
                    "Easiest fix: delete the folder $PROJ and re-run this installer (a fresh copy is created)."
            fi
        fi
    fi
    restore_personal
    # Her new defaults (the answer from the start): only when the update
    # above really brought the newest version.
    if [ "${REPLACE_DEFAULTS:-0}" = 1 ]; then
        if [ "$(git -C "$PROJ" rev-parse HEAD 2>/dev/null)" = "$(git -C "$PROJ" rev-parse "origin/$BRANCH" 2>/dev/null)" ]; then
            apply_new_defaults
        else
            defaults_not_replaced
        fi
    fi
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
# Pin the voice engine to the version Amadeus is tested with (2026-08-18):
# GPT-SoVITS's October 2026 update ("V5" + a CUDA-graph rework) made her
# voice take minutes per line on Windows. Fresh downloads AND older installs
# are put on it; the switch resets the copy's changed files (the voice
# patches, re-applied right below, and the engine's own settings file, back
# to the tested original). Downloaded model files are untracked and stay.
GPT_SOVITS_COMMIT="48b1a0169a28582a8984402f82cf438d3bfa6aca"
if [ "$(git -C "$GPT" rev-parse HEAD 2>/dev/null)" != "$GPT_SOVITS_COMMIT" ]; then
    log "  Switching the voice engine to the tested version (${GPT_SOVITS_COMMIT:0:7})..."
    git -C "$GPT" cat-file -e "${GPT_SOVITS_COMMIT}^{commit}" 2>/dev/null \
        || git -C "$GPT" fetch --quiet origin "$GPT_SOVITS_COMMIT" \
        || die "Could not download the tested voice engine version." \
               "Check your internet connection, then re-run the installer."
    git -C "$GPT" checkout --force --quiet "$GPT_SOVITS_COMMIT" \
        || die "Could not switch the voice engine to its tested version." \
               "Re-run the installer, or report this."
fi
[ "$(git -C "$GPT" rev-parse HEAD 2>/dev/null)" = "$GPT_SOVITS_COMMIT" ] \
    || die "The voice engine is not on its tested version." "Re-run the installer, or report this."
log "  Voice engine is on the tested version."

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
# app environment (3.10), so it gets its own toolbox; the sidecar's venv is
# built from it at the end of this step.
ensure_env "amadeus-cm" "3.13"

pip_in_env "amadeus" install --quiet --upgrade pip >/dev/null 2>&1 || true
# ddgs: the DuckDuckGo search library behind her web search (weather is
# separate - it uses a keyless Open-Meteo feed and needs no library).
# e2k: a small offline English->katakana model (public-domain code; its model
# is trained on CC-BY-SA dictionaries) so her voice says English words the
# Japanese way instead of switching to an English accent.
# pillow: checks and shrinks the pictures you show her (and removes their
# location data) before her model sees them. pypdf + python-docx: read the
# PDF and Word documents you give her.
pip_in_env "amadeus" install Flask flask-cors requests tqdm langchain langchain-openai pydantic ddgs e2k pillow pypdf python-docx \
    || die "Could not install the app's packages." "See the install log for details: $INSTALL_LOG"

# Smoke test: the whole backend must import cleanly in this toolbox.
# ddgs is imported lazily by the backend (web search), so also check it
# explicitly: a machine that lost the search library must never hide
# behind a passing smoke test.
smoke="$(cd "$PROJ/backend" && run_in_env "amadeus" "import api; import ddgs; import e2k; import PIL; import pypdf; import docx; print('SMOKE_OK')" 2>&1 | tail -1)"
if [ "${smoke}" = "SMOKE_OK" ]; then
    log "  App toolbox ready (startup smoke test passed)."
else
    log "  WARNING: the startup smoke test did not pass. If Amadeus fails to start later, send the log."
    printf '%s%s%s\n' "$C_DIM" "$smoke" "$C_RESET" | tee_to
fi

# Her memory (the CharacterMemory sidecar: its venv + about 1.3 GB of
# packages), set up HERE rather than on her first start - by the launcher's
# own setup code, so install and launch can never do it two different ways
# (the launcher still repairs it at start if this could not finish). Not
# fatal: she works without it.
log "  Setting up her memory (about 1.3 GB, a few minutes)..."
cm_py="$CONDA_HOME/envs/amadeus-cm/bin/python"
setup_args=("$PROJ/scripts/launcher.py" --setup-memory)
[ -x "$cm_py" ] && setup_args+=(--cm-python "$cm_py")
app_py="$CONDA_HOME/envs/amadeus/bin/python"
if [ -x "$app_py" ]; then
    "$app_py" "${setup_args[@]}" 2>&1 | grep -v '^Progress [0-9]* of [0-9]*$' | tee_to >/dev/null
    rc=${PIPESTATUS[0]}
else
    "$CONDA" run -n amadeus --no-capture-output python "${setup_args[@]}" 2>&1 \
        | grep -v '^Progress [0-9]* of [0-9]*$' | tee_to >/dev/null
    rc=${PIPESTATUS[0]}
fi
if [ "$rc" -eq 0 ]; then
    log "  Her memory is set up."
else
    log "  Her memory could not be set up right now - Amadeus finishes it when she starts."
    log "  (Details: $PROJ/logs/memory-setup.log)"
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
step 8 "Downloading her voice models (about 1.2 GB the first time)"

GPT_PKG="$GPT/GPT_SoVITS"
PRETRAINED="$GPT_PKG/pretrained_models"
mkdir -p "$PRETRAINED"

# Straight into the voice engine's folder (not the temp folder); a re-run
# skips the files already checked and continues a half-finished one.
while read -r vf_rel vf_bytes vf_sha; do
    [ -n "$vf_rel" ] || continue
    if ! get_verified_file "$VOICE_REPO_URL/$vf_rel?download=true" "$PRETRAINED/$vf_rel" \
            "$vf_bytes" "$vf_sha" "voice file $(basename "$vf_rel")" </dev/null; then
        die "Could not download her voice models." "$VF_ERROR"
    fi
done <<VOICE_LIST
$VOICE_FILES
VOICE_LIST
# The old full download (older installers) in the temp folder: ~13 GB
# nobody needs any more.
if [ -d "$TMP/GPT-SoVITS-hf" ]; then
    rm -rf "$TMP/GPT-SoVITS-hf"
    log "  Removed the old full voice-model download from the temp folder."
fi
log "  Voice models are in place."

# Pre-download the ~130 MB language-identification model her voice engine
# uses to tell which language a line is in. (The engine's own downloader
# refuses to run without its cache directory; her startup code creates it,
# but downloading HERE means her very first line never waits on the network.)
LID_FILE="$PRETRAINED/$LID_DEST_REL/lid.176.bin"
LID_BYTES=131266198   # the published size (the Windows installer checks the same file)
mkdir -p "$PRETRAINED/$LID_DEST_REL"
# A download cut off half-way used to stay as "already present" for good.
if [ -f "$LID_FILE" ] && [ "$(stat -f %z "$LID_FILE" 2>/dev/null || echo 0)" != "$LID_BYTES" ]; then
    rm -f "$LID_FILE"
fi
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
    if run_in_env "GPTSoVits" "$lid_code" "$LID_FILE.part" "$LID_URL" >/dev/null 2>&1 \
        && [ "$(stat -f %z "$LID_FILE.part" 2>/dev/null || echo 0)" = "$LID_BYTES" ]; then
        mv -f "$LID_FILE.part" "$LID_FILE"
        log "  Voice language model pre-downloaded."
    else
        rm -f "$LID_FILE.part"
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
#  STEP 9: her built-in local model (option A) - or, for option B on an
#  Apple-chip Mac, llama.cpp alone (to run a model of her own later)
# ----------------------------------------------------------------------------
LLM_READY=0
if [ "$CHOICE_MODE" = "builtin" ]; then
    step 9 "Setting up her built-in local model - $(model_field "$CHOICE_MODEL" 2) (big download, be patient)"
else
    step 9 "Setting up the local-model helper (llama.cpp)"
fi
if install_local_llm "$PROJ"; then LLM_READY=1; fi

# ----------------------------------------------------------------------------
#  STEP 10: the web page (Node.js packages)
# ----------------------------------------------------------------------------
step 10 "Preparing the web page"

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
#  STEP 11: done
# ----------------------------------------------------------------------------
step 11 "All done"

printf '%s\n' "" | tee_to
printf '%s%s AMADEUS IS READY! %s%s\n' "$C_BOLD" "$C_GRN" "$C_BOLD" "$C_RESET" | tee_to
printf '%s\n' "" | tee_to
if [ "$CHOICE_MODE" = "builtin" ]; then
    if [ "$LLM_READY" = 1 ]; then
        log "  Everything is installed. Amadeus thinks with her built-in local model ($(model_field "$CHOICE_MODEL" 2)) - nothing to connect."
    else
        printf '%s%s%s\n' "$C_YEL" "  Everything is installed EXCEPT her built-in local model (see the messages above)." "$C_RESET" | tee_to
        printf '%s%s%s\n' "$C_YEL" "  Run this installer again when you can - it continues the download where it stopped." "$C_RESET" | tee_to
    fi
    log "  To START Amadeus, double-click this file:"
    log "       $PROJ/start_macos.command"
    log "  (You can switch to your own model or a cloud service any time in Settings -> Connection.)"
    log "  When another program needs the memory, start her with start_macos_no_model.command instead"
    log "  (load her model later in Settings -> Connection, or unload it there any time)."
    log ""
    log "(Install log saved to: $INSTALL_LOG)"
    printf '%s\n' "" | tee_to
    read -r -p "Press Enter to close this window" _ || true
    exit 0
fi
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
if [ "$LLM_READY" = 1 ]; then
    log "  To run a GGUF model on this Mac instead: put the file in"
    log "       $MODELS_DIR"
    log "     then choose Built-in in Settings -> Connection and press Refresh list."
fi
log ""
log "(Install log saved to: $INSTALL_LOG)"

printf '%s\n' "" | tee_to
read -r -p "Press Enter to close this window" _ || true
