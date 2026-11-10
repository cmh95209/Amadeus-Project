#!/usr/bin/env bash
# Starts Amadeus on Linux (the app menu's "Amadeus" entry runs this file in
# a terminal window). Pass --no-model to start without her built-in model.

set -u

# The Amadeus help channel (one constant per script).
DISCORD_URL="https://discord.gg/YxYkZPSVf3"

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

# An app-menu start may have a minimal PATH. ~/.amadeus-tools holds what the
# Linux installer set up in the home folder: Node.js, and (bin) any git /
# curl / Git-LFS this PC lacked.
export PATH="$HOME/.amadeus-tools/node/bin:$HOME/.amadeus-tools/bin:$HOME/.local/bin:$PATH"

find_conda() {
    if command -v conda >/dev/null 2>&1; then
        command -v conda
        return 0
    fi

    local candidates=(
        "$HOME/miniforge3/bin/conda"
        "$HOME/miniconda3/bin/conda"
        "$HOME/anaconda3/bin/conda"
        "$HOME/mambaforge/bin/conda"
        "/opt/conda/bin/conda"
        "/opt/miniforge3/bin/conda"
        "/opt/miniconda3/bin/conda"
        "/opt/anaconda3/bin/conda"
    )

    for candidate in "${candidates[@]}"; do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done

    return 1
}

CONDA_EXE="$(find_conda)" || {
    echo "ERROR: Conda could not be found."
    echo "Run the Amadeus installer (install_linux.sh) again, then try again."
    echo
    read -r -p "Press Enter to close..." _
    exit 1
}

"$CONDA_EXE" run \
    -n amadeus \
    --no-capture-output \
    python "$PROJECT_ROOT/scripts/launcher.py" "$@"

EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo
    echo "Amadeus launcher exited with an error."
    echo "The details are in the logs folder: $PROJECT_ROOT/logs"
    echo "Need help? Ask on our Discord: $DISCORD_URL - and send the logs folder $PROJECT_ROOT/logs."
    read -r -p "Press Enter to close..." _
fi

exit $EXIT_CODE
