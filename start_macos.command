#!/bin/zsh

set -u

PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

# Finder-launched .command files may have a minimal PATH.
# (.amadeus-tools/node/bin: where the macOS installer may put Node.js.)
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.amadeus-tools/node/bin:$PATH"

find_conda() {
    if command -v conda >/dev/null 2>&1; then
        command -v conda
        return 0
    fi

    local candidates=(
        "$HOME/anaconda3/bin/conda"
        "$HOME/miniconda3/bin/conda"
        "$HOME/miniforge3/bin/conda"
        "$HOME/opt/anaconda3/bin/conda"
        "/opt/anaconda3/bin/conda"
        "/opt/anaconda3/condabin/conda"
        "/opt/homebrew/Caskroom/miniconda/base/bin/conda"
    )

    for candidate in "${candidates[@]}"; do
        if [[ -x "$candidate" ]]; then
            echo "$candidate"
            return 0
        fi
    done

    return 1
}

CONDA_EXE="$(find_conda)" || {
    echo "ERROR: Conda could not be found."
    echo "Install Anaconda/Miniconda, then try again."
    echo
    read "?Press Enter to close..."
    exit 1
}

"$CONDA_EXE" run \
    -n amadeus \
    --no-capture-output \
    python "$PROJECT_ROOT/scripts/launcher.py"

EXIT_CODE=$?

if [[ $EXIT_CODE -ne 0 ]]; then
    echo
    echo "Amadeus launcher exited with an error."
    echo "Check .runtime/logs for service logs."
    read "?Press Enter to close..."
fi

exit $EXIT_CODE
