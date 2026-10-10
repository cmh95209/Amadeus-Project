# Amadeus-L

Amadeus-L is a Steins;Gate 0-inspired AI character assistant designed to
replicate the Amadeus AI assistant. This project recreates that in-story
character as a real, usable companion: she thinks and speaks Japanese
natively with English translation shown on screen.

The project combines configurable LLMs, long-term memory, persistent
conversation history, customizable character behavior, Japanese-first
bilingual dialogue, neural voice synthesis, and Live2D character rendering
in a single interactive system.

Amadeus-L is a fork of the original project by @reflectors02, adapting it
and adding experimental features such as local LLM support and other
bleeding edge features such as web search.

The project is still actively evolving. It is not intended to be a finished
product; it is an ongoing attempt to explore what happens when an AI character
is given personality, voice, visual presence, and continuity over time.

**As of v2.0 (October 2026)**, Amadeus-L has long-term memory, voice-first
native-Japanese dialogue, greeting systems, web search and weather modules,
and installer scripts for Windows and Mac. See the [Changelog](docs/changelog.md).

<img width="3434" height="2015" alt="image" src="https://github.com/user-attachments/assets/cee65838-11da-464a-8072-c7fb0ad24c39" />
<img width="3434" height="2015" alt="image" src="https://github.com/user-attachments/assets/1c6b2a62-d2ca-4a1e-ba03-ad183f5e01de" />

---

# Quick Start

The fastest way to run Amadeus is a single copy-paste command. It installs
everything (Python environments, the voice engine, her voice models, the web
interface) and then you just start the app.

Note: To use Amadeus as a companion and AI assistant, you will require either
a local LLM or a cloud LLM model to connect with the app.

## Windows

1. Press the Windows key, type **PowerShell**, right-click it and choose
   **Run as administrator**.
2. Paste this one line and press Enter:

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass; irm "https://raw.githubusercontent.com/cmh95209/Amadeus-Project/main/scripts/install_windows.ps1" -OutFile "$HOME\Downloads\install_windows.ps1"; & "$HOME\Downloads\install_windows.ps1"
   ```

3. Wait. On a fresh PC the download-heavy steps (the voice engine and its
   models) take a while; on a machine that has run the installer before, most
   steps are skipped. The installer is safe to re-run if it ever stops
   partway — it resumes where it left off — and it writes a log to
   `install_log.txt` in the folder it installed into.
4. When it prints **ALL DONE**, double-click `start_windows.bat` in the
   Amadeus folder (by default your `C:\Users\YOURUSERNAME\Amadeus` folder).

To install into a different folder, add a parameter at the end of the command:

```powershell
Set-ExecutionPolicy -Scope Process Bypass; irm "https://raw.githubusercontent.com/cmh95209/Amadeus-Project/main/scripts/install_windows.ps1" -OutFile "$HOME\Downloads\install_windows.ps1"; & "$HOME\Downloads\install_windows.ps1" -InstallDir "D:\Amadeus"
```

Prefer to download the installer by hand? Both installer scripts are attached
to the [v2.0 release](https://github.com/cmh95209/Amadeus-Project/releases/tag/v2.0).

## macOS (Apple Silicon and Intel)

1. Open **Terminal** (the one-shot installer works on a brand-new Mac — it
   installs any missing developer tools itself, and it never asks for your
   administrator password).
2. Paste this one line and press Enter:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/cmh95209/Amadeus-Project/main/scripts/install_macos.sh -o install_macos.sh && bash install_macos.sh
   ```

3. Wait (a fresh Mac downloads several gigabytes of models), then double-click
   `start_macos.command` in the Amadeus folder (by default `~/Amadeus`).

Prefer to download the installer by hand? The Windows and macOS installer script is
attached to the [v2.0 release](https://github.com/cmh95209/Amadeus-Project/releases/tag/v2.0).

## Your first meeting

The first time you start the app, Amadeus wakes up with her own line — and
she is honest about what she still can't do. Before she can talk you through
anything, you have to give her a brain:

1. Open **Settings → Connection** in the app.
2. Point her at a language model — **any** of these work:
   - a local server on your own GPU (Unsloth Desktop, Ollama, LM Studio,
     llama.cpp, vLLM, or any OpenAI-compatible server) — leave the address
     field empty and she auto-detects the usual local ports (8888, 8000);
   - or a cloud model through **OpenRouter** (or any OpenAI-compatible
     endpoint) with your API key.
3. Pick the model name (the server's real model list appears as clickable
   chips, and "Test connection" verifies it before you commit).
4. Save. She speaks her real wake-up line, and the conversation begins.

Amadeus does **not** bundle or automatically install a local LLM — run the
model with a server of your choice and point Amadeus at it. The installer
handles everything *except* the model server.

Prefer to install it by hand? The collapsible **Manual Installation**
section further down lists every step the installer scripts do for you, in
case you prefer to do it yourself or run into something the scripts cannot
handle.

---

# How Conversation and Voice Work

Amadeus separates the **text you read** from the **voice you hear**.

You can chat with her in **English** (or any language she can understand).
For each normal response, Amadeus asks the LLM to write her reply **natively
in Japanese first**, then a translation of it for the UI — in a single model
call:

- **Japanese (spoken line)** — natural spoken Japanese, written the way she
  would actually talk; sent to GPT-SoVITS for voice synthesis. This is also
  what is stored in her memory, so her own history stays in her own voice.
- **English (display line)** — a translation of that Japanese line, displayed
  in the conversation UI. Her voice is Japanese-only by design.

So a typical conversation looks like:

```text
You type a message
        │
        ▼
      LLM
        │
        ├── Japanese dialogue (native) ─► GPT-SoVITS ─► Amadeus speaks Japanese
        │
        └── English translation ─────► displayed in the WebUI
```

### Does Amadeus run a local LLM?

**Yes — or it can.** The conversational LLM can run **on your own GPU**
(Unsloth Desktop, Ollama, LM Studio, llama.cpp, vLLM, or any
OpenAI-compatible server) **or** remotely through **OpenRouter** with your
own API key.

Point Amadeus at your model server in Settings → Connection ("Model server
address"). Leave the field empty and Amadeus auto-detects the usual local
ports (8888, 8000) — which is how it finds an Unsloth Desktop server that
picks a new port every time it restarts.

The connection status dot and the "Test connection" button ask the server
for its real model list, so a mistyped model name shows an amber warning
instead of failing mid-conversation, and the server's models appear as
clickable chips.

What runs locally:

- **The conversational LLM** (when pointed at a local server) — on your GPU
- **GPT-SoVITS** — Japanese voice synthesis
- **The CharacterMemory sidecar** — her long-term memory (small; CPU is fine)
- **Live2D / Cubism** — character rendering and animation
- **Conversation history** — stored locally in SQLite
- **Amadeus frontend and backend**

What can run remotely:

- **LLM inference** — through OpenRouter (or any OpenAI-compatible endpoint)

Amadeus does **not bundle or automatically install a local LLM** — run the
model with a server of your choice and point Amadeus at it.

---

# Development Roadmap

Status as of v2.0:

```text
Live2D static rendering       ✓
Live2D + lip sync             ✓
Interaction reactions         ✓
Streaming voice               ✓
Local LLM support             ✓
Native-Japanese dialogue      ✓
Connection status + test      ✓
Multi-session conversations   ✓
Reply versions + undo         ✓
Relationship (trust) stats    ✓
Web access + deep thinking    ✓
Live weather & air quality    ✓
Voice retention               ✓
First-meeting ceremony        ✓
Long-term memory (v2.0)       ✓
One-shot installers (Win/Mac) ✓
Display-language dropdown     planned (her voice stays Japanese)
Show her Japanese line        planned
Better animations/Live2D      planned
Better web and harness tools  planned
Agentic workflows             planned

---

# Documentation

The longer sections of this README live in the [docs/ folder](docs/).

| Section | What's inside |
|---------|---------------|
| [Long-Term Memory (v2.0)](docs/long-term-memory.md) | How her memory works and where it lives |
| [Latest features (v2.0)](docs/latest-features.md) | Everything she can do today |
| [Architecture](docs/architecture.md) | The five components and how they talk to each other |
| [Project Structure](docs/architecture.md#project-structure) | What lives in each folder |
| [Technology](docs/architecture.md#technology) | The stack behind each component |
| [Manual Installation](docs/manual-installation.md) | The hand-install, step by step |
| [Configuration](docs/using-amadeus.md#configuration) | Model server, API key, web access, deep thinking |
| [Launching Amadeus](docs/using-amadeus.md#launching-amadeus) | What the launcher starts, and how |
| [Manual Startup](docs/using-amadeus.md#manual-startup) | Starting each service by hand |
| [Live2D WebUI](docs/using-amadeus.md#live2d-webui) | Her visual companion in the browser |
| [Updating Amadeus](docs/using-amadeus.md#updating-amadeus) | Keeping an install fresh |
| [Runtime Data and Secrets](docs/using-amadeus.md#runtime-data-and-secrets) | Where her memories and your keys live |
| [Common Issues](docs/common-issues.md) | Fixes for the problems people actually hit |
| [Changelog](docs/changelog.md) | Every release since the first build |
| [Notes](docs/notes.md) | Small project notes |
| [License](docs/license.md) | MIT app, MIT voice engine, isolated GPL-3.0 memory engine |

---

# License

The Amadeus project code is MIT-licensed; her voice engine (GPT-SoVITS) is
MIT; her long-term memory engine (CharacterMemory) is GPL-3.0 and runs only
inside the sidecar's own process. Full notes and credits:
[docs/license.md](docs/license.md).

The voice sample is a copyright of the original IP holders (MAGES inc and relevant parties), and is only used for entertainment and personal purposes.
Do not distribute or sell the voice sample without permission of the original IP holders.
