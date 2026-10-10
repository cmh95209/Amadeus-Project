# Using Amadeus

Configuration, launching, manual startup, the Live2D WebUI, updates, and where her data lives.

## Configuration

### Model Server, API Key, and Active Model

These are set in the app's **Settings → Connection** panel (the first-meeting
flow), and are stored locally as plain text:

- **Model server address** — `backend/llm_server.txt`. Leave empty to
  auto-detect local ports (8888, 8000), or set a full URL such as
  `http://127.0.0.1:8080/v1` (local) or an OpenRouter URL.
- **API key** — `backend/data/api_key.txt`. Needed for cloud endpoints such
  as OpenRouter; local servers that don't check keys can use any value or
  none.
- **Active model** — `backend/data/llm_model.txt`. The exact model name the
  server serves (the "Test connection" button shows the real list; avoid
  variants like `:batch` unless you know why you want them).

The memory sidecar follows these same settings automatically — there is no
separate memory configuration.

You can change all of these at any time without reinstalling anything.

### Web Access, Deep Thinking, and Voice Retention

- **Web access** — a toggleable local web search (DuckDuckGo, no API key)
  lets her verify recent events; it reads the top result's page for real
  content, with an optional "deep thinking" mode that enables model reasoning
  for the search decision only. Disabled by default on fresh installs.
- **Live weather & air quality** — keyless, from public feeds.
- **Voice retention** — keep the last N generated voice recordings, freeing
  disk space automatically.

---

## Launching Amadeus

### Launcher

The project ships a cross-platform launcher that starts the whole stack in
the right order and waits for each service:

1. Starts the GPT-SoVITS voice engine.
2. Waits for the voice engine to become available.
3. Installs the frontend (npm) if `node_modules/` is missing.
4. Starts the Flask backend.
5. Waits for the backend to become available.
6. Starts the CharacterMemory sidecar (first launch: builds its venv and
   installs its packages — this takes a few minutes; afterwards it starts in
   seconds). If the sidecar cannot be installed or started, the launcher
   warns and continues — the app runs without long-term memory.
7. Waits for the sidecar to become available (best effort).
8. Starts the React/Vite frontend.
9. Waits for the WebUI to become available.
10. Opens Amadeus automatically in the default browser.
11. Shuts down launcher-owned processes when the launcher exits.

Runtime logs are written locally to:

```text
.runtime/logs/
```

(voice: `gptsovits.log`, backend: `backend.log`, sidecar: `cm-sidecar.log`,
frontend: `frontend.log`)

The `.runtime/` directory is ignored by Git.

### macOS

If you used the installer, simply double-click `start_macos.command` in your
Amadeus folder (or run `./start_macos.command` in Terminal).

If you installed manually, make the launcher executable first:

```bash
chmod +x start_macos.command
```

Then run:

```bash
./start_macos.command
```

or double-click `start_macos.command` in Finder.

The launcher opens Amadeus automatically once all services are ready.

Press `Ctrl+C` in the launcher terminal to shut down the complete stack.

### Windows

Double-click:

```text
start_windows.bat
```

or run it from Command Prompt:

```bat
start_windows.bat
```

The Windows launcher uses the same underlying startup sequence as the macOS
launcher.

A health check that does **not** need a model or a running app is included:
double-click `check_amadeus.bat` and it runs the backend self-tests with a
built-in stand-in, reporting a plain-English pass/fail.

---

## Manual Startup

Manual startup is mainly useful for development and debugging. Start the
services in this order:

### GPT-SoVITS

```bash
conda activate GPTSoVits
cd backend
python start_gptsovits.py
```

GPT-SoVITS normally listens on:

```text
http://127.0.0.1:9880
```

### Backend

```bash
conda activate amadeus
cd backend
python main.py
```

The backend listens on:

```text
http://127.0.0.1:5050
```

### Memory sidecar (optional)

```bash
# Windows
memory_sidecar\venv\Scripts\python.exe memory_sidecar\cm_sidecar.py
# macOS
memory_sidecar/venv/bin/python memory_sidecar/cm_sidecar.py
```

The sidecar listens on:

```text
http://127.0.0.1:9870
```

If `memory_sidecar/venv` does not exist yet, run the launcher once first (it
builds the venv), or skip the sidecar — the app runs fine without it.

### Frontend

```bash
cd frontend
npm run dev
```

The frontend normally listens on:

```text
http://127.0.0.1:5173
```

---

## Live2D WebUI

Amadeus renders its character directly in the browser using the official
Live2D Cubism SDK for Web.

The current rendering path is:

```text
Live2DCharacter.tsx
        │
        ▼
KurisuController.ts
        │
        ▼
KurisuModel.ts
        │
        ├── model3.json
        ├── moc3
        ├── textures
        └── WebGL shaders
        │
        ▼
Live2D Cubism Framework + Core
        │
        ▼
WebGL canvas
```

Runtime model assets are stored under:

```text
frontend/public/live2d/
```

WebGL shader files are served from:

```text
frontend/public/cubism-shaders/WebGL/
```

The Cubism Core runtime is loaded from:

```text
frontend/public/live2dcubismcore.min.js
```

The project previously experimented with a Pixi-based Live2D integration.
That approach was removed in favor of direct use of the current official
Cubism Web SDK.

### Character Motion and Lip Sync

The browser-side character system separates body motion from mouth motion.

- `MotionPlayer.ts` manages looping `Idle` and `Talk` states plus
  higher-priority one-shot reactions.
- `SpeechPlayer.ts` plays streamed audio in the browser and measures the
  actual waveform with the Web Audio API.
- The measured amplitude is smoothed and applied to `ParamMouthOpenY`.
- Touch reactions can interrupt the talking-body loop without stopping lip
  sync.
- When a reaction finishes, the character returns to `Talk` if audio is
  still playing, otherwise `Idle`.

The current motion set includes a longer natural idle, a subtle talking-body
loop, a head-pat reaction, and special touch reactions.

Prerecorded interaction lines are stored under:

```text
backend/assets/reaction_audio/
```

and are served through Flask to the same browser audio/lip-sync path used by
generated speech.

---

## Updating Amadeus

Pull the latest project changes:

```bash
git pull origin main
```

The simplest way to apply everything (environments, voice engine patches,
models, frontend) is to re-run the installer one-liner for your platform — it
is resumable and skips what is already in place. If you installed manually:

### Update Backend Environment

```bash
conda activate amadeus
cd backend
pip install -r requirements.in
cd ..
```

### Update Frontend

```bash
cd frontend
npm install
cd ..
```

### Update GPT-SoVITS

Only update GPT-SoVITS when Amadeus is known to support the newer version:

```bash
cd GPT-SoVITS
git pull origin main
pip install -r requirements.txt
cd ..
```

(Re-apply the voice patches from step 2 if the update touches the patched
files.)

The memory sidecar's venv keeps its pinned packages; after a project update
that changes the sidecar's requirements, delete `memory_sidecar/venv` and
let the launcher rebuild it.

---

## Runtime Data and Secrets

The following are local runtime data and should not be committed:

```text
backend/data/api_key.txt
backend/data/llm_model.txt
backend/data/memory.db
backend/data/character_memory/    # her long-term memory (v2.0)
backend/llm_server.txt
backend/generated/
.runtime/
memory_sidecar/venv/
frontend/node_modules/
GPT-SoVITS/
```

Conversation history is stored locally in:

```text
backend/data/memory.db
```

Deleting this database removes the locally stored conversation history.

Her long-term memory lives in:

```text
backend/data/character_memory/
```

Deleting that folder resets her memory to a clean slate (her lore is
re-seeded automatically on the next start). Back it up if you want to keep
what she has learned about you.
