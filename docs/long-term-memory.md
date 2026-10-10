# Long-Term Memory (v2.0)

Since v2.0, Amadeus has a long-term memory of her own. A separate local
service — the **CharacterMemory sidecar** (127.0.0.1:9870) — quietly
processes your conversations in the background:

- it learns **facts** about you, **episodes** of what happened, and a
  running **summary** of who you are and what you care about;
- before each reply it renders the relevant memories into her prompt, so she
  can reference things you told her days or weeks ago;
- her memory is **user data**, stored locally in
  `backend/data/character_memory/` — nothing is ever sent anywhere except the
  turns mirrored to her LLM, which go to whatever model server you configured.

The sidecar runs in its own process with its own Python environment, so the
memory engine (and its license) stays cleanly separated from the rest of the
app: if it is missing or fails, the app runs exactly as it did before v2.0
(without long-term memory) and the launcher only warns.

The memory engine is built on **CharacterMemory** by
[Francesco Caracciolo](https://github.com/FrancescoCaracciolo/CharacterMemory)
— an open-source memory system for LLM characters (GPL-3.0), which Amadeus
runs as a separate helper process. See `memory_sidecar/LICENSE-NOTE.md` for
the full license notes.

The launcher starts the sidecar automatically (its one-time setup takes a few
minutes on first launch). It follows the same LLM settings as the app, so
there is nothing extra to configure.
