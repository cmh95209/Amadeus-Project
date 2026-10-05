# memory_sidecar - long-term memory for Amadeus

A separate local service (127.0.0.1:9870) that lets Amadeus remember
you across conversations: it learns facts, episodes and a running
summary from mirrored turns and renders a memory block into the
app's prompt.

- Started automatically by the launcher; the first run installs its
  own venv (requirements.txt, a few minutes of downloads).
- If the sidecar is absent or fails, the app runs exactly as before
  (no memory) - the launcher only warns.
- Her learned memory is USER DATA in backend/data/character_memory/
  (git-ignored). Deleting that folder resets her memory; the lore
  index in seed_index/ is re-seeded automatically on next start.
- The engine (lib/) is GPL-3.0-or-later and isolated in its own
  process - see LICENSE-NOTE.md.
