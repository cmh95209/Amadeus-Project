#!/usr/bin/env bash
# Starts Amadeus WITHOUT loading her built-in model - for when another
# program needs the graphics card. Everything else starts as usual; load her
# model later in Settings -> Connection.
exec "$(cd "$(dirname "$0")" && pwd)/start_linux.sh" --no-model "$@"
