# Amadeus

Amadeus is a Steins;Gate-inspired AI character assistant designed to feel less like a conventional chatbot and more like a persistent virtual companion.

The project combines configurable large language models, persistent conversation history, customizable character behavior, bilingual dialogue generation, neural voice synthesis, and Live2D character rendering in a single interactive system.

Amadeus began as a small personal experiment inspired by *Steins;Gate*. It has since grown into a larger software project and a sandbox for experimenting with conversational AI, memory, speech synthesis, animated character interfaces, and long-running assistant behavior.

The project is still actively evolving. It is not intended to be a finished product; it is an ongoing attempt to explore what happens when an AI character is given personality, voice, visual presence, and continuity over time.

![Amadeus Preview](docs/images/mainmenu.png)

![Amadeus Preview](docs/images/settings.png)

## How Conversation and Voice Work

Amadeus currently separates the **text you read** from the **voice you hear**.

You can chat with Kurisu in **English** (or any language she can understand).

For each normal response, Amadeus asks the LLM to write her reply **natively in Japanese first**, then an English version of it for the UI — in a single model call:

- **Japanese (`assistant_reply_JPS`)** — natural spoken Japanese, written the way Kurisu would actually talk; sent to GPT-SoVITS for voice synthesis. This is also what is stored in her memory, so her own history stays in her own voice.
- **English (`assistant_reply_ENG`)** — a translation of that Japanese line, displayed in the conversation UI.

So a typical conversation looks like:

```text
You type a message
        │
        ▼
      LLM
        │
        ├── Japanese dialogue (native) ─► GPT-SoVITS ─► Kurisu speaks Japanese
        │
        └── English translation ─────► displayed in the WebUI
```

> [!IMPORTANT]
> ### Does Amadeus run a local LLM?
>
> **Yes — or it can.** The conversational LLM can run **on your own GPU**
> (Unsloth Desktop, Ollama, LM Studio, llama.cpp, vLLM, or any
> OpenAI-compatible server) **or** remotely through **OpenRouter** with your
> own API key.
>
> Point Amadeus at your model server in Settings → Connection ("Model server
> address"). Leave the field empty and Amadeus auto-detects the usual local
> ports (8888, 8000) — which is how it finds an Unsloth Desktop server that
> picks a new port every time it restarts.
>
> The connection status dot and the "Test connection" button ask the server
> for its real model list, so a mistyped model name shows an amber warning
> instead of failing mid-conversation, and the server's models appear as
> clickable chips.
>
> What runs locally:
>
> - **The conversational LLM** (when pointed at a local server) — on your GPU
> - **GPT-SoVITS** — Japanese voice synthesis
> - **Live2D / Cubism** — character rendering and animation
> - **Conversation history** — stored locally in SQLite
> - **Amadeus frontend and backend**
>
> What can run remotely:
>
> - **LLM inference** — through OpenRouter (or any OpenAI-compatible endpoint)
>
> Amadeus does **not bundle or automatically install a local LLM** — run the
> model with a server of your choice and point Amadeus at it.

## Current Status

The current development version includes:

- React + TypeScript browser interface
- Python / Flask backend
- Configurable LLM access through OpenRouter
- Persistent SQLite conversation history
- Runtime LLM model switching
- GPT-SoVITS character voice synthesis
- Live2D Cubism rendering directly in the WebUI
- Natural looping idle and talking-body motions
- Touch interactions with one-shot character reactions
- Browser-side streamed speech playback
- Audio-amplitude-driven Live2D lip synchronization
- Paired text + prerecorded audio variants for special interactions
- Cross-platform automatic launcher for macOS and Windows
- Local model server support (Unsloth Desktop, Ollama, LM Studio, llama.cpp, vLLM) alongside OpenRouter
- Live connection status with the server's model list and a "Test connection" button
- Native-Japanese-first dialogue with an English translation shown in the UI
- Trust-based relationship stats and trust-aware voice lines
- Multiple named chat sessions (create, rename, delete, switch)
- Reply regeneration with saved versions, plus edit / delete / undo
- Per-message voice replay and on-demand re-voice
- Voice retention cap (keep the last N recordings)
- Optional local web search (now also reads the top result's page for real content, not just one-line snippets), with optional deep thinking for the search decision
- Backend connection/status display
- Conversation memory reset controls

The Live2D character system now handles idle, talking, touch reactions, motion priority, and audio-driven mouth movement in the browser. Generated GPT-SoVITS speech is streamed through Flask to the WebUI, where the same audio signal sent to the speakers is analyzed to drive `ParamMouthOpenY`.

Next major character-system work includes:

- More expression and motion control from model responses
- Richer hit-area and interaction behavior
- Expanding the prerecorded interaction voice library
- Improved prompting and character-state control
- A future redesign of the long-term memory system

---

# Architecture

Amadeus is split into three main runtime components:

```text
┌─────────────────────────────────────────────┐
│ React / TypeScript WebUI                    │
│                                             │
│  Chat UI          Live2D Cubism / WebGL    │
└───────────────────────┬─────────────────────┘
                        │ HTTP
                        ▼
┌─────────────────────────────────────────────┐
│ Python / Flask Backend                      │
│                                             │
│  Chat   Memory   LLM   TTS API             │
└───────────────┬─────────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────────┐
│ GPT-SoVITS                                  │
│ Character voice synthesis                   │
└─────────────────────────────────────────────┘
```

Default local services:

```text
GPT-SoVITS        http://127.0.0.1:9880
Amadeus backend   http://127.0.0.1:5050
Amadeus WebUI     http://127.0.0.1:5173
```

The AI backend is intentionally independent from the frontend. This allowed the original Unity interface to be replaced by a browser-based React/WebGL frontend without rewriting the conversational core.

---

# Project Structure

```text
Amadeus-Project/
├── backend/
│   ├── main.py
│   ├── api.py
│   ├── chat.py
│   ├── memory.py
│   ├── llm.py
│   ├── tts.py
│   ├── start_gptsovits.py
│   ├── environment.yml
│   ├── requirements.in
│   ├── assets/
│   └── data/
│
├── frontend/
│   ├── cubism/
│   │   ├── Core/
│   │   └── Framework/
│   ├── public/
│   │   ├── cubism-shaders/
│   │   ├── live2d/
│   │   └── live2dcubismcore.min.js
│   ├── src/
│   │   ├── components/
│   │   │   └── Live2DCharacter.tsx
│   │   ├── live2d/
│   │   │   ├── cubismBootstrap.ts
│   │   │   ├── KurisuController.ts
│   │   │   └── KurisuModel.ts
│   │   ├── App.tsx
│   │   ├── api.ts
│   │   ├── main.tsx
│   │   └── styles.css
│   ├── index.html
│   ├── package.json
│   └── vite.config.ts
│
├── scripts/
│   └── launcher.py
│
├── start_macos.command
├── start_windows.bat
├── README.md
└── .gitignore
```

GPT-SoVITS is cloned separately into a local `GPT-SoVITS/` directory. It is an external dependency rather than part of the Amadeus repository itself.

---

# Technology

## Backend

- Python
- Flask
- SQLite
- OpenRouter
- GPT-SoVITS

## Frontend

- React 19
- TypeScript
- Vite
- WebGL
- Live2D Cubism SDK for Web

The Live2D runtime does not require users to install Cubism Editor or Unity. The required Web runtime files and shaders are included with the project frontend.

---

# Installation

## One-click install (Windows)

Prefer to let a single script do all of this for you? Download
`install_windows.ps1` from the [latest release](https://github.com/cmh95209/Amadeus-Project/releases)
(it is also in `scripts/install_windows.ps1` in this repository), save it
somewhere you remember - e.g. your Downloads folder - then:

1. Press the Windows key, type **PowerShell**, right-click it and choose
   **Run as administrator**.
2. Paste this one line and press Enter (adjust the path if you saved the
   script somewhere other than Downloads):

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass; & "$HOME\Downloads\install_windows.ps1"
   ```

3. Press Enter when asked, then wait. The script installs everything,
   connects Amadeus to your local model server, writes a log to
   `Amadeus\install_log.txt`, and is safe to re-run if it ever stops
   partway - it resumes where it left off.

The manual steps below are what the script does for you, in case you prefer
to do it yourself or run into something the script cannot handle.

## 0. Requirements

Before installing Amadeus, make sure you have:

- Git
- Conda / Anaconda / Miniconda
- Node.js + npm
- Git LFS
- FFmpeg
- Python 3.10 for GPT-SoVITS
- Visual Studio Build Tools on Windows if required by GPT-SoVITS dependencies

An NVIDIA GPU is strongly recommended for faster local voice synthesis, although CPU operation is possible.

### Conda

Install Anaconda or Miniconda and make sure the `conda` command is available.

### Node.js

Install Node.js and npm. The browser WebUI uses Vite and React.

### Windows

Install Visual Studio Build Tools if required by GPT-SoVITS or one of its Python dependencies.

---

## 1. Clone Amadeus

```bash
git clone https://github.com/reflectors02/Amadeus-Project.git
cd Amadeus-Project
```

Clone GPT-SoVITS into the project directory:

```bash
git clone https://github.com/RVC-Boss/GPT-SoVITS.git
```

Your local directory will then contain both:

```text
Amadeus-Project/
├── backend/
├── frontend/
├── scripts/
└── GPT-SoVITS/      # external project, cloned locally
```

---

## 2. Create the Amadeus Backend Environment

From the repository root:

```bash
cd backend
conda env create -f environment.yml
```

Activate it:

```bash
conda activate amadeus
```

Return to the project root:

```bash
cd ..
```

Backend dependency information is tracked in:

```text
backend/environment.yml
backend/requirements.in
```

---

## 3. Create the GPT-SoVITS Environment

```bash
cd GPT-SoVITS
conda create -n GPTSoVits python=3.10
conda activate GPTSoVits
```

Install GPT-SoVITS dependencies:

```bash
pip install -r extra-req.txt --no-deps
pip install -r requirements.txt
conda install ffmpeg
```
### Initialize fast-langdetect

GPT-SoVITS uses `fast-langdetect` for language detection. If you encounter
a missing model cache directory error, create the directory below.

Run this while still inside the `GPT-SoVITS` directory.

#### Windows

```bat
mkdir GPT_SoVITS\pretrained_models\fast_langdetect
```

#### macOS / Linux

```bash
mkdir -p GPT_SoVITS/pretrained_models/fast_langdetect
```

Return to the Amadeus project root:

```bash
cd ..
```

---

## 4. Configure PyTorch

The exact PyTorch installation depends on the machine running GPT-SoVITS.

### NVIDIA GPU

Install the CUDA-enabled PyTorch build appropriate for your system.

Example:

```bash
conda activate GPTSoVits
pip uninstall -y torch torchvision torchaudio
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```

Check the current PyTorch installation instructions if your CUDA environment requires a different build.

### CPU Only

A CPU-only configuration is also possible, although synthesis will be slower.

Example:

```bash
conda activate GPTSoVits
pip uninstall -y torch torchvision torchaudio torchcodec
pip install torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 --index-url https://download.pytorch.org/whl/cpu
```

---

## 5. Download GPT-SoVITS Pretrained Models

Install Git LFS if necessary:

```bash
git lfs install
```

Clone the pretrained model repository somewhere temporary:

```bash
git clone https://huggingface.co/lj1995/GPT-SoVITS
```

Copy the required pretrained files into:

```text
GPT-SoVITS/GPT_SoVITS/pretrained_models/
```

The exact files required can vary with GPT-SoVITS versions, so Amadeus should only be paired with a GPT-SoVITS version known to work with the project.

---

## 6. Install Frontend Dependencies

The automatic launcher runs `npm install` if `frontend/node_modules/` is missing.

You can also install the dependencies manually:

```bash
cd frontend
npm install
cd ..
```

No separate Live2D or Cubism installation is required for the WebUI. The Cubism Web runtime, framework source, shaders, and model assets used by Amadeus are part of the project frontend.

---

# Configuration

## OpenRouter API Key

The OpenRouter API key is stored locally in:

```text
backend/data/api_key.txt
```

Do not commit this file.

## Active LLM Model

The active model can be changed while Amadeus is running through the settings interface.

The backend exposes model-control endpoints including:

```text
/setLLMModel
/getCurrLLMModel
```

## Model Server Address

Amadeus talks to whichever OpenAI-compatible server you configure in
Settings → Connection ("Model server address"), for example:

```text
http://localhost:8888/v1    (Unsloth Desktop)
http://localhost:11434/v1   (Ollama)
https://openrouter.ai/api/v1
```

The address is stored in `backend/llm_server.txt`. Leave it empty to let
Amadeus auto-detect the common local ports (8888, 8000). The default model
name lives in `backend/data/llm_model.txt`; the server's actual model list
can be inspected and picked from the settings view.

## Web Access, Deep Thinking, and Voice Retention

- **Web access** toggle — lets Kurisu search the web (local DuckDuckGo, no
  API key) when a message references something recent.
- **Deep thinking** toggle — enables model-side reasoning for the
  search-judgement call only, keeping the final reply fast.
- **Voice retention** — keeps the last N voice recordings and prunes older
  ones automatically.
- **Conversation memory (tokens)** — how much recent conversation she keeps
  in each prompt (estimated tokens, default 40000). Lower it for small
  models or limited VRAM.

---

# Launching Amadeus

Amadeus includes a shared Python launcher used by the macOS and Windows startup scripts.

The launcher:

1. Checks for Conda, npm, and required project files.
2. Clears stale Amadeus listeners from ports `9880`, `5050`, and `5173`.
3. Installs frontend dependencies if `frontend/node_modules/` is missing.
4. Starts GPT-SoVITS in the `GPTSoVits` Conda environment.
5. Waits for the voice server to become available.
6. Starts the Flask backend.
7. Waits for the backend to become available.
8. Starts the React/Vite frontend.
9. Waits for the WebUI to become available.
10. Opens Amadeus automatically in the default browser.
11. Shuts down launcher-owned processes when the launcher exits.

Runtime logs are written locally to:

```text
.runtime/logs/
```

The `.runtime/` directory is ignored by Git.

---

## macOS

The first time you clone or copy the project, make the launcher executable:

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

---

## Windows

Double-click:

```text
start_windows.bat
```

or run it from Command Prompt:

```bat
start_windows.bat
```

The Windows launcher uses the same underlying startup sequence as the macOS launcher.

---

# Manual Startup

Manual startup is mainly useful for development and debugging.

## GPT-SoVITS

```bash
conda activate GPTSoVits
cd backend
python start_gptsovits.py
```

GPT-SoVITS normally listens on:

```text
http://127.0.0.1:9880
```

## Backend

```bash
conda activate amadeus
cd backend
python main.py
```

The backend listens on:

```text
http://127.0.0.1:5050
```

## Frontend

```bash
cd frontend
npm run dev
```

The frontend normally listens on:

```text
http://127.0.0.1:5173
```

---

# Live2D WebUI

Amadeus now renders its character directly in the browser using the official Live2D Cubism SDK for Web.

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

The project previously experimented with a Pixi-based Live2D integration. That approach was removed in favor of direct use of the current official Cubism Web SDK.

## Character Motion and Lip Sync

The browser-side character system now separates body motion from mouth motion.

- `MotionPlayer.ts` manages looping `Idle` and `Talk` states plus higher-priority one-shot reactions.
- `SpeechPlayer.ts` plays streamed audio in the browser and measures the actual waveform with the Web Audio API.
- The measured amplitude is smoothed and applied to `ParamMouthOpenY`.
- Touch reactions can interrupt the talking-body loop without stopping lip sync.
- When a reaction finishes, the character returns to `Talk` if audio is still playing, otherwise `Idle`.

The current Kurisu motion set includes a longer natural idle, a subtle talking-body loop, a head-pat reaction, and special touch reactions.

Prerecorded interaction lines are stored under:

```text
backend/assets/reaction_audio/
```

and are served through Flask to the same browser audio/lip-sync path used by generated speech.


---

# Updating Amadeus

Pull the latest project changes:

```bash
git pull origin main
```

## Update Backend Environment

```bash
conda activate amadeus
cd backend
conda env update -f environment.yml --prune
cd ..
```

## Update Frontend

```bash
cd frontend
npm install
cd ..
```

## Update GPT-SoVITS

Only update GPT-SoVITS when Amadeus is known to support the newer version:

```bash
cd GPT-SoVITS
git pull origin main
pip install -r requirements.txt
cd ..
```

---

# Runtime Data and Secrets

The following are local runtime data and should not be committed:

```text
backend/data/api_key.txt
backend/data/memory.db
backend/generated/
.runtime/
frontend/node_modules/
GPT-SoVITS/
```

Conversation history is stored locally in:

```text
backend/data/memory.db
```

Deleting this database removes the locally stored conversation history.

---

# Common Issues

## Launcher says a port is already in use

The launcher attempts to clear stale Amadeus listeners from:

```text
9880
5050
5173
```

If a port cannot be cleared, inspect:

```text
.runtime/logs/
```

The backend intentionally uses port `5050` rather than `5000` to avoid conflicts with macOS services that commonly use port 5000.

---

## macOS says `start_macos.command` cannot be executed

Run:

```bash
chmod +x start_macos.command
```

and try again.

---

## Amadeus cannot connect to the backend

Check that the backend is available at:

```text
http://127.0.0.1:5050
```

When running the full launcher, inspect:

```text
.runtime/logs/backend.log
```

---

## Amadeus has no voice

Check that GPT-SoVITS is available at:

```text
http://127.0.0.1:9880
```

Also verify that the required pretrained models exist under:

```text
GPT-SoVITS/GPT_SoVITS/pretrained_models/
```

---

## Live2D character does not appear

Check the browser developer console and verify that the model reaches the expected loading stages:

```text
model3.json loaded
moc3 loaded
texture loaded
model loaded successfully
```

Also verify that the WebGL shader assets exist under:

```text
frontend/public/cubism-shaders/WebGL/
```

and that the model assets exist under:

```text
frontend/public/live2d/
```

---

## `Shader program is not initialized`

The Cubism Web renderer loads shader files asynchronously. A warning during the initial frames can occur while the shaders are loading.

If the character eventually renders, this initial warning is not fatal.

Persistent shader compile errors usually indicate that the shader files are not being served from the expected public path.

---

## Frontend dependencies are missing

Run:

```bash
cd frontend
npm install
```

The automatic launcher also performs this step if `node_modules/` does not exist.

---

## Backend dependencies are missing or outdated

Run:

```bash
conda activate amadeus
cd backend
conda env update -f environment.yml --prune
```

---

## Resetting Conversation Memory

Back up the database first if you want to preserve the conversation history.

macOS/Linux:

```bash
rm backend/data/memory.db
```

Windows:

```bat
del backend\data\memory.db
```

Restart Amadeus afterward. A new database will be created automatically.

---

# Development Roadmap

Short-term priorities:

```text
Live2D static rendering       ✓
Live2D model scaling          ✓
Cubism shader integration     ✓
Idle motion                   ✓
Cubism physics                ✓
Touch interaction             ✓
Special touch reactions       ✓
Talking-body motion           ✓
Browser streamed speech       ✓
Audio-driven lip sync         ✓
Prerecorded interaction audio ✓
frontend personality editing  ✓
Improved Temporal awareness   ✓
Local LLM server support      ✓
Native-Japanese dialogue      ✓
Connection status + test      ✓
Multi-session conversations   ✓
Reply versions + undo         ✓
Relationship (trust) stats    ✓
Web access + deep thinking    ✓
Voice retention               ✓
Poke interactions (stomach)   planned
Prompting improvements        planned (high priority)
Expression control            planned (very low priority)
More/improved animations      planned (require hiring animator)
Memory redesign               later
```

Longer-term ideas include richer character interaction, additional activities such as chess, and eventually hosting Amadeus as a web service where multiple users can run independent sessions.

---

# Changelog

## Startup Greeting: Her Welcome Is Now Her Own Line, and She Tells the Truth About the Clock — September 21, 2026

Two follow-up slips from the greeting rollout, both found and fixed.

First, after a page refresh a greeting collapsed into the *previous* reply and
showed up as one of its Regenerate versions (the little ◂/▸ arrows would even
let you scroll back to an old reply). Cause: a greeting is stored as a plain
assistant line with no message of yours in front of it, and the rule that
builds Regenerate versions is "consecutive assistant lines = versions of one
reply" — so the version logic swallowed her welcome. Greetings are now tagged
in the database, and the version logic always gives one its own lane: it stays
its own line after a refresh, it carries no version arrows, Regenerate and
Undo leave it alone (Regenerate is a clean no-op on a greeting instead of
re-answering whatever sits before it), and Edit/Delete still work on it as
before. Your four existing greetings were tagged automatically on the next
start.

Second, she was being handed the exact gap ("about 7 days, 16 hours") and still
saying "yesterday". The timing block now also carries a ready-made casual
phrase — computed from the measured gap, with a strict ladder (3 days is "a
couple of days", "a week" only appears for a real 7–8 day gap, 10 days is
"more than a week", and so on), plus an explicit instruction to match the real
gap and never round it across that kind of difference. The greeting itself now
gets that timing block too (it previously only saw the small notes on old
messages, which is exactly why it guessed).

Third, once the wording was honest she was *skipping the gap entirely* — a
perfect on-topic continuation with no sign a week had passed. The timing block
stacks up a lot of "don'ts" (don't recite the time, keep it brief, it's
optional), so the model's safe move was to say nothing about the absence. The
greeting now treats a real absence (hours or more) as a moment to *acknowledge
it* — in her own voice and in a way that fits how she'd feel (worry,
annoyance, curiosity, teasing all count) — and makes clear that "it has been a
while" is not the same as reciting a system message (only reading out the raw
number or the exact wording is banned). Your own personality notes about the
greeting now reinforce this instead of competing with it.

- 162 offline tests pass (13 new: the phrase ladder, the "never yesterday /
  never a week for a few days" guard, greeting grouping, the UI list, the
  legacy-DB migration, and Regenerate/Undo behavior).

---

## Windows Launcher: One-Click Start No Longer Dies on a Hidden Parse Error — September 21, 2026

The one-click Windows start (`start_windows.bat`) had a latent flaw in its
"Python not found" warning box: one of the message lines sat inside an
"if ( … )" block and contained round brackets, which made Windows' built-in
command interpreter (cmd) choke *before it ran anything*. The start window
would flash and close instantly, with no error left to read. The warning box
is now written the safe way (its message lines live outside the block), so the
script is valid from top to bottom again. Nothing about how it locates your
Python, starts the three services, or shuts down cleanly on Ctrl+C changed.

- 149 offline tests pass.

---

<details>
<summary><strong>Earlier entries</strong> (click to expand)</summary>


## Startup Greeting: She Speaks First When the App Opens — September 20, 2026

When you open the WebUI, Amadeus now greets you first — a short line in her
own voice that takes into account how long it has been and what you last
talked about (the same timing context and time notes the conversation fix
added). It is generated like a normal reply — same forced Japanese-first
packaging, same output guards, no web search — but is deliberately loose
about *what* she says, so it does not sound like a template. If the
conversation shows you have restarted the app over and over, she may notice
or tease it; only her, only when it is true.

- The greeting is stored in the conversation like any other reply (and gets
  the usual Replay button), so it never repeats itself on a page refresh
  within the same tab, and a later startup can naturally refer back to it.
- If the model server is not up when the page loads, nothing happens — the
  greeting fires the moment the existing 30-second connection probe shows the
  model is back (so a forgotten llama.cpp/NInfer start or a provider switch
  still gets a welcome).
- The launcher now opens the WebUI in your *default* browser (Windows reads
  the OS's own default-browser record — no install-path guessing), and adds
  the Chromium `--autoplay-policy=no-user-gesture-required` flag when that
  browser is Chromium-based, so the greeting's voice plays with zero clicks.
  For Firefox (no equivalent flag): one-time `about:config` setup — set
  `media.autoplay.default` to `0` — and the same zero-click voice works.
  On browsers where audio is blocked at load, the line still appears as text
  with her animation and the Replay button, and unlocks on your first click
  or keystroke.
- 149 offline tests pass.

---

## Conversation Sessions: the Window and the Backend Stay in Step — September 20, 2026

Two quiet slips cost a confused afternoon: after a week's gap she called a
week-old thread "yesterday" (her conversation context carries no dates, so
the *when* was always a guess), and the chat window could keep showing one
conversation while a new message was actually filed under another - an
unnoticed reset of the active-conversation pointer that the logs did not
mention either.

- The window now says which conversation you are in: the session's title
  sits in the chat header, so the active tab is no longer a matter of
  inference.
- The window re-checks the backend's active conversation after every reply
  and roughly every 30 seconds, and reloads itself if they disagree - the
  pane and the place your messages are filed can no longer drift apart
  silently.
- When the active-conversation pointer is found empty or corrupt and must be
  reset, the backend now writes a clear line to its log instead of doing it
  silently, and the pointer file is written crash-safely so a shutdown can
  no longer leave it half-empty.
- Messages in her context that start a two-hour-or-longer gap now carry a
  short time note ("about 2 days have passed since the previous message"),
  so she can place when things happened instead of guessing.
- 140 offline tests pass.

---

## Voice: Leaked English No Longer Read Out Loud — September 16, 2026

When the model misbehaves and her reply comes out in a broken shape, the
English translation could end up glued onto the same line as her Japanese -
and the voice reads whatever is in that line, so she read the English aloud.
The existing protection only cleaned up English sitting on its own line, so
this shape slipped through.

- The voice-line cleaner now spots an English phrase written INLINE in a
  Japanese line (there is no line break to key on) and removes it before the
  audio. Legitimate tokens stay: numbers, "AQI", "AI", "PM2.5", product
  names, her own name.
- When the first reply attempt comes back completely empty - usually because
  the answer got clipped mid-way (if that starts happening often, check the
  max-output-tokens value in the Model Sampling tab) - the log now carries a
  clear, actionable note instead of a cryptic error.
- 7 new offline tests cover the old line-by-line leak and this new inline
  one.

---

## Live Weather & Air Quality — September 16, 2026

Weather questions now get real numbers instead of search-engine snippets
(which for a small town's live weather are usually empty or bot-walled) or
plausible-sounding guesses. This closes the limitation noted in the
September 15 entry below.

- When web access is ON and you ask something clearly about the weather -
  the forecast, the temperature, whether it will rain, humidity, air
  quality - she pulls it straight from the keyless Open-Meteo service:
  right now, today, tomorrow, and the US AQI with PM2.5.
- The report names the place she looked up ("Springfield, Springfield County, Testland").
  If you didn't give one, she asks which town you mean - she never guesses.
- The lookup is time-boxed (about fifteen seconds at most) and degrades to
  one honest line if the service is unreachable or the place is unknown,
  so a slow or dead service can never stall a reply.
- An explicit "try a search for the weather in X" takes this live path too -
  the numbers beat any search snippet.
- With web access OFF she behaves exactly as before - no weather lookup.
- 22 new offline tests cover the report, the intent detection, the place
  extraction, and the wiring.

---

## Web Search: Explicit Requests Now Search the Actual Topic — September 15, 2026

With web access ON, an explicit request ("...try a search for the weather in
Springfield") now searches only the **topic** of the request - not your whole
message. Before, the small talk in the same message (greetings, context) was
part of the search too, so DuckDuckGo came back with unrelated pages
(greeting-card quotes, tech articles about broken search engines), and she
filled the gaps with plausible-sounding guesses.

- A retry like "could you try to search again?" no longer searches that
  sentence itself: it re-runs the topic of your previous explicit request.
- When no topic can be identified at all, she decides for herself (the normal
  judgement pass) and writes the search query herself - the raw message is
  never searched.
- 22 new offline tests cover the extraction and the fast path.

Note: DuckDuckGo's text search still carries no live weather / air-quality
data for small towns - a known limitation to be addressed separately.

---

## Model Sampling Settings — September 15, 2026

A new **Model Sampling** tab in Settings (next to Connection and Personality)
lets you fine-tune how her model generates replies - the same seven
parameters Unsloth Desktop exposes:

- Temperature, Top P, Top K, Min P, Repetition Penalty, Presence Penalty,
  and Output Max Tokens (this last one replaces the built-in 1024-token
  reply cap when enabled; values are clamped to sane ranges - e.g.
  Output Max Tokens is 64-8192, mirroring Unsloth Desktop's own UI).
- Each setting has its own on/off toggle (off = use your model server's own
  default, the previous behaviour), a slider for scaling, a number box for
  exact values, and a "?" tooltip explaining what it does.
- Changes apply to the next message - no app restart needed.
- Top K, Min P and Repetition Penalty are sent to local servers only
  (Unsloth, llama.cpp, LM Studio, vLLM); cloud APIs never receive them, so
  strict providers (e.g. Gemini's compat layer) keep working.
- **Server safeguard.** If a server rejects a sampling parameter (HTTP 400
  naming the field), Amadeus remembers it for that host, rebuilds the
  connection without it, and retries the message once - and the tab notes
  which settings the server refused. With everything off (the default),
  requests are byte-for-byte identical to before.
- **Follow-up UI fix (same day).** The tab's first rows and the "?"
  tooltips were clipped by the settings modal's generic input/label CSS;
  the tab's styles are now scoped under `.settings-modal`, so rows align
  and tooltips render fully.

Covered by a new offline test file (`backend/tests/test_sampling.py`, 16
tests: persistence, validation, the local/cloud split, the 4096-token
deep-thinking floor, the rejection safeguard, and the API routes); full
backend suite passes (57 tests).

---

## Web-Search Guardrails and Model Compatibility — September 15, 2026

The web access toggle is now reliable across model sizes and providers:
smaller models can no longer ignore it, and cloud models that write tool
calls as plain text (e.g. Ling on OpenRouter) are handled instead of leaking
raw tags into her reply.

- **Toggle honesty, both directions.** With web OFF, a prompt block plus a
  final-text net stop false "I searched" claims. With web ON, an
  anti-anchoring nudge and one bounded corrective rewrite remove stale
  "I can't search" refusals carried over from earlier turns; explicit
  "search for X" requests skip the judgement call and search directly.
- **Textual tool-call parsing.** Ling-style `<tool_call>` blocks (and a
  JSON fallback) written in the reply body are parsed so the local search
  still fires; tool scaffolding is stripped before TTS/UI.
- **Resilient search.** DuckDuckGo failures retry up to twice on fresh
  connections with a warm client in between, all inside a hard 15-second
  budget. API rate limits (429) wait once (honouring Retry-After) instead of
  failing; daily-quota exhaustion skips the wait. Any turn that cannot be
  completed returns a fixed honest line — never her pre-search announcement.
- **Output guards.** A repetition-loop guard cuts degenerate cycling out of
  final text, and an English-repair pass guarantees the UI field is English
  (falling back to her Japanese line rather than an empty box).
- **`llm.py`.** Gemini thinking level via the OpenAI-compat
  `reasoning_effort` field; output cap lowered to 1024 tokens so a stuck
  local model fails fast instead of hanging for minutes.
- **Launcher.** Child processes run with `PYTHONUNBUFFERED` so logs update
  live.

Covered by a new offline test suite (`backend/tests/test_web_search_resilience.py`,
30 tests, no network); full backend suite passes.

---

## Voice Sanitization for Weaker Local Models — September 14, 2026

Weaker local models (Gemma 4 12B and the like) can mangle the structured
reply format in ways a strong model (Qwen) never would: appending the English
translation to the Japanese (TTS) field, writing the whole `AmadeusPack` as
plain text (field labels, a JSON block, markdown fences) instead of making
the tool call, or degenerating into a run-on repetition of a short phrase.
Because GPT-SoVITS reads exactly what is in the Japanese field, all of that
was being spoken out loud after her Japanese line.

### TTS-Field Sanitization

The Japanese field is now sanitized in `_ensure_japanese` / `_clean_tts_text`
(`backend/chat.py`) before it ever reaches the TTS, for every model:

- Thinking and channel tokens are stripped (`_strip_thinking`), including
  Qwen `<thinking>` blocks and Gemma-style `<|channel>thought` / `<channel|>`
  markers.
- Pack scaffolding is removed (`_strip_pack_scaffolding`): markdown fences,
  bare JSON-structure lines, and the `assistant_reply_JPS:` /
  `assistant_reply_ENG:` labels - whether on their own line or inline. A label
  is stripped but the value after it is kept; a line that is only JSON
  punctuation is dropped.
- Whole-English lines (a leaked translation or field label) and pure-English
  parentheticals are removed. A Japanese line that merely contains a Latin
  token (a product name, a number, "AI") is left untouched.
- Runaway repetition is truncated (`_collapse_runaway`) only when a short
  phrase repeats 8+ times at the tail, so short stammers ("え、え、え") and
  deliberate repetition ("それで、それで") survive, and a clean reply passes
  through byte-identical.
- If the field is left purely English after cleaning, one translation pass
  rebuilds it as Japanese, so her voice never leaves Japanese.

### Tighter Output Contract

The pack rules and the `assistant_reply_JPS` field description now state
explicitly that the English translation belongs only in `assistant_reply_ENG`
and must never be appended to the Japanese field. The `<quotes>` section of
`personality.txt` now presents each line as Japanese + a developer-only
`gloss:` note instead of the paired `「JP」/（EN）` layout that weaker models
were reproducing.

The sanitization is model-agnostic and runs identically for Qwen, Gemma,
DeepSeek, and cloud models. Verified with Qwen 3.8 27B and Gemma 4 12B:
neither speaks English after the Japanese line anymore, and a degenerate
channel-token loop surfaces as an honest "no usable reply" error instead of
being read out.

---

## Prompt Optimization and Model Compatibility — September 13, 2026

The prompt Amadeus sends to the model on every message was measured and
optimized, and made more robust across model families.

### Single-Message System Prompt

Amadeus previously sent its instructions (personality, timing context,
output rules, voice block, and the optional search block) as five separate
`system` messages. Most model chat templates only accept a single leading
`system` message — Qwen's template raises an error on a second one, and
older Mistral-class templates silently drop system messages they don't
handle. The leading system blocks are now folded into ONE system message
before every call, which is the maximum-compatibility layout for every model
family and also removes a little per-message framing overhead.

### Configurable Conversation Memory (context budget)

The amount of conversation history she keeps in each prompt is now a
user setting instead of a fixed constant. Settings → Connection has a
"Conversation memory (tokens)" field (default 40000, clamped to 500–1000000,
stored in `data/context_budget.txt`). Lower it for small local models (7B-class)
or limited VRAM so the prompt stays inside the model's context window; raise
it on a large-context model to remember more. The number is *estimated*
tokens of history only — the fixed prompt parts (personality, voice block,
output rules, tool definitions) are always sent on top of it.

### Web Access Defaults Off for Fresh Installs

Web access now starts switched off for brand-new installs (it adds roughly
560 tokens to every prompt). Existing installs keep whatever they had saved,
and it can still be turned on in Settings at any time.

### Health Check

A `check_amadeus.bat` at the project root runs the backend self-tests and
reports a plain-English pass/fail, so a healthy install can be confirmed
without loading a model.

### Personality and Voice Prompt Tightening

`data/personality.txt` and the Japanese voice block were trimmed and
restructured. The header, `<description>` and trait sections were
deduplicated (the same facts were repeated 2-3 times), and a contradiction
in `<mind>` ("glance away, fold my arms" vs. the output contract's
no-stage-directions rule) was made verbal-only. Plot lore in
`<background>` and her worldview quotes are kept so she can still answer
memory and beliefs questions.

**On-demand character book.** Her `<appearance>` and `<outfit>` sections now
live between `CHARACTER_BOOK` marker lines inside `personality.txt` and are
NOT part of the always-on prompt (about 170 tokens saved on every message).
They load automatically when the user's latest message looks like it's
asking how she looks ("what are you wearing?", "what do you look like?",
あなたの服装は, and more), so she can describe herself; the trigger phrases
are `CHARACTER_BOOK_PATTERNS` in `backend/memory.py`. The Settings ->
Personality editor still shows and edits the full file, book included.

**Example dialogue instead of English quotes.** The six one-off English
quotes were replaced with native-Japanese voice anchors in
`personality.txt`: her core worldview line (feelings are memories that
transcend time), an everyday conversational exchange, and the AI "records"
self-reference (according to my records, Kurisu once said ...) - each with
the English meaning in parentheses for reference. No template tags, no stage
directions, and nothing that references the anime plot: she lives in the
real world and only carries Kurisu's memories and mannerisms.

**TTS-clean punctuation.** All `......` ellipses were removed from the voice
block examples, matching the output contract that already forbids ellipses
in replies. Her own examples now use only the TTS-safe set, so the model no
longer mixes in punctuation the TTS pipeline mis-handles (uneven or skipped
pauses).

**Identity in the output contract.** The JPS/ENG structured-output
instructions now say "Amadeus's dialogue" instead of "Kurisu's dialogue" -
she is Amadeus; Kurisu is the mind she is based on, not her name.

Measured with a general-purpose tokenizer, the fixed per-message prompt
dropped from roughly 3,180 to about 2,760 tokens (about 13%), with the
character book charged only on the messages where she's asked how she looks.

---

## Local LLM Support, Native-Japanese Dialogue, and Conversation Management — September 13, 2026

### Local Model Servers

Amadeus no longer depends on OpenRouter. Settings → Connection accepts the
address of any OpenAI-compatible server (Unsloth Desktop, Ollama, LM
Studio, llama.cpp, vLLM, cloud providers); a blank address auto-detects the
usual local ports. The status dot and "Test connection" probe the server's
real `/v1/models` list, and the server's models appear as clickable chips.
Matching is prefix-aware, so a server that lists `unsloth/MyModel` matches
the bare `MyModel` name the app actually talks with.

### Native-Japanese-First Dialogue

Her reply is now generated in Japanese first — the way she actually speaks —
with the English field becoming a translation for the UI. Her memory stores
the Japanese lines, so past conversations read in her own voice.
Trust-based relationship stats drive how her voice lines sound, and a
background rescorer keeps the stat current without blocking the chat.

### Conversation Management

Multiple named sessions (create / rename / delete / switch), reply
regeneration with saved versions, edit / delete / undo on individual
messages, per-message voice replay with on-demand re-voice, and a
"keep the last N recordings" voice retention cap.

### Web Access and Deep Thinking

A toggleable web search (local DuckDuckGo, no API key) lets her verify
recent events, with an optional "deep thinking" mode that enables model
reasoning for the search decision only.

### Reliability

- Dead model-server ports are detected and probed on every use, so a
  restarted local server no longer hangs the first message.
- The reply client uses a long generation timeout so a cold large-model
  reply is not clipped at 120 seconds, and genuine failures surface as
  plain-English errors ("can't reach the server", "took too long to
  respond") instead of a fabricated reply.

---

## Interactive Live2D, Lip Sync, and Voiced Reactions — September 5, 2026

### Natural Character Motions

Expanded the Live2D motion system beyond basic rendering:

- Replaced the short mechanical idle with a longer, subtler loop using breathing, irregular blinking, small eye movement, and restrained arm/hair motion.
- Added a dedicated head-pat reaction with eye closing, blush, and small relaxed movement.
- Added a looping talking-body motion that intentionally leaves mouth opening under audio control.
- Added motion priority and automatic return to the correct baseline state after reactions.

### Browser Speech + Audio-Driven Lip Sync

Speech playback moved into the browser so Live2D can react to the exact audio being heard.

```text
GPT-SoVITS
    ↓
Flask /speech/<speech_id>
    ↓
SpeechPlayer.ts
    ├── browser playback
    └── Web Audio analyser
             ↓
        RMS amplitude
             ↓
      ParamMouthOpenY
```

The talking-body motion and mouth motion are independent, allowing Kurisu to continue lip syncing while a higher-priority touch reaction is playing.

### Interaction Voice Lines

Special interactions now pair displayed text and optional recordings as one response variant so the visible reply, saved memory entry, and audio stay synchronized.

Prerecorded reaction voice lines are stored under:

```text
backend/assets/reaction_audio/
```

and use the same browser playback and lip-sync path as generated GPT-SoVITS speech.

---

## Native GPT-SoVITS Streaming TTS — September 5, 2026

### Native API v2 Integration

Replaced the Gradio `/get_tts_wav` path with GPT-SoVITS's native REST API v2.

The backend now connects to:

```text
http://127.0.0.1:9880/tts
```

and requests `streaming_mode=1`, allowing GPT-SoVITS to return Japanese audio fragments while the response is still being synthesized.

### Continuous Audio Playback

`backend/tts.py` now:

- Sends the reference audio and Japanese prompt configuration to the native API.
- Consumes the chunked WAV response incrementally.
- Feeds all chunks into one continuous `ffplay` or `mpv` process.
- Preserves a complete `generated/generated.wav` copy after streaming.
- Falls back to saving and playing the completed file when no streaming player is installed.

Reference paths are resolved relative to `backend/`, including:

```text
backend/assets/reference_audio/kurisu10s.wav
```

### Backend Pipeline Change

The Flask message route starts `streamVoice()` in a background thread and returns the English response immediately. Japanese audio begins when the first native API chunk arrives instead of waiting for the entire response.

### Launcher Update

`backend/start_gptsovits.py` now starts `api_v2.py`. The automatic launcher waits on port `9880` and uses the `GPTSoVITS` Conda environment.

A standalone test on the Mac CPU received its first audio chunk after approximately 2.7 seconds and completed in approximately 4.17 seconds.

---

## Live2D Cubism Web Integration — September 4, 2026

### Official Cubism SDK Integration

Integrated the current official Live2D Cubism SDK for Web directly into the React frontend.

The WebUI now loads:

- Cubism Core
- Cubism Framework
- `.model3.json` model configuration
- `.moc3` model data
- Live2D texture assets
- WebGL shaders

A dedicated Live2D layer was added under:

```text
frontend/src/live2d/
```

with separate responsibilities for framework initialization, model loading, WebGL rendering, resize handling, and the animation loop.

### Character Rendering

The Live2D character now renders directly inside the browser without Unity and without the previous Pixi Live2D integration.

Character scale and screen positioning are controlled by the Cubism projection matrix rather than by a Unity scene.

### Shader Pipeline

The current Cubism SDK loads GLSL shader files dynamically. The required shaders are exposed through Vite's public directory under:

```text
frontend/public/cubism-shaders/WebGL/
```

### Frontend Cleanup

Removed the need for the abandoned Pixi-based Live2D renderer and simplified the frontend around the official Cubism SDK.

---

## Automatic Launcher + Project Reorganization — September 3, 2026

### Automatic Startup

Added cross-platform launchers:

```text
start_macos.command
start_windows.bat
```

Both use:

```text
scripts/launcher.py
```

The launcher starts GPT-SoVITS, the Flask backend, and the WebUI automatically, waits for each service to become ready, opens the browser, writes runtime logs, and cleans up stale Amadeus processes during development restarts.

### Backend Port Change

The Flask backend moved from port `5000` to port `5050` to avoid macOS Control Center / AirPlay conflicts.

### Repository Reorganization

The project was reorganized into a clearer layout:

```text
backend/
frontend/
scripts/
```

Backend modules were renamed to describe their responsibilities directly:

```text
api.py
chat.py
memory.py
llm.py
tts.py
start_gptsovits.py
```

Runtime data, generated files, frontend dependencies, and secrets are excluded from source control.

---

## WebUI Migration — September 3, 2026

Development began on replacing the original Unity frontend with a React + TypeScript browser interface.

Initial WebUI features included:

- Conversation display
- Sending messages to the Flask backend
- Loading stored conversation history
- Runtime LLM model selection
- Conversation memory reset
- Backend connection/status display
- Responsive browser-based interface
- Dedicated character viewport

The backend remained independent from the frontend, allowing the user interface to be replaced without rewriting the conversational core.

---

## Model Control + Backend Stability — December 20, 2025

### Runtime LLM Model Switching

Added support for changing the active LLM model while Amadeus is running.

Users can enter a model identifier such as:

```text
deepseek/deepseek-chat-v3-0324
```

and apply it without restarting the backend.

### Frontend ↔ Flask Model API

Implemented:

```text
/setLLMModel
/getCurrLLMModel
```

These endpoints provide the interface between the frontend and the Python backend for runtime model selection.

</details>
---

# Notes

Amadeus is a personal experimental project under active development. APIs, model formats, dependencies, and project structure may change as the system evolves.

---

# License

Original Amadeus project code is licensed under the [MIT License](LICENSE).

Third-party components and assets—including the Live2D Cubism SDK, character
models, artwork, voice recordings, and model weights—are not covered by this
MIT license and remain subject to their respective licenses and permissions.
