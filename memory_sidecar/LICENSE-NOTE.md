# Licensing note - memory sidecar

The long-term memory engine in this folder vendors the `character_memory`
library (charactermemory 0.1.2, upstream master commit 9581ad1) from
https://github.com/FrancescoCaracciolo/CharacterMemory . It is licensed
GPL-3.0-or-later (declared in the upstream pyproject.toml; the upstream
repo ships no separate LICENSE file). Amadeus itself is MIT.

The two do not mix code: this library runs ONLY inside the sidecar
process (memory_sidecar/venv), which talks to the app exclusively over
localhost HTTP (127.0.0.1:9870). That separate-process boundary is the
license isolation between the GPL engine and the MIT app.

When redistributing, keep this note and the upstream link available
(GPL-3.0 requires the license terms and source availability of the
covered code - the full vendored source is right here in lib/).
