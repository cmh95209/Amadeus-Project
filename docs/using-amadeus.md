# Using Amadeus

Configuration, launching, manual startup, the Live2D WebUI, updates, and where her data lives.

## Configuration

### Provider, Key, and Model (v3.1)

**Settings → Connection → Custom Model**:

1. **Provider** — pick from the list: a local server (auto-detect, Ollama,
   LM Studio), a cloud provider (OpenRouter, Google Gemini, OpenAI, DeepSeek,
   Groq, Mistral, xAI, Together AI, Cerebras, NVIDIA, Hugging Face, Moonshot AI,
   NanoGPT), **Google Vertex AI** (paste or choose your service-account JSON
   key and pick an endpoint), or **Custom address** for any OpenAI-compatible
   server.
2. **Key** — paste the provider's API key and press **Save**. Each provider
   keeps its own key, so switching back and forth does not lose them. Local
   servers need no key unless yours asks for one.
3. **Model** — pick one from the list that appears, then press **Test**: she
   sends one real message through it and the status line says whether it
   works with Amadeus (or why not - wrong key, unknown model, no credit...).

Everything is stored locally as plain text (`backend/llm_server.txt`,
`backend/data/llm_model.txt`, the keys in `backend/data/`).

The memory sidecar follows these same settings automatically — there is no
separate memory configuration.

You can change all of these at any time without reinstalling anything.

### Her Built-in Model (Windows and Mac)

When the installer set up her own model (option A), she needs no server
address or key: **Settings → Connection** shows it as **Built-in**, and can
unload, load or switch it (for example when a game needs the graphics
memory - or start her with `Start Amadeus without her model.bat` on Windows,
`start_macos_no_model.command` on a Mac). You can switch to a cloud service
or your own server there at any time.

On a Mac (Apple chip, M1 or newer, 16 GB of memory or more) her model runs on
the chip's graphics (Metal) and shares the Mac's memory with everything else:
close memory-hungry programs for the best speed. Macs with 16-23 GB get the
3-bit Gemma 4 12B, Macs with 24 GB or more the 12B QAT with a longer
conversation memory. Intel Macs and Macs with less memory use option B.

### Memory Across Chat Tabs (v3.1)

**Settings → Preferences** has two switches, both on by default:

- **Remember my other chats** — she keeps up with your other chat tabs: what
  each was about and how it ended, newest first.
- **Search old chats** — each time you write, she looks through all your other
  chats for the same topic and remembers the best matches. It works in any
  language and costs no extra model calls.

With both off she only knows the open tab, plus her long-term memory and what
she knows about you. More in [Long-term memory](long-term-memory.md).

### Reasoning

**Settings → Connection → Reasoning** sets how much she thinks before she
answers (off, low, medium, high). It replaced the old "Deep thinking" switch.

### Remote Access (Phone, Tablet, Other PCs)

**Settings → Mobile and remote access** opens her on your own devices
through Tailscale - step by step in [Remote access](remote-access.md).

### Web Access and Voice Retention

- **Web access** — a toggleable web search (Firecrawl's free keyless search,
  with DuckDuckGo-style `ddgs` as an automatic backup; no API key, no account)
  lets her verify recent events; results carry their dates so she does not
  mistake old news for new, and she reads the top result's page for real
  content (she also reads links you send her). A **sources** chip under her
  reply opens the
  pages she used. Disabled by default on fresh installs.
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

The launcher window shows a few status lines; every detail is in the
`logs` folder inside the project folder (a normal, visible folder - send it
when asking for help):

```text
logs/
```

(launcher: `launcher.log`, voice: `gptsovits.log`, backend: `backend.log`,
sidecar: `cm-sidecar.log` and its one-time setup `memory-setup.log`,
frontend: `frontend.log`, built-in model: `llm.log`)

The `logs/` and `.runtime/` folders are ignored by Git.

**Her built-in model and your graphics memory.** With the built-in model
(installer option A), the launcher first checks that the graphics card has
room for it (NVIDIA cards). If a game or another program is using the memory,
it asks: close that program and press Enter, start anyway, or start without
her model. `Start Amadeus without her model.bat` (macOS:
`start_macos_no_model.command`) always starts without it. In Settings ->
Connection -> Built-in you can unload her model (freeing its graphics memory),
load it again, or pick another GGUF file: put your own model in the `models`
folder next to the project folder and press "Refresh list".

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

**Windows:** double-click `Update Amadeus.bat` in the `Amadeus-Project` folder
(installs from before v3.1.1 do not have it yet: re-run `Install Amadeus.bat`
once). **Mac and Linux:** run the installer line from the README again. It updates the project, adds new packages and models,
skips what is already in place, and keeps your own files: your chats and her
memory, your saved connection, and her personality if you edited it (Settings
then offers the new default personality instead of overwriting yours).

If you installed manually, pull the latest project changes:

```bash
git pull origin main
```

If git refuses because your own `personality.txt`, `llm_server.txt` or
`llm_model.txt` would be overwritten, re-run the installer instead - it
updates around them. Then:

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
