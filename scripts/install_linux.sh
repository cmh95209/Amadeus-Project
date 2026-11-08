#!/usr/bin/env bash
# ============================================================================
#  AMADEUS - ONE-SHOT INSTALLER FOR LINUX   v1.0  (resumable / safe to re-run)
#  This is the official Linux installer, served from the fork to new Linux
#  installs. It follows the macOS installer (install_macos.sh) step for step,
#  with the Windows installer's graphics-card rules for her built-in model.
#
#  Installs everything Amadeus needs to run on a Linux PC:
#    - the app itself (a copy of the project, straight from the fork)
#    - her three Python toolboxes (the app, her long-term memory, her voice)
#    - the voice engine and its voice models
#    - the web page (Node.js)
#    - optionally her own built-in local model (Gemma 4 + llama.cpp)
#
#  Nothing is installed system-wide: everything lands in your home folder,
#  so no administrator (sudo) password is needed. Tools this PC lacks (git,
#  curl, Git-LFS, FFmpeg) come from conda-forge into ~/.amadeus-tools.
#  The one thing this installer cannot do for you: your graphics card's
#  driver (NVIDIA's own driver for NVIDIA cards) - install it the usual way
#  for your Linux version first, if you want her to use the card.
#
#  TESTED ON: Ubuntu 22.04 / 24.04 and Debian 12 (and versions based on
#  them, like Linux Mint and Pop!_OS), 64-bit Intel/AMD processors (x86_64).
#  Other Linux versions should work, but are not tested.
#
#  HOW TO RUN (in a terminal):
#     wget -O install_linux.sh "<installer URL from the fork>"
#     bash install_linux.sh                 # lands in ~/Amadeus
#     bash install_linux.sh ~/Some/Folder   # or anywhere you like
#  (curl -fsSL "<URL>" -o install_linux.sh works too.)
#
#  TEST OPTIONS (for the project's own test installs; nobody else needs
#  them - they change only where the code comes from, never the steps):
#     --branch NAME             install that branch of the fork (default: main)
#     --simulate-gpu nvidia|nvidia-nodriver|amd|intel|none   pretend the card
#     --simulate-vram-gb N      pretend the card has N GB of graphics memory
#     --simulate-driver X.Y     pretend the NVIDIA driver version
#     --simulate-ram-gb N       pretend this PC has N GB of memory
#     --hardware-check-only [--json]   only show which model this PC gets
#     --skip-llm-download       all screens, but no model downloads
#     --llm-screens-only        ONLY the model screens + its settings file
#
#  WHAT'S NEW IN v1.0 (November 2026, app v3.0) - first Linux release:
#   - The same steps, questions and checks as the Mac installer v1.6.
#   - Her built-in model follows the Windows rules: an NVIDIA card (with
#     NVIDIA's driver) runs her model with llama.cpp's CUDA build and her
#     voice on the card; an AMD or Intel card runs her model with the
#     Vulkan build and her voice on the processor. The model size follows
#     the card's memory (the same table as on Windows).
#   - No sudo: missing basics (git, curl, Git-LFS, FFmpeg) come from
#     conda-forge; Node.js is the official build, in your home folder.
#   - Adds "Amadeus" to your desktop's app menu, plus start_linux.sh.
#
#  BEFORE RUNNING, HAVE AT HAND (optional, but makes it faster):
#   - nothing required; the installer fetches everything it needs.
#   - Option A (graphics card with 6 GB+): about 9 GB of free disk for her
#     model (plus about 1 GB for llama.cpp's NVIDIA build).
#   - Option B: a cloud service (OpenRouter, OpenAI, ...) or a local model
#     server you already run (Ollama, LM Studio, llama.cpp). You connect
#     her to it AFTER install, from her page.
#
#  AFTER IT FINISHES:
#   Open your app menu and choose "Amadeus" (or run
#   ~/Amadeus/Amadeus-Project/start_linux.sh) and follow her introduction.
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
#  Configuration (keep in sync with the Windows and Mac installers)
# ----------------------------------------------------------------------------
REPO_URL="https://github.com/cmh95209/Amadeus-Project"
BRANCH="main"
INSTALLER_VERSION="v1.0"
LEGACY_BRANCH="feature/character-memory-spike"
GPT_REPO_URL="https://github.com/RVC-Boss/GPT-SoVITS.git"
HF_REPO_URL="https://huggingface.co/lj1995/GPT-SoVITS"

# Miniforge: the community-maintained conda build (no license conditions).
# Stable "latest release" links - they always point at the newest build.
MINIFORGE_URL_X64="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh"
MINIFORGE_URL_ARM="https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-aarch64.sh"

# Node.js (for the web page). Pinned LTS release (the same as on Windows and
# Mac); the official tarball is unpacked into the home folder.
NODE_VER="v24.21.0"
NODE_DIST_BASE="https://nodejs.org/dist/$NODE_VER"

# The Hugging Face file that must be present for her voice to work at all.
HF_MARKER="s2G488k.pth"

# The ~130 MB language-identification model her voice engine uses to tell
# which language a line is in (the same address as the other installers).
LID_URL="https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin"
LID_DEST_REL="GPT_SoVITS/pretrained_models/fast_langdetect"

NLTK_RESOURCES="averaged_perceptron_tagger_eng averaged_perceptron_tagger cmudict"

# The voice engine's graphics-card PyTorch (CUDA 12.8 build) runs on cards
# of generation ("compute capability") 7.5 or newer = RTX 20 / GTX 16 series
# onwards - the same rule as the Windows installer.
VOICE_MIN_COMPUTE_CAP="7.5"

# Her voice engine's toolbox. Spelled exactly the way the launcher starts it
# ("conda run -n GPTSoVITS"): Linux tells upper and lower case apart.
VOICE_ENV="GPTSoVITS"

# ----------------------------------------------------------------------------
#  Machine layout (detected up front)
# ----------------------------------------------------------------------------
ARCH="$(uname -m)"
IS_X64=0
case "$ARCH" in x86_64|amd64) IS_X64=1 ;; esac
IS_WSL=0
if grep -qi microsoft /proc/version 2>/dev/null; then IS_WSL=1; fi

# A scratch area that exists on every Linux (set by the system, or /tmp).
TMP="${TMPDIR:-/tmp}"

# Tools the installer may place in the user's home folder (never system-wide).
TOOLS_DIR="$HOME/.amadeus-tools"

# Where Amadeus itself lands (the first argument that is not an option, or
# the user's Amadeus folder), plus the test options (see the top).
INSTALL_DIR=""
SIM_RAM_GB=-1
SIM_GPU=""
SIM_VRAM_GB=-1
SIM_DRIVER=""
HARDWARE_CHECK_ONLY=0
JSON_OUT=0
SKIP_LLM_DOWNLOAD=0
LLM_SCREENS_ONLY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --branch)          BRANCH="${2:-}"; shift; [ $# -gt 0 ] && shift ;;
        --simulate-ram-gb) SIM_RAM_GB="${2:-}"; shift; [ $# -gt 0 ] && shift ;;
        --simulate-gpu)    SIM_GPU="${2:-}"; shift; [ $# -gt 0 ] && shift ;;
        --simulate-vram-gb) SIM_VRAM_GB="${2:-}"; shift; [ $# -gt 0 ] && shift ;;
        --simulate-driver) SIM_DRIVER="${2:-}"; shift; [ $# -gt 0 ] && shift ;;
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
case "$SIM_VRAM_GB" in -1|[0-9]|[0-9][0-9]|[0-9][0-9][0-9]) ;; *) printf 'Invalid --simulate-vram-gb: %s\n' "$SIM_VRAM_GB"; exit 2 ;; esac
case "$SIM_GPU" in ''|nvidia|nvidia-nodriver|amd|intel|none) ;; *) printf 'Invalid --simulate-gpu: %s (nvidia, nvidia-nodriver, amd, intel or none)\n' "$SIM_GPU"; exit 2 ;; esac
case "$SIM_DRIVER" in ''|[0-9]*) ;; *) printf 'Invalid --simulate-driver: %s\n' "$SIM_DRIVER"; exit 2 ;; esac
case "$SIM_DRIVER" in *[!0-9.]*) printf 'Invalid --simulate-driver: %s\n' "$SIM_DRIVER"; exit 2 ;; esac
INSTALL_DIR="${INSTALL_DIR:-$HOME/Amadeus}"
case "$INSTALL_DIR" in /*) ;; *) INSTALL_DIR="$PWD/$INSTALL_DIR" ;; esac
# Everything below works from the home folder: started inside some git
# folder, git (and "git lfs install") would act on THAT folder - or stop
# when it cannot read it (2026-11-08, WSL test: voice models arrived as
# Git-LFS placeholders).
cd "$HOME" 2>/dev/null || cd /
PROJ="$INSTALL_DIR/Amadeus-Project"
INSTALL_LOG="$INSTALL_DIR/install_linux.log"

CONDA_HOME=""     # resolved in step 2 (existing conda or the one we install)
CONDA=""          # the conda executable itself
NODE_BIN_DIR=""   # directory holding node+npm (system one or ours)
NPM=""

# ----------------------------------------------------------------------------
#  Helpers
# ----------------------------------------------------------------------------
ensure_tools_dir() { mkdir -p "$TOOLS_DIR" "$TOOLS_DIR/bin"; }

# Download $1 to $2 with whichever tool this PC has (Ubuntu 24.04 ships wget
# but no curl). Only for the first downloads: from step 2 on, curl is always
# there (the system's, or conda-forge's in ~/.amadeus-tools).
fetch() {
    if command -v curl >/dev/null 2>&1; then
        curl -L --fail --retry 3 --retry-delay 5 --connect-timeout 30 --progress-bar -o "$2" "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -q --show-progress --tries=3 -O "$2" "$1" || { rm -f "$2"; return 1; }
    else
        return 1
    fi
}

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
# directly (the Windows v4.11 reliability fix); 'conda run' only as fallback.
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

# >>> BUILT-IN MODEL FUNCTIONS (tests/test_installer_linux.py runs them)
# ============================================================================
#  Her built-in local model (option A) - the same design as the Windows
#  installer (v4.13) and the Mac installer (v1.6): the A/B question first, a
#  look at this PC, background downloads, every file checked (size +
#  SHA-256), llama.cpp's own start test, and the app's settings file
#  backend/data/local_llm.json.
# ============================================================================
# >>> LINUX TIER TABLE (copy of the PC tiers, the models and the "linux"
#     llama.cpp builds of scripts/local_llm_tiers.json - tests/test_llm_tiers.py
#     keeps the two equal)
LLAMA_RELEASE="b11400"
DEDICATED_CARD_MIN_GB=2
VOICE_ON_CPU_MIN_RAM_GB=16
DISK_HEADROOM_GB=2
# build min_nvidia_driver (0 = any card), then name bytes sha256 per file
LINUX_BUILDS="cuda-13.4 580 llama-b11400-bin-ubuntu-cuda-13.4-x64.tar.gz 152449896 2e297621e5def65c0f19aa8ee6967b98ee83b359bad7ca000ae3c7908507c81d cudart-llama-b11400-bin-ubuntu-cuda-13.4-x64.tar.gz 440236489 9d040ad50388ca51b4c43dedf6410b5a37ed01634e14ca9a6cc7131749807b39
cuda-12.8 570 llama-b11400-bin-ubuntu-cuda-12.8-x64.tar.gz 171555633 7cc6d9ae0ff51ab91f2ca5a0d57bd79f018258c5c2892947548dc2dc146be101 cudart-llama-b11400-bin-ubuntu-cuda-12.8-x64.tar.gz 594377603 efd4bd1e61d0100e837e129ece19fc0953bd519fde77702ca31a6d54f6afd63a
vulkan 0 llama-b11400-bin-ubuntu-vulkan-x64.tar.gz 31617783 5912d3ac7eb6ac4a46503309c701213c2a162a71e57a137c6c7f1d295f08a7c4"
# id min_vram_gb model context kv_cache voice_device history_default vram_need_gb warning(1/0)
PC_TIERS="6-7 6 12B-Q3 16384 q4_0 cpu 6000 4.6 1
8-11 8 12B-Q3 16384 q4_0 cpu 6000 6.5 0
12-15 12 12B 24576 q8_0 cuda 14000 9.6 0
16-23 16 12B 32768 f16 cuda 22000 11.0 0
24-plus 24 12B 49152 f16 cuda 32000 11.5 0"
# key|label|repo|revision|file|bytes|sha256|vision file|vision saved as|vision bytes|vision sha256
PC_MODELS="12B|Gemma 4 12B (QAT)|unsloth/gemma-4-12b-it-qat-GGUF|980b060c40a8539ac159e0501a3e0f66a6365af3|gemma-4-12B-it-qat-UD-Q4_K_XL.gguf|6716356800|90fd44e29e0d7cffeb0fd00dc73cfdab9ed0b0e95306ecf7821ea634c940c370|mmproj-F16.gguf|gemma-4-12B-it-qat-mmproj-F16.gguf|175115840|ecc4e93128da8363b7dbf2193eab98cf1142353f52ceaa0c95c0872997aaadd3
12B-Q3|Gemma 4 12B (3-bit, for 8 GB cards)|unsloth/gemma-4-12b-it-GGUF|fc034cfff751157913579611efad8462ac1be606|gemma-4-12b-it-Q3_K_M.gguf|5693872960|dbf58f4c413b82b27106867eb42f7087e4172a172f6f201e2e11598a6582e504|mmproj-F16.gguf|gemma-4-12b-it-mmproj-F16.gguf|175115840|91f086971e56d7a7d8d39e271873fccdb49541bd259d6e02c401a4f1cb7a219e"
# <<< LINUX TIER TABLE

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
    line="$(printf '%s\n' "$PC_MODELS" | grep "^$1|" | head -1)"
    printf '%s' "$line" | cut -d'|' -f"$2"
}

# The files of one llama.cpp build, one "name bytes sha256" per line.
build_files() {
    printf '%s\n' "$LINUX_BUILDS" | awk -v b="$1" '$1 == b { for (i = 3; i + 2 <= NF; i += 3) print $i, $(i + 1), $(i + 2) }'
}

# Nearest whole GB, halves rounded up (the same rule as the Windows
# installer and scripts/llm_tiers.py round_gb).
round_gb_bytes() { echo $(( ($1 + 536870912) / 1073741824 )); }

# GB with one decimal, rounded UP (download sizes).
bytes_to_gb_up() { awk -v b="$1" 'BEGIN { g = b / 1073741824 * 10; i = int(g); if (g > i) i++; printf "%.1f", i / 10 }'; }

# "610.88" -> 610; anything unreadable -> 0 (scripts/llm_tiers.py driver_major).
driver_major() {
    local head="${1%%.*}"
    case "$head" in ''|*[!0-9]*) echo 0 ;; *) echo "$head" ;; esac
}

# Which llama.cpp build: the newest CUDA build the NVIDIA driver can run,
# else Vulkan (AMD / Intel / an old NVIDIA driver) - the same rule as
# scripts/llm_tiers.py pick_build with the table's "linux" builds.
pick_build() {
    local vendor="$1" major best="" best_need=-1 b need rest
    if [ "$vendor" = "nvidia" ]; then
        major="$(driver_major "$2")"
        while read -r b need rest; do
            [ -n "$b" ] || continue
            if [ "$need" -gt 0 ] && [ "$major" -ge "$need" ] && [ "$need" -gt "$best_need" ]; then
                best="$b"; best_need="$need"
            fi
        done <<< "$LINUX_BUILDS"
    fi
    echo "${best:-vulkan}"
}

# The decision for this PC - the same rules as scripts/llm_tiers.py
# choose_linux (the project's tests run both on the same inputs).
# $1 vendor (nvidia / nvidia-nodriver / amd / intel / "")  $2 graphics GB
# $3 memory GB (-1 = unknown)  $4 NVIDIA driver version  $5 x86_64 (1/0)
# Sets CH_OFFERED (1/0), CH_REASON, and when offered CH_TIER, CH_MODEL,
# CH_CONTEXT, CH_KV, CH_VOICE, CH_HISTORY, CH_NEED, CH_WARN, CH_BUILD.
pc_choose() {
    local vendor="$1" vram="$2" ram="$3" driver="$4" x64="$5"
    local id min model ctx kv voice hist need warn
    CH_OFFERED=0; CH_REASON=""; CH_TIER=""; CH_MODEL=""; CH_CONTEXT=""; CH_KV=""
    CH_VOICE=""; CH_HISTORY=""; CH_NEED=""; CH_WARN=0; CH_BUILD=""
    if [ "$x64" != 1 ]; then CH_REASON="not_x64"; return 0; fi
    if [ "$vendor" = "nvidia-nodriver" ]; then CH_REASON="nvidia_no_driver"; return 0; fi
    if [ -z "$vendor" ] || [ "$vram" -lt "$DEDICATED_CARD_MIN_GB" ] 2>/dev/null; then CH_REASON="no_card"; return 0; fi
    while read -r id min model ctx kv voice hist need warn; do
        [ -n "$id" ] || continue
        if [ "$vram" -ge "$min" ] 2>/dev/null; then
            CH_TIER="$id"; CH_MODEL="$model"; CH_CONTEXT="$ctx"; CH_KV="$kv"; CH_VOICE="$voice"
            CH_HISTORY="$hist"; CH_NEED="$need"; CH_WARN="$warn"
        fi
    done <<< "$PC_TIERS"
    if [ -z "$CH_TIER" ]; then CH_REASON="small_card"; return 0; fi
    # Her voice engine can use only NVIDIA cards; on any other card it runs
    # on the processor, which needs plenty of normal memory.
    [ "$vendor" = "nvidia" ] || CH_VOICE="cpu"
    if [ "$CH_VOICE" = "cpu" ] && [ "$ram" -ge 0 ] 2>/dev/null && [ "$ram" -lt "$VOICE_ON_CPU_MIN_RAM_GB" ]; then
        CH_TIER=""; CH_MODEL=""; CH_CONTEXT=""; CH_KV=""; CH_VOICE=""; CH_HISTORY=""; CH_NEED=""; CH_WARN=0
        CH_REASON="low_ram"; return 0
    fi
    CH_OFFERED=1; CH_BUILD="$(pick_build "$vendor" "$driver")"
}

# Everything option A downloads (bytes): model + its vision file + llama.cpp.
llm_download_bytes() {
    local bytes
    bytes=$(( $(model_field "$1" 6) + $(model_field "$1" 10) ))
    bytes=$(( bytes + $(build_files "$2" | awk '{ s += $2 } END { print s + 0 }') ))
    echo "$bytes"
}

# Free space (GB, one decimal) where $1 is (or will be), -1 when unknown.
free_disk_gb() {
    local d="$1"
    while [ -n "$d" ] && [ ! -d "$d" ]; do d="$(dirname "$d")"; done
    df -Pk "${d:-/}" 2>/dev/null | awk 'NR == 2 { printf "%.1f", int($4 / 1048576 * 10) / 10; found = 1 } END { if (!found) print "-1" }'
}

# The biggest graphics card (sets CARD_VENDOR, CARD_NAME, CARD_VRAM_GB,
# CARD_DRIVER, CARD_CAP). NVIDIA: nvidia-smi (exact memory, driver and
# generation). AMD / Intel: the kernel's own record in /sys (amdgpu reports
# the card's memory there). An NVIDIA card the kernel lists but nvidia-smi
# cannot report has no NVIDIA driver: "nvidia-nodriver". Built-in graphics
# (little or no memory of their own) count as no card.
detect_card() {
    local out line name mem drv cap gb d vendor_id vendor bytes nv_seen=0
    CARD_VENDOR=""; CARD_NAME=""; CARD_VRAM_GB=0; CARD_DRIVER=""; CARD_CAP=""
    if command -v nvidia-smi >/dev/null 2>&1; then
        out="$(nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader,nounits 2>/dev/null)"
        # A driver too old for the compute_cap field answers with an error.
        printf '%s\n' "$out" | grep -q '^[^,]*, *[0-9]' \
            || out="$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>/dev/null)"
        while IFS=',' read -r name mem drv cap; do
            mem="$(printf '%s' "$mem" | tr -d ' ')"
            case "$mem" in ''|*[!0-9]*) continue ;; esac
            gb=$(( (mem + 512) / 1024 ))
            if [ -z "$CARD_VENDOR" ] || [ "$gb" -gt "$CARD_VRAM_GB" ]; then
                CARD_VENDOR="nvidia"; CARD_VRAM_GB="$gb"
                CARD_NAME="$(printf '%s' "$name" | sed 's/^ *//; s/ *$//')"
                CARD_DRIVER="$(printf '%s' "$drv" | tr -d ' ')"
                CARD_CAP="$(printf '%s' "${cap:-}" | tr -d ' ')"
                case "$CARD_CAP" in [0-9]*.[0-9]*|[0-9]) ;; *) CARD_CAP="" ;; esac
            fi
        done <<< "$out"
    fi
    [ -n "$CARD_VENDOR" ] && return 0
    for d in /sys/class/drm/card*; do
        case "${d##*/}" in card*-*) continue ;; esac   # a screen connection, not a card
        [ -r "$d/device/vendor" ] || continue
        vendor_id="$(cat "$d/device/vendor" 2>/dev/null)"
        case "$vendor_id" in
            0x10de) nv_seen=1; continue ;;
            0x1002) vendor="amd" ;;
            0x8086) vendor="intel" ;;
            *) continue ;;
        esac
        bytes="$(cat "$d/device/mem_info_vram_total" 2>/dev/null || echo 0)"
        case "$bytes" in ''|*[!0-9]*) bytes=0 ;; esac
        gb="$(round_gb_bytes "$bytes")"
        if [ "$gb" -ge "$DEDICATED_CARD_MIN_GB" ] && { [ -z "$CARD_VENDOR" ] || [ "$gb" -gt "$CARD_VRAM_GB" ]; }; then
            CARD_VENDOR="$vendor"; CARD_VRAM_GB="$gb"
            CARD_NAME="$([ "$vendor" = amd ] && echo "AMD graphics card" || echo "Intel graphics card")"
        fi
    done
    if [ -z "$CARD_VENDOR" ] && [ "$nv_seen" = 1 ]; then
        CARD_VENDOR="nvidia-nodriver"; CARD_NAME="NVIDIA graphics card (no NVIDIA driver found)"
    fi
}

# Normal memory in whole GB, -1 when unknown. Linux reports a little less
# than what is fitted (the kernel and built-in graphics keep some), so the
# number is rounded UP: a 16 GB PC that shows 15.3 GB still counts as 16.
detect_ram_gb() {
    local kb
    kb="$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo 2>/dev/null)"
    case "$kb" in ''|*[!0-9]*) echo -1 ;; *) echo $(( (kb + 1048575) / 1048576 )) ;; esac
}

# Look at this PC (honouring the --simulate-* test options) and decide.
# Sets HW_* and the CH_* decision.
hardware_check() {
    HW_SIMULATED=0
    HW_X64="$IS_X64"
    if [ -n "$SIM_GPU" ]; then
        HW_SIMULATED=1
        CARD_VENDOR="$SIM_GPU"; [ "$SIM_GPU" = "none" ] && CARD_VENDOR=""
        CARD_NAME="(pretend ${SIM_GPU} card)"; CARD_VRAM_GB=0; CARD_DRIVER=""; CARD_CAP=""
        if [ "$SIM_GPU" = "nvidia" ]; then CARD_DRIVER="610.88"; fi
    else
        detect_card
    fi
    if [ "$SIM_VRAM_GB" -ge 0 ] 2>/dev/null; then HW_SIMULATED=1; CARD_VRAM_GB="$SIM_VRAM_GB"; fi
    if [ -n "$SIM_DRIVER" ]; then HW_SIMULATED=1; CARD_DRIVER="$SIM_DRIVER"; fi
    HW_RAM_GB="$(detect_ram_gb)"
    if [ "$SIM_RAM_GB" -ge 0 ] 2>/dev/null; then HW_SIMULATED=1; HW_RAM_GB="$SIM_RAM_GB"; fi
    HW_DISK_GB="$(free_disk_gb "$INSTALL_DIR")"
    pc_choose "$CARD_VENDOR" "$CARD_VRAM_GB" "$HW_RAM_GB" "$CARD_DRIVER" "$HW_X64"
    HW_DL_GB=0
    if [ "$CH_OFFERED" = 1 ]; then HW_DL_GB="$(bytes_to_gb_up "$(llm_download_bytes "$CH_MODEL" "$CH_BUILD")")"; fi
}

build_label() {
    case "$1" in
        cuda-*) echo "NVIDIA (CUDA ${1#cuda-}) build" ;;
        vulkan) echo "Vulkan build" ;;
        *) echo "$1 build" ;;
    esac
}

# Plain-language summary lines of a hardware check (screen + log).
format_hardware_check() {
    if [ -n "$CARD_VENDOR" ]; then
        log "  Graphics card : $CARD_NAME$([ "$CARD_VRAM_GB" -gt 0 ] 2>/dev/null && echo " - $CARD_VRAM_GB GB")$([ -n "$CARD_DRIVER" ] && echo ", driver $CARD_DRIVER")"
    else
        log "  Graphics card : none found (or built-in graphics only)"
    fi
    if [ "$HW_RAM_GB" -ge 0 ] 2>/dev/null; then log "  Memory        : $HW_RAM_GB GB"
    else log "  Memory        : unknown"; fi
    log "  Free disk     : $HW_DISK_GB GB"
    if [ "$CH_OFFERED" = 1 ]; then
        log "  Local model   : $(model_field "$CH_MODEL" 2) ($HW_DL_GB GB download, with its helper program)"
        log "  Settings      : $CH_CONTEXT context, $CH_KV memory, voice on the $([ "$CH_VOICE" = cuda ] && echo "graphics card" || echo "processor"), $(build_label "$CH_BUILD")"
    else
        case "$CH_REASON" in
            not_x64)          log "  Local model   : not offered - it needs a 64-bit Intel/AMD processor (option B only)" ;;
            nvidia_no_driver) log "  Local model   : not offered - NVIDIA's driver is not installed (option B for now)" ;;
            small_card)       log "  Local model   : not offered - it needs a graphics card with at least 6 GB (option B only)" ;;
            low_ram)          log "  Local model   : not offered - her voice would need at least $VOICE_ON_CPU_MIN_RAM_GB GB of memory (option B only)" ;;
            *)                log "  Local model   : not offered - it needs a graphics card (option B only)" ;;
        esac
    fi
    if [ "$HW_SIMULATED" = 1 ]; then log "  (TEST: pretend hardware values in use)"; fi
}

json_str() { printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"; }
json_bool() { if [ "$1" = 1 ]; then printf 'true'; else printf 'false'; fi; }

# One line of JSON (the project's tests read it): --hardware-check-only --json.
hardware_json() {
    local choice
    if [ "$CH_OFFERED" = 1 ]; then
        choice="{\"offered\": true, \"reason\": \"\", \"tier\": \"$CH_TIER\", \"model\": \"$CH_MODEL\", \"context\": $CH_CONTEXT, \"kv_cache\": \"$CH_KV\", \"voice_device\": \"$CH_VOICE\", \"history_default\": $CH_HISTORY, \"vram_need_gb\": $CH_NEED, \"warning\": $(json_bool "$CH_WARN"), \"build\": \"$CH_BUILD\"}"
    else
        choice="{\"offered\": false, \"reason\": \"$CH_REASON\"}"
    fi
    printf '{"x64": %s, "vendor": %s, "card": %s, "vram_gb": %s, "driver": %s, "ram_gb": %s, "disk_free_gb": %s, "simulated": %s, "download_gb": %s, "choice": %s}\n' \
        "$(json_bool "$HW_X64")" "$(json_str "$CARD_VENDOR")" "$(json_str "$CARD_NAME")" "$CARD_VRAM_GB" \
        "$(json_str "$CARD_DRIVER")" "$HW_RAM_GB" "$HW_DISK_GB" "$(json_bool "$HW_SIMULATED")" "$HW_DL_GB" "$choice"
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
    CHOICE_CONTEXT="$CH_CONTEXT"; CHOICE_KV="$CH_KV"; CHOICE_VOICE="$CH_VOICE"; CHOICE_HISTORY="$CH_HISTORY"
    CHOICE_NEED="$CH_NEED"; CHOICE_SIMULATED="$HW_SIMULATED"
    CHOICE_CHOSEN="${2:-$(date '+%Y-%m-%dT%H:%M:%S')}"
}
write_choice_file() {
    mkdir -p "$MODELS_DIR"
    if [ "$CHOICE_MODE" = "builtin" ]; then
        printf '{\n    "mode": "builtin",\n    "model": "%s",\n    "tier": "%s",\n    "build": "%s",\n    "context": %s,\n    "kv_cache": "%s",\n    "voice_device": "%s",\n    "history_default": %s,\n    "vram_need_gb": %s,\n    "simulated": %s,\n    "chosen": "%s"\n}\n' \
            "$CHOICE_MODEL" "$CHOICE_TIER" "$CHOICE_BUILD" "$CHOICE_CONTEXT" "$CHOICE_KV" "$CHOICE_VOICE" \
            "$CHOICE_HISTORY" "$CHOICE_NEED" "$(json_bool "$CHOICE_SIMULATED")" "$CHOICE_CHOSEN" > "$LLM_CHOICE_FILE"
    else
        printf '{\n    "mode": "custom",\n    "chosen": "%s"\n}\n' "$CHOICE_CHOSEN" > "$LLM_CHOICE_FILE"
    fi
}

# Option A on this PC: show what was found, then confirm (or option B).
confirm_local_model() {
    local need
    log ""
    format_hardware_check
    log ""
    if [ "$CH_OFFERED" != 1 ]; then
        case "$CH_REASON" in
            not_x64)
                log "  Her built-in model needs a 64-bit Intel or AMD processor (this PC has: $ARCH). So Amadeus will use option B: you connect a model yourself (a cloud service such as OpenRouter, or your own model server)." ;;
            nvidia_no_driver)
                log "  This PC has an NVIDIA graphics card, but NVIDIA's own driver is not installed (or not working), so her model cannot use the card yet."
                log "  Install the NVIDIA driver the usual way for your Linux version (on Ubuntu: the 'Additional Drivers' app), restart the PC, then run this installer again to get her built-in model."
                log "  For now, Amadeus will use option B: you connect a model yourself (a cloud service such as OpenRouter, or your own model server)." ;;
            small_card)
                log "  Her built-in model needs a graphics card with at least 6 GB of its own memory (this one has $CARD_VRAM_GB GB). The smaller models that would fit don't sound like her, so Amadeus will use option B: you connect a model yourself (a cloud service such as OpenRouter, or your own model server)." ;;
            low_ram)
                log "  On this PC her voice has to run on the processor, which needs at least $VOICE_ON_CPU_MIN_RAM_GB GB of normal memory (this PC has $HW_RAM_GB GB). So Amadeus will use option B: you connect a model yourself (a cloud service such as OpenRouter, or your own model server)." ;;
            *)
                log "  Her built-in model needs a graphics card (NVIDIA, AMD or Intel, 6 GB or more). This PC has none, so Amadeus will use option B: you connect a model yourself (a cloud service such as OpenRouter, or your own model server)." ;;
        esac
        read -r -p "Press Enter to continue with option B " _ || true
        set_choice_custom; return 0
    fi
    if [ "$CH_WARN" = 1 ]; then
        log "  NOTICE: this graphics card has only $CARD_VRAM_GB GB. Her model then runs partly on the processor - she answers, but slowly."
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
                    CHOICE_VOICE="$(json_get "$LLM_CHOICE_FILE" voice_device)"; [ -n "$CHOICE_VOICE" ] || CHOICE_VOICE="cpu"
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
    log "    A) Install and set up a local model automatically (private, runs on this PC, no account needed)"
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
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
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

# conda-forge's libgomp (in ~/.amadeus-tools/libs) copied into $1 as
# libgomp.so.1. Returns 1 when it cannot be had.
provide_libgomp() {
    local libs="$TOOLS_DIR/libs" lib
    lib="$(ls "$libs/lib/"libgomp.so.1* 2>/dev/null | head -1)"
    if [ -z "$lib" ]; then
        [ -n "${CONDA:-}" ] || return 1
        rm -rf "$libs"
        "$CONDA" create -y -q -p "$libs" -c conda-forge --override-channels libgomp >>"$INSTALL_LOG" 2>&1 || return 1
        lib="$(ls "$libs/lib/"libgomp.so.1* 2>/dev/null | head -1)"
        [ -n "$lib" ] || return 1
    fi
    cp -L "$lib" "$1/libgomp.so.1"
}

# Which graphics system a build must see in llama-server --list-devices.
build_device_word() {
    case "$1" in cuda-*) echo "CUDA" ;; vulkan) echo "Vulkan" ;; *) echo "" ;; esac
}

# Download + unpack one llama.cpp build ($1, e.g. cuda-13.4) into
# models/llama.cpp/<release>-linux-<build>/ and prove its server program
# starts AND sees the graphics card. Sets LLAMA_EXE (or returns 1 with
# VF_ERROR). The NVIDIA builds come in two parts (llama.cpp + NVIDIA's
# runtime files); the runtime files are put next to llama-server, where it
# looks for them.
install_llama_build() {
    local build="$1" dir="$MODELS_DIR/llama.cpp/$LLAMA_RELEASE-linux-$1" done_file dl name bytes sha
    local found srv_dir sub ver code devices word files
    done_file="$dir/install_complete.txt"
    LLAMA_EXE=""
    if [ -f "$done_file" ] && [ -x "$(cat "$done_file")" ]; then
        LLAMA_EXE="$(cat "$done_file")"
        log "  Already have llama.cpp $LLAMA_RELEASE ($(build_label "$build")) - skipping."; return 0
    fi
    files="$(build_files "$build")"
    if [ -z "$files" ]; then VF_ERROR="Unknown llama.cpp build: $build"; return 1; fi
    dl="$MODELS_DIR/downloads"
    while read -r name bytes sha; do
        [ -n "$name" ] || continue
        get_verified_file "$(llama_url "$name")" "$dl/$name" "$bytes" "$sha" "llama.cpp $name" || return 1
    done <<< "$files"
    rm -rf "$dir"
    log "  Unpacking llama.cpp ($(build_label "$build"))..."
    while read -r name bytes sha; do
        [ -n "$name" ] || continue
        expand_tar_safe "$dl/$name" "$dir" || return 1
    done <<< "$files"
    found="$(find "$dir" -type f -name llama-server | head -1)"
    if [ -z "$found" ]; then VF_ERROR="The llama.cpp download ($build) has no llama-server."; return 1; fi
    srv_dir="$(dirname "$found")"
    for sub in "$dir"/*/; do
        sub="${sub%/}"
        [ -d "$sub" ] && [ "$sub" != "$srv_dir" ] || continue
        cp -a "$sub/." "$srv_dir/" && rm -rf "$sub"
    done
    chmod +x "$found" 2>/dev/null || true
    # llama.cpp's Linux builds need the OpenMP helper library (libgomp.so.1):
    # part of most desktops, but missing on minimal systems (2026-11-08,
    # WSL test: "error while loading shared libraries: libgomp.so.1"). Then
    # conda-forge's copy goes next to llama-server, where it looks first.
    if command -v ldd >/dev/null 2>&1 && ldd "$found" 2>/dev/null | grep -q 'libgomp\.so\.1 => not found'; then
        log "  (adding the helper library llama.cpp needs (libgomp) from conda-forge...)"
        provide_libgomp "$srv_dir" || log "  (could not add it right now - details in the install log)"
    fi
    # Start test: the program must run (catches a missing library file) ...
    ver="$("$found" --version 2>&1)"; code=$?
    printf '%s\n' "  llama-server --version (exit $code): $ver" >> "$INSTALL_LOG"
    if [ $code -ne 0 ]; then VF_ERROR="llama.cpp ($(build_label "$build")) would not start on this PC: $(printf '%s' "$ver" | tail -2)"; return 1; fi
    # ... and must see the graphics card (without it, it would quietly run
    # on the processor - far too slow for her).
    word="$(build_device_word "$build")"
    devices="$("$found" --list-devices 2>&1)"
    printf '%s\n' "  llama-server --list-devices: $devices" >> "$INSTALL_LOG"
    if [ -n "$word" ] && ! printf '%s\n' "$devices" | grep -q "$word"; then
        VF_ERROR="llama.cpp ($(build_label "$build")) could not see your graphics card. Make sure its driver is installed (for AMD/Intel cards: the Mesa Vulkan driver, package mesa-vulkan-drivers)."
        return 1
    fi
    printf '%s' "$found" > "$done_file"
    while read -r name bytes sha; do
        [ -n "$name" ] && rm -f "$dl/$name" "$dl/$name.verified"
    done <<< "$files"
    LLAMA_EXE="$found"
    log "  llama.cpp $LLAMA_RELEASE ($(build_label "$build")) ready."
}

# The chosen build, or - when an NVIDIA build cannot be set up (download
# or start test failed) - the Vulkan build, the same fallback as the
# Windows installer v4.13.2. Updates CHOICE_BUILD when it falls back.
install_llama_with_fallback() {
    local first_error
    install_llama_build "$CHOICE_BUILD" && return 0
    [ "$CHOICE_BUILD" != "vulkan" ] || return 1
    first_error="$VF_ERROR"
    log "  $first_error"
    log "  Trying llama.cpp's Vulkan build instead..."
    if install_llama_build vulkan; then CHOICE_BUILD="vulkan"; return 0; fi
    VF_ERROR="$first_error (the Vulkan build did not work either: $VF_ERROR)"
    return 1
}

# The app's built-in-model settings: backend/data/local_llm.json (the same
# fields as the Windows installer writes). Written even when a download
# failed (model_ok / server_ok false), so the app knows the model is
# missing. Option B on a PC that could run her model: the same file with
# mode "custom" (llama.cpp is installed, so Settings can switch to Built-in
# later). Option B elsewhere: an old file is kept, but switched to "custom".
# What the user changed in the app survives a re-run: the model file picked
# in Settings (selected_file, while it still exists) and the conversation
# memory number for the built-in model (history_budget).
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
    "voice_device": "$CHOICE_VOICE",
    "gpu_layers": 999,
    "port": $LLM_PORT,
    "history_default": $CHOICE_HISTORY,
    "vram_need_gb": $CHOICE_NEED,
    "simulated": $(json_bool "$CHOICE_SIMULATED"),
    "written": "$(date '+%Y-%m-%dT%H:%M:%S')",
    "installer": "linux-$INSTALLER_VERSION"$extra
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
        if install_llama_with_fallback; then exe="$LLAMA_EXE"; server_ok=1; else log "  $VF_ERROR"; fi
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

# Option B on a PC that could run her model: llama.cpp too (no model), so
# Settings -> Connection -> Built-in can run a GGUF of the user's own later,
# with this PC's tier settings.
install_local_model_runner() {
    local proj="$1" exe="" model_path model_ok=0
    local keep_mode="$CHOICE_MODE" keep_chosen="$CHOICE_CHOSEN"
    set_choice_from_check custom "$keep_chosen"
    mkdir -p "$MODELS_DIR"
    if [ "$SKIP_LLM_DOWNLOAD" = 1 ]; then log "  (TEST: --skip-llm-download - llama.cpp is not downloaded.)"
    elif install_llama_with_fallback; then exe="$LLAMA_EXE"
    else log "  $VF_ERROR"; fi
    model_path="$MODELS_DIR/$(model_field "$CHOICE_MODEL" 5)"
    # Her own tier model, if an earlier option-A install left it (checked).
    if [ -f "$model_path" ] && [ -f "$model_path.verified" ]; then model_ok=1; fi
    write_local_llm_config "$proj" custom "$exe" "$([ -n "$exe" ] && echo 1 || echo 0)" "$model_path" "$model_ok"
    CHOICE_MODE="$keep_mode"
    [ -n "$exe" ]
}

# ---------- the big downloads start in the background ----------
# Right after the A/B question, curl (no window) fetches llama.cpp + her
# model + its vision file into the same ".part" files get_verified_file
# uses: when the install reaches the model step it finds them finished (or
# half-finished, and continues them) and checks each one exactly as before.
# A re-run first stops a download an earlier run left. (No curl on this PC
# yet: the model step downloads them itself, with conda-forge's curl.)
bg_pid_file() { printf '%s' "$MODELS_DIR/downloads/background_$1.pid"; }

# Every file the chosen option downloads at its model step, one per line:
# url|dest|bytes|sha256 (already downloaded and checked ones left out).
local_llm_files() {
    local name bytes sha file build
    { [ "$CHOICE_MODE" = "builtin" ] || [ "$CH_OFFERED" = 1 ]; } || return 0
    build="${CHOICE_BUILD:-$CH_BUILD}"
    {
    if [ ! -f "$MODELS_DIR/llama.cpp/$LLAMA_RELEASE-linux-$build/install_complete.txt" ]; then
        build_files "$build" | while read -r name bytes sha; do
            [ -n "$name" ] && printf '%s|%s|%s|%s\n' "$(llama_url "$name")" "$MODELS_DIR/downloads/$name" "$bytes" "$sha"
        done
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
    command -v curl >/dev/null 2>&1 || return 0
    BG_TRIED=1
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

# >>> NEW DEFAULTS FUNCTIONS (the same as install_macos.sh)
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

# >>> APP MENU FUNCTIONS (tests/test_installer_linux.py runs them)
# Adds Amadeus to the desktop's app menu (the freedesktop.org standard every
# common Linux desktop follows): ~/.local/share/applications/*.desktop. The
# entry opens a terminal window running start_linux.sh (her status lines and
# "Press Enter to close" live there). $1 project folder  $2 1 = also the
# "without her model" entry (option A only; removed otherwise).
desktop_quote() { printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/`/\\`/g' -e 's/\$/\\$/g')"; }
write_menu_entries() {
    local proj="$1" with_no_model="$2" apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    local icon="$proj/frontend/public/icons/icon-512.png"
    mkdir -p "$apps" || return 1
    cat > "$apps/amadeus.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Amadeus
Comment=Start Amadeus
Exec=$(desktop_quote "$proj/start_linux.sh")
Path=$proj
Icon=$icon
Terminal=true
Categories=Utility;
EOF
    if [ "$with_no_model" = 1 ]; then
        cat > "$apps/amadeus-no-model.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Amadeus (without her model)
Comment=Start Amadeus without loading her built-in model
Exec=$(desktop_quote "$proj/start_linux_no_model.sh")
Path=$proj
Icon=$icon
Terminal=true
Categories=Utility;
EOF
    else
        rm -f "$apps/amadeus-no-model.desktop"
    fi
    chmod +x "$apps/amadeus.desktop" "$apps/amadeus-no-model.desktop" 2>/dev/null || true
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$apps" >/dev/null 2>&1
    return 0
}
# <<< APP MENU FUNCTIONS

# Test: only look at this PC and say which model it would get (installs
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
printf '%s AMADEUS INSTALLER for LINUX  %s%s\n' "$C_BOLD" "$INSTALLER_VERSION" "$C_RESET" | tee_to
printf '%s This installs everything Amadeus needs on this PC:%s\n' "$C_BOLD" "$C_RESET" | tee_to
printf '%s\n' "   - the app (into: $INSTALL_DIR)" | tee_to
printf '%s\n' "   - three Python toolboxes: the app, her memory, her voice" | tee_to
printf '%s\n' "   - her voice engine + voice models + the web page" | tee_to
printf '%s\n' "  Everything lands in your home folder - no sudo password." | tee_to
printf '%s It is safe to re-run this script at any time: finished steps are%s\n' "$C_BOLD" "$C_RESET" | tee_to
printf '%s skipped and missing pieces are repaired (downloads resume).%s\n' "$C_RESET" | tee_to
printf '%s%s%s\n' "$C_BOLD" "============================================================" "$C_RESET" | tee_to

mkdir -p "$INSTALL_DIR"
: > "$INSTALL_LOG"   # fresh log for this run

if [ "$BRANCH" != "main" ] || [ "$SIM_RAM_GB" != "-1" ] || [ -n "$SIM_GPU" ] || [ "$SIM_VRAM_GB" != "-1" ] \
        || [ -n "$SIM_DRIVER" ] || [ "$SKIP_LLM_DOWNLOAD" = 1 ]; then
    log "TEST OPTIONS in use:"
    if [ "$BRANCH" != "main" ]; then log "  branch            : $BRANCH"; fi
    if [ -n "$SIM_GPU" ]; then log "  pretend card      : $SIM_GPU"; fi
    if [ "$SIM_VRAM_GB" != "-1" ]; then log "  pretend card mem. : $SIM_VRAM_GB GB"; fi
    if [ -n "$SIM_DRIVER" ]; then log "  pretend driver    : $SIM_DRIVER"; fi
    if [ "$SIM_RAM_GB" != "-1" ]; then log "  pretend memory    : $SIM_RAM_GB GB"; fi
    if [ "$SKIP_LLM_DOWNLOAD" = 1 ]; then log "  no local-model downloads (screens + settings file only)"; fi
fi
if [ "$(uname -s)" != "Linux" ] && [ "$LLM_SCREENS_ONLY" != 1 ]; then
    die "This installer is for Linux (this machine is: $(uname -s))." \
        "Windows uses install_windows.ps1, Macs use install_macos.sh."
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
# update that brings a newer version and when they differ from it).
confirm_new_defaults
log ""
log "Everything else installs now. It downloads several gigabytes, so give it time."
read -r -p "Press Enter to begin " _ || true
start_background_downloads

# ----------------------------------------------------------------------------
#  STEP 1: sanity checks
# ----------------------------------------------------------------------------
step 1 "Checking your PC"

OS_NAME="unknown Linux"; OS_ID=""; OS_LIKE=""
if [ -r /etc/os-release ]; then
    OS_NAME="$(. /etc/os-release && printf '%s' "${PRETTY_NAME:-${NAME:-Linux}}")"
    OS_ID="$(. /etc/os-release && printf '%s' "${ID:-}")"
    OS_LIKE="$(. /etc/os-release && printf '%s' "${ID_LIKE:-}")"
fi
log "  Linux version : $OS_NAME"
log "  Processor     : $ARCH"
case " $OS_ID $OS_LIKE " in
    *" ubuntu "*|*" debian "*) ;;
    *) log "  NOTE: Amadeus is tested on Ubuntu and Debian (and Linux versions based on them). This one should work too, but is not tested - if something fails, send the install log." ;;
esac
if [ "$IS_X64" != 1 ]; then
    log "  NOTE: Amadeus is tested on 64-bit Intel/AMD processors (x86_64). On $ARCH it should work, but is not tested."
fi
if [ "$IS_WSL" = 1 ]; then
    log "  (This is Linux inside Windows (WSL) - fine for testing; on Windows itself, install_windows.ps1 is the usual way.)"
fi
format_hardware_check
if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    die "This PC has neither curl nor wget (one of them is needed to download anything)." \
        "Install one (on Ubuntu/Debian: sudo apt install wget), then run this installer again."
fi
for tool in tar gzip awk sed grep; do
    command -v "$tool" >/dev/null 2>&1 || die "The basic tool '$tool' is missing on this PC." \
        "Install it with your Linux version's package manager, then run this installer again."
done

# ----------------------------------------------------------------------------
#  STEP 2: prerequisites (conda, git, curl, Git-LFS, node, ffmpeg)
# ----------------------------------------------------------------------------
step 2 "Making sure the basic tools are present"

# --- 2a. Conda (the manager that builds her Python toolboxes, and - on this
#         PC - also the source of any missing basic tool).
#         Reuses an existing Miniconda/Anaconda/Miniforge if present.
conda_candidates=(
    "$HOME/miniforge3"
    "$HOME/miniconda3"
    "$HOME/anaconda3"
    "$HOME/mambaforge"
    "/opt/conda"
    "/opt/miniforge3"
    "/opt/miniconda3"
    "/opt/anaconda3"
)
CONDA_HOME=""
for cand in "${conda_candidates[@]}"; do
    if [ -x "$cand/bin/conda" ]; then CONDA_HOME="$cand"; break; fi
done
CONDA_INSTALLED_NEW=0
if [ -n "$CONDA_HOME" ]; then
    log "  Conda: found your existing install at $CONDA_HOME - reusing it."
else
    log "  Conda not found - installing Miniforge (the community conda build, ~90 MB)..."
    mf_url=$([ "$IS_X64" = 1 ] && echo "$MINIFORGE_URL_X64" || echo "$MINIFORGE_URL_ARM")
    fetch "$mf_url" "$TMP/miniforge-setup.sh" \
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
"$CONDA" tos accept --override-channels \
    --channel https://repo.anaconda.com/pkgs/main \
    --channel https://repo.anaconda.com/pkgs/r --all >/dev/null 2>&1 || true
"$CONDA" config --set notify_outdated_conda false >/dev/null 2>&1 || true
if [ $CONDA_INSTALLED_NEW = 1 ]; then
    # Also register conda with your terminal (bash) so you can use it directly.
    # Undo anytime with: conda init --reverse
    "$CONDA" init bash >/dev/null 2>&1 || true
    log "  (Conda was also added to your terminal's startup file - undo with 'conda init --reverse'.)"
fi

# --- 2b. git, curl and Git-LFS (the voice models are stored with Git-LFS).
#         Whatever this PC lacks comes from conda-forge into its own small
#         toolbox in ~/.amadeus-tools/basics - no sudo. Small launchers in
#         ~/.amadeus-tools/bin hand over to it (start_linux.sh puts that
#         folder on the path too).
BASICS_ENV="$TOOLS_DIR/basics"
ensure_tools_dir
case ":$PATH:" in *":$TOOLS_DIR/bin:"*) ;; *) export PATH="$TOOLS_DIR/bin:$PATH" ;; esac
hash -r
missing=()
for tool in git curl git-lfs; do
    command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
if [ ${#missing[@]} -eq 0 ]; then
    log "  git, curl, Git-LFS: present."
else
    log "  Setting up what this PC lacks (${missing[*]}) from conda-forge - a minute or two..."
    if "$CONDA" install -y -q -p "$BASICS_ENV" -c conda-forge --override-channels "${missing[@]}" >>"$INSTALL_LOG" 2>&1 \
            || "$CONDA" create -y -q -p "$BASICS_ENV" -c conda-forge --override-channels "${missing[@]}" >>"$INSTALL_LOG" 2>&1; then
        for tool in "${missing[@]}"; do
            if [ -x "$BASICS_ENV/bin/$tool" ]; then
                printf '#!/bin/sh\nexec "%s/bin/%s" "$@"\n' "$BASICS_ENV" "$tool" > "$TOOLS_DIR/bin/$tool"
                chmod +x "$TOOLS_DIR/bin/$tool"
            fi
        done
        hash -r
    fi
    for tool in "${missing[@]}"; do
        command -v "$tool" >/dev/null 2>&1 || die "Could not set up '$tool'." \
            "Re-run the installer - it retries just this step. (Or install it yourself: on Ubuntu/Debian, sudo apt install $tool.)"
    done
    log "  Ready: ${missing[*]} (in your home folder)."
fi
log "  git: $(git --version 2>/dev/null | head -1)"
lfs_probe="$(git lfs version 2>&1 | tail -1)"
if printf '%s' "$lfs_probe" | grep -q "git-lfs/"; then
    # From the home folder (see the "cd" at the top), then proven: the
    # setting must really be in the user's git settings.
    ( cd "$HOME" && git lfs install --skip-repo ) >>"$INSTALL_LOG" 2>&1 || true
    if [ -z "$(cd "$HOME" && git config --global --get filter.lfs.process 2>/dev/null)" ]; then
        die "Git-LFS is installed but could not be switched on (git lfs install)." \
            "Re-run the installer; if it fails again, send the install log."
    fi
    log "  Git-LFS ready."
else
    log "  (asking Git-LFS to identify itself said: $lfs_probe)"
    die "Could not set up Git-LFS (the line above explains why)." \
        "Re-run the installer - it retries just this step. If it fails again, send the install log."
fi
# The model files the background download could not start without curl.
[ "${BG_TRIED:-0}" = 1 ] || start_background_downloads

# --- 2c. Node.js (runs the web page). Prefers what you already have.
node_ok() {
    command -v node >/dev/null 2>&1 || return 1
    local major
    major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
    [ "${major:-0}" -ge 18 ] 2>/dev/null
}
if [ -x "$TOOLS_DIR/node/bin/node" ]; then
    export PATH="$TOOLS_DIR/node/bin:$PATH"; hash -r
fi
if node_ok; then
    NODE_BIN_DIR="$(dirname "$(command -v node)")"
    log "  Node.js: found ($(node --version)) - using it."
else
    log "  Node.js not found (or older than 18) - installing the official build into your home folder..."
    node_arch=$([ "$IS_X64" = 1 ] && echo "linux-x64" || echo "linux-arm64")
    curl -L --fail --progress-bar -o "$TOOLS_DIR/node.tar.gz" \
        "$NODE_DIST_BASE/node-$NODE_VER-$node_arch.tar.gz" \
        || die "Could not download Node.js." "Check your internet connection and re-run the installer."
    rm -rf "$TOOLS_DIR/node"
    mkdir -p "$TOOLS_DIR/node"
    tar -xzf "$TOOLS_DIR/node.tar.gz" -C "$TOOLS_DIR/node" --strip-components=1 \
        || die "Could not unpack Node.js." "Re-run the installer."
    rm -f "$TOOLS_DIR/node.tar.gz"
    export PATH="$TOOLS_DIR/node/bin:$PATH"
    hash -r
    node_ok || die "Node.js was installed but does not run on this PC." "Send the install log."
    NODE_BIN_DIR="$TOOLS_DIR/node/bin"
    log "  Node.js ready ($(node --version), in your home folder)."
fi
NPM="$NODE_BIN_DIR/npm"

# --- 2d. FFmpeg for videos. A PC that already has ffmpeg + ffprobe keeps its
# own; otherwise conda-forge's build in its own small toolbox, with small
# launchers in ~/.amadeus-tools/ffmpeg/bin, where the launcher and the app
# look for it (the same place as on Windows and Mac). Never fatal: without
# it only videos are missing (a re-run adds it).
FFMPEG_BIN="$TOOLS_DIR/ffmpeg/bin"
FFMPEG_ENV="$TOOLS_DIR/ffmpeg/conda"
ffmpeg_works() {
    "$1/ffmpeg" -hide_banner -version >/dev/null 2>&1 \
        && "$1/ffprobe" -hide_banner -version >/dev/null 2>&1
}
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
    log "  FFmpeg: present on this PC - videos use it."
elif ffmpeg_works "$FFMPEG_BIN"; then
    log "  FFmpeg for videos: already set up."
else
    log "  FFmpeg for videos: setting it up from conda-forge (a few minutes)..."
    ff_err="$INSTALL_DIR/ffmpeg-err.log"
    : > "$ff_err"
    mkdir -p "$FFMPEG_BIN"
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

# --- 2e. Speech model for videos: faster-whisper "small", pinned
# (backend/transcribe.py lists the same files - a test keeps them equal).
# Never fatal: the app fetches it itself when it is missing.
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
    if [ -f "$sp_file" ] && [ "$(sha256_of "$sp_file")" = "$sp_sha" ]; then
        continue
    fi
    curl -L --fail --retry 5 --retry-delay 10 -C - --progress-bar -o "$sp_file.part" \
        "https://huggingface.co/Systran/faster-whisper-small/resolve/$SPEECH_REV/$sp_name" || true
    if [ -f "$sp_file.part" ] && [ "$(sha256_of "$sp_file.part")" = "$sp_sha" ]; then
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
    # The user's own versions go back over whatever the update brought (not
    # 'stash pop', which merged a changed shipped personality.txt into theirs).
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
    # own (the same rule as the other installers).
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
chmod +x "$PROJ/start_linux.sh" "$PROJ/start_linux_no_model.sh" 2>/dev/null || true

# ----------------------------------------------------------------------------
#  STEP 4: the voice engine (GPT-SoVITS) + her voice patches
# ----------------------------------------------------------------------------
step 4 "Setting up the voice engine"

GPT="$PROJ/GPT-SoVITS"
clone_or_update "$GPT_REPO_URL" "$GPT" ""
# Pin the voice engine to the version Amadeus is tested with (the same pin
# as the other installers).
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
    cp -R "$VOICE_PATCH/." "$GPT/"
    log "  Voice patches applied."
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
# app environment (3.10), so it gets its own toolbox.
ensure_env "amadeus-cm" "3.13"

pip_in_env "amadeus" install --quiet --upgrade pip >/dev/null 2>&1 || true
# The same package list as the other installers (web search, English words
# in her voice, pictures, PDF and Word documents).
pip_in_env "amadeus" install Flask flask-cors requests tqdm langchain langchain-openai pydantic ddgs e2k pillow pypdf python-docx \
    || die "Could not install the app's packages." "See the install log for details: $INSTALL_LOG"

smoke="$(cd "$PROJ/backend" && run_in_env "amadeus" "import api; import ddgs; import e2k; import PIL; import pypdf; import docx; print('SMOKE_OK')" 2>&1 | tail -1)"
if [ "${smoke}" = "SMOKE_OK" ]; then
    log "  App toolbox ready (startup smoke test passed)."
else
    log "  WARNING: the startup smoke test did not pass. If Amadeus fails to start later, send the log."
    printf '%s%s%s\n' "$C_DIM" "$smoke" "$C_RESET" | tee_to
fi

# Her memory (the CharacterMemory sidecar: its venv + about 1.3 GB of
# packages), set up HERE by the launcher's own setup code. Not fatal.
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

ensure_env "$VOICE_ENV"
PIP_LOG="$INSTALL_DIR/voice_pip.log"
: > "$PIP_LOG"
pip_in_env "$VOICE_ENV" install --quiet --upgrade pip >/dev/null 2>&1 || true

# --- 6a. PyTorch FIRST, with the build that matches this PC (the Windows
#         rule): an NVIDIA card with a working driver, generation 7.5 or
#         newer (RTX 20 / GTX 16 series onwards) gets the graphics-card
#         build (CUDA 12.8); every other PC the processor build. (Linux's
#         plain "pip install torch" would fetch a 3 GB graphics-card build
#         even on a PC without one, so the build is always named.)
HAS_NVIDIA=0
if [ -z "$SIM_GPU" ] && [ "$CARD_VENDOR" = "nvidia" ]; then HAS_NVIDIA=1; fi
VOICE_BUILD="cpu"
if [ "$HAS_NVIDIA" = 1 ]; then
    if [ -z "$CARD_CAP" ] || awk -v c="$CARD_CAP" -v m="$VOICE_MIN_COMPUTE_CAP" 'BEGIN { exit !(c + 0 >= m + 0) }'; then
        VOICE_BUILD="cuda"
    fi
    log "  Graphics card: $CARD_NAME - $CARD_VRAM_GB GB, driver $CARD_DRIVER, generation ${CARD_CAP:-unknown}"
fi
torch_probe='
try:
    import torch
    print("T|" + torch.__version__ + "|" + str(torch.version.cuda or "cpu") + "|" + str(torch.cuda.is_available()))
except Exception:
    print("NONE")
'
torch_info="$(run_in_env "$VOICE_ENV" "$torch_probe" 2>/dev/null | tail -1)"
printf '%s\n' "  PyTorch check: $torch_info (NVIDIA card: $HAS_NVIDIA, voice build: $VOICE_BUILD)" >> "$INSTALL_LOG"
torch_ready=0
case "$torch_info" in
    T\|*)
        tp_tag="$(printf '%s' "$torch_info" | cut -d'|' -f3)"
        tp_can="$(printf '%s' "$torch_info" | cut -d'|' -f4)"
        if [ "$VOICE_BUILD" = "cuda" ] && [ "$tp_can" = "True" ]; then torch_ready=1; fi
        if [ "$VOICE_BUILD" = "cpu" ] && [ "$tp_tag" = "cpu" ]; then torch_ready=1; fi
        ;;
esac
install_torch() {   # $1 = cu128 / cpu
    pip_in_env "$VOICE_ENV" uninstall -y torch torchvision torchaudio >>"$PIP_LOG" 2>&1 || true
    pip_in_env "$VOICE_ENV" install torch torchvision torchaudio --index-url "https://download.pytorch.org/whl/$1" 2>&1 | tee -a "$PIP_LOG" >/dev/null
    return "${PIPESTATUS[0]}"
}
if [ $torch_ready = 1 ]; then
    log "  PyTorch $(printf '%s' "$torch_info" | cut -d'|' -f2) is already in place for this PC - skipping the download."
else
    if [ "$VOICE_BUILD" = "cuda" ]; then
        log "  NVIDIA card found - installing PyTorch's graphics-card build (CUDA 12.8, about 3 GB)..."
        torch_index="cu128"
    else
        if [ "$HAS_NVIDIA" = 1 ]; then
            log "  This graphics card is too old for the voice engine's graphics-card build (it needs an RTX 20 / GTX 16 series card or newer). Her voice will run on the processor - it works, just slower."
        elif [ "$CARD_VENDOR" = "amd" ] || [ "$CARD_VENDOR" = "intel" ]; then
            log "  Her voice runs on the processor on this PC (the voice engine can only use NVIDIA cards) - it works, just a little slower."
        else
            log "  No NVIDIA card found - installing PyTorch's processor build (her voice works, just a little slower)."
        fi
        torch_index="cpu"
    fi
    log "  (a large download - this is normal; keep the window open)"
    if ! install_torch "$torch_index"; then
        tail -15 "$PIP_LOG" | tee_to
        die "Could not install PyTorch." "See: $PIP_LOG"
    fi
    log "  PyTorch installed."
fi

# --- 6b. The rest of the voice engine's packages (from its list, already
#         carrying Amadeus' voice patches).
log "  Installing the voice engine's remaining packages..."
if ! pip_in_env "$VOICE_ENV" install -r "$GPT/extra-req.txt" --no-deps 2>&1 | tee -a "$PIP_LOG" >/dev/null; then
    log "  WARNING: 'extra-req' install had issues - continuing (see $PIP_LOG)."
fi
voice_reqs_ok=0
if pip_in_env "$VOICE_ENV" install -r "$GPT/requirements.txt" 2>&1 | tee -a "$PIP_LOG" >/dev/null; then
    voice_reqs_ok=1
else
    # A package that has to be built on this PC needs a C compiler, which
    # many desktops lack: conda-forge's compiler goes into the voice toolbox
    # (no sudo), then the list is tried again.
    log "  (a package needs a C compiler - adding conda-forge's to the voice toolbox and trying again...)"
    if "$CONDA" install -y -q -n "$VOICE_ENV" -c conda-forge --override-channels c-compiler cxx-compiler make >>"$PIP_LOG" 2>&1 \
            && pip_in_env "$VOICE_ENV" install -r "$GPT/requirements.txt" 2>&1 | tee -a "$PIP_LOG" >/dev/null; then
        voice_reqs_ok=1
    fi
fi
if [ $voice_reqs_ok = 0 ]; then
    # Last resort: without the optional Japanese text helper (her voice code
    # treats it as optional) - better than a voice engine that never starts.
    log "  (full package list failed - retrying without the optional Japanese text helper)"
    grep -v '^pyopenjtalk-prebuilt' "$GPT/requirements.txt" > "$GPT/requirements-core.tmp.txt"
    pip_in_env "$VOICE_ENV" install -r "$GPT/requirements-core.tmp.txt" 2>&1 | tee -a "$PIP_LOG" >/dev/null \
        || { tail -15 "$PIP_LOG" | tee_to; die "Could not install the voice engine's packages." "See: $PIP_LOG"; }
fi

# ----------------------------------------------------------------------------
#  STEP 7: check the voice engine can use this PC's graphics card
# ----------------------------------------------------------------------------
step 7 "Checking the voice engine's hardware support"

# A real calculation on the card, not just "is a card there?": a card too
# old for this PyTorch says "available" and then fails here.
gpu_probe='
import torch
if not torch.cuda.is_available():
    print("GPU_NO")
else:
    try:
        x = torch.ones(8, device="cuda")
        print("GPU_OK" if float((x * 2).sum()) == 16.0 else "GPU_FAIL|wrong result")
    except Exception as e:
        print("GPU_FAIL|" + (str(e).strip().splitlines() or [""])[0])
'
if [ "$VOICE_BUILD" = "cuda" ]; then
    gpu_check="$(run_in_env "$VOICE_ENV" "$gpu_probe" 2>&1 | tail -1)"
    printf '%s\n' "  PyTorch GPU check: $gpu_check" >> "$INSTALL_LOG"
    case "$gpu_check" in
        GPU_OK) log "  PyTorch can use your graphics card - her voice will run on it." ;;
        GPU_FAIL\|*kernel\ image*|GPU_FAIL\|*not\ compatible*|GPU_FAIL\|*compute\ capability*|GPU_FAIL\|*sm_[0-9]*)
            log "  Your graphics card could not run the voice engine's graphics-card build. Switching her voice to the processor (works, just slower)..."
            install_torch cpu || { tail -15 "$PIP_LOG" | tee_to; die "Could not install PyTorch (processor build)." "See: $PIP_LOG"; }
            log "  Her voice will run on the processor." ;;
        GPU_FAIL*) log "  NOTE: PyTorch sees your card but a test calculation on it failed (details in the install log). If her voice does not work, update your NVIDIA driver and re-run this installer once." ;;
        *) log "  NOTE: PyTorch is installed but could not see the card yet. Update your NVIDIA driver, then re-run this installer once." ;;
    esac
else
    torch_check="$(run_in_env "$VOICE_ENV" "$torch_probe" 2>/dev/null | tail -1)"
    case "$torch_check" in
        T\|*) log "  Voice hardware: processor mode (PyTorch $(printf '%s' "$torch_check" | cut -d'|' -f2))." ;;
        *) log "  WARNING: PyTorch could not be loaded in the voice toolbox - her voice will not work until this is fixed. Send the log." ;;
    esac
fi

# ----------------------------------------------------------------------------
#  STEP 8: her voice models + pre-downloads
# ----------------------------------------------------------------------------
step 8 "Downloading her voice models (a few GB the first time)"

GPT_PKG="$GPT/GPT_SoVITS"
PRETRAINED="$GPT_PKG/pretrained_models"
mkdir -p "$PRETRAINED"

hf_cache="$TMP/GPT-SoVITS-hf"
if [ -f "$hf_cache/$HF_MARKER" ] && [ "$(file_size "$hf_cache/$HF_MARKER")" -ge 1000000 ] 2>/dev/null; then
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
    if [ ! -f "$hf_cache/$HF_MARKER" ] || [ "$(file_size "$hf_cache/$HF_MARKER")" -lt 1000000 ] 2>/dev/null; then
        die "The voice models do not look complete." "Re-run the installer; if it fails again, send the log."
    fi
fi
# Mirror the downloaded models into the voice engine's expected folder.
if command -v rsync >/dev/null 2>&1; then
    rsync -a --exclude .git "$hf_cache/" "$PRETRAINED/" >/dev/null 2>&1 || cp -R "$hf_cache/." "$PRETRAINED/"
else
    ( cd "$hf_cache" && tar --exclude=.git -cf - . ) | ( cd "$PRETRAINED" && tar -xf - ) \
        || cp -R "$hf_cache/." "$PRETRAINED/"
fi
log "  Voice models are in place."

# Pre-download the ~130 MB language-identification model.
LID_FILE="$PRETRAINED/$LID_DEST_REL/lid.176.bin"
LID_BYTES=131266198   # the published size (the other installers check the same file)
mkdir -p "$PRETRAINED/$LID_DEST_REL"
if [ -f "$LID_FILE" ] && [ "$(file_size "$LID_FILE")" != "$LID_BYTES" ]; then
    rm -f "$LID_FILE"
fi
if [ -f "$LID_FILE" ]; then
    log "  Voice language model already present."
else
    log "  Pre-downloading the voice language model (~130 MB)..."
    if curl -L --fail --retry 3 --retry-delay 5 -s -S -o "$LID_FILE.part" "$LID_URL" 2>>"$INSTALL_LOG" \
        && [ "$(file_size "$LID_FILE.part")" = "$LID_BYTES" ]; then
        mv -f "$LID_FILE.part" "$LID_FILE"
        log "  Voice language model pre-downloaded."
    else
        rm -f "$LID_FILE.part"
        log "  WARNING: could not pre-download the voice language model right now."
        log "  (If she is offline at first start, her voice will retry the download automatically.)"
    fi
fi

# Pre-fetch the Western-script text data her voice engine needs.
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
# shellcheck disable=SC2086
nltk_out="$(run_in_env "$VOICE_ENV" "$nltk_code" "$NLTK_DIR" $NLTK_RESOURCES 2>/dev/null | tail -1)"
case "$nltk_out" in
    NLTK_DATA_OK)      log "  Voice text data for Western-script words is in place." ;;
    NLTK_DATA_PARTIAL) log "  (some Western-script text data could not be fetched right now - she will retry at first use.)" ;;
    *)                 log "  (the Western-script text data check was skipped - if a line with a Western word is later unvoiced, it self-heals at next start.)" ;;
esac

# ----------------------------------------------------------------------------
#  STEP 9: her built-in local model (option A) - or, for option B on a PC
#  that could run it, llama.cpp alone (to run a model of her own later)
# ----------------------------------------------------------------------------
LLM_READY=0
if [ "$CHOICE_MODE" = "builtin" ]; then
    step 9 "Setting up her built-in local model - $(model_field "$CHOICE_MODEL" 2) (big download, be patient)"
else
    step 9 "Setting up the local-model helper (llama.cpp)"
fi
if install_local_llm "$PROJ"; then LLM_READY=1; fi

# ----------------------------------------------------------------------------
#  STEP 10: the web page (Node.js packages) + the app menu
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
if write_menu_entries "$PROJ" "$([ "$CHOICE_MODE" = "builtin" ] && echo 1 || echo 0)"; then
    log "  Added Amadeus to your app menu."
else
    log "  (Could not add Amadeus to the app menu - start her with start_linux.sh instead.)"
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
    log "  To START Amadeus: choose \"Amadeus\" in your app menu, or run:"
    log "       $PROJ/start_linux.sh"
    log "  (You can switch to your own model or a cloud service any time in Settings -> Connection.)"
    log "  When another program needs the graphics card, start \"Amadeus (without her model)\""
    log "  instead (start_linux_no_model.sh; load her model later in Settings -> Connection)."
    log ""
    log "(Install log saved to: $INSTALL_LOG)"
    printf '%s\n' "" | tee_to
    read -r -p "Press Enter to close this window" _ || true
    exit 0
fi
log "  Here is what to do next:"
log "  1) START Amadeus: choose \"Amadeus\" in your app menu, or run:"
log "       $PROJ/start_linux.sh"
log "  2) When she opens in your browser, read her introduction message -"
log "     it walks you through connecting your model, step by step."
log "  3) (Shortcut) You can also open Settings -> Connection and click"
log "     'Test connection' - the dot turns green when she can reach"
log "     your model. Pick your model from the chips (or type its name)"
log "     and paste its API key, if your provider has one."
if [ "$LLM_READY" = 1 ]; then
    log "  To run a GGUF model on this PC instead: put the file in"
    log "       $MODELS_DIR"
    log "     then choose Built-in in Settings -> Connection and press Refresh list."
fi
log ""
log "(Install log saved to: $INSTALL_LOG)"

printf '%s\n' "" | tee_to
read -r -p "Press Enter to close this window" _ || true
