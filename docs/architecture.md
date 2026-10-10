# Architecture and Project Layout

How the pieces fit together, what lives where, and the stack behind each component.

## Architecture

Amadeus is split into five runtime components (the LLM server is external):

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
└──────────┬──────────────────┬───────────────┘
           │                  │
           ▼                  ▼
┌─────────────────────┐  ┌──────────────────────────────────┐
│ GPT-SoVITS          │  │ CharacterMemory sidecar          │
│ Character voice     │  │ Long-term memory (separate       │
│ synthesis           │  │ process; GPL engine; optional)   │
└─────────────────────┘  └──────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────┐
│ LLM server (yours: local or cloud)          │
│ Unsloth / Ollama / LM Studio / llama.cpp /  │
│ vLLM / OpenRouter / any OpenAI-compatible   │
└─────────────────────────────────────────────┘
```

Default local services:

```text
GPT-SoVITS voice     http://127.0.0.1:9880
Amadeus backend      http://127.0.0.1:5050
Amadeus WebUI        http://127.0.0.1:5173
Memory sidecar       http://127.0.0.1:9870
LLM server           (yours — local port or cloud URL)
```

The AI backend is intentionally independent from the frontend. This allowed
the original Unity interface to be replaced by a browser-based React/WebGL
frontend without rewriting the conversational core, and the memory engine to
live in its own process.

---

## Project Structure

```text
Amadeus-Project/
├── backend/
│   ├── main.py                # Flask app entry point
│   ├── api.py                 # REST API
│   ├── chat.py                # conversation pipeline (voice-first)
│   ├── memory.py              # SQLite conversation history
│   ├── llm.py                 # LLM access (local or OpenRouter)
│   ├── tts.py                 # GPT-SoVITS voice client
│   ├── start_gptsovits.py     # voice engine startup
│   ├── ceremony.py            # first-meeting / wake-up logic
│   ├── ja_voice.py            # her Japanese voice rules
│   ├── cm_bridge.py           # bridge to the memory sidecar
│   ├── weather.py             # live weather / air quality
│   ├── webfetch.py            # web search result reading
│   ├── console_encoding.py    # UTF-8 console hardening
│   ├── stats.py               # trust / relationship stats
│   ├── environment.yml        # backend env (python 3.10)
│   ├── requirements.in        # backend dependencies
│   ├── assets/                # prerecorded interaction audio
│   └── data/                  # runtime data (git-ignored)
│
├── frontend/
│   ├── cubism/                # Live2D Cubism SDK (Core/Framework)
│   ├── public/
│   │   ├── cubism-shaders/
│   │   ├── live2d/            # character model assets
│   │   └── live2dcubismcore.min.js
│   ├── src/
│   │   ├── components/
│   │   │   ├── Live2DCharacter.tsx
│   │   │   └── SamplingSettings.tsx
│   │   ├── live2d/
│   │   │   ├── cubismBootstrap.ts
│   │   │   ├── KurisuController.ts
│   │   │   ├── KurisuModel.ts
│   │   │   ├── MotionPlayer.ts
│   │   │   └── textureLoader.ts
│   │   ├── audio/
│   │   │   └── SpeechPlayer.ts
│   │   ├── App.tsx
│   │   ├── api.ts
│   │   ├── interactions.ts
│   │   ├── main.tsx
│   │   └── styles.css
│   ├── index.html
│   ├── package.json
│   └── vite.config.ts
│
├── memory_sidecar/            # long-term memory (v2.0)
│   ├── cm_sidecar.py          # sidecar entry point (port 9870)
│   ├── requirements.txt       # pinned sidecar dependencies
│   ├── lib/                   # vendored CharacterMemory engine (GPL)
│   ├── seed_index/            # her lore, re-seeded on first start
│   ├── LICENSE-NOTE.md
│   └── venv/                  # built on first launch (git-ignored)
│
├── scripts/
│   ├── launcher.py            # cross-platform automatic launcher
│   ├── install_windows.ps1    # Windows one-shot installer (v4.11)
│   └── install_macos.sh       # macOS one-shot installer (v1.2)
│
├── GPT-SoVITS/                # voice engine (cloned locally, git-ignored)
├── docs/images/
├── start_macos.command
├── start_windows.bat
├── check_amadeus.bat          # backend self-test (no model needed)
├── README.md
├── LICENSE
└── .gitignore
```

GPT-SoVITS is cloned separately into a local `GPT-SoVITS/` directory. It is
an external dependency rather than part of the Amadeus repository itself.

---

## Technology

### Backend

- Python 3.10 (backend environment)
- Python 3.13 (memory sidecar environment)
- Flask
- SQLite
- Any OpenAI-compatible LLM server (local) or OpenRouter
- GPT-SoVITS
- CharacterMemory (vendored, GPL-3.0 — isolated in the sidecar process)

### Frontend

- React 19
- TypeScript
- Vite
- WebGL
- Live2D Cubism SDK for Web

The Live2D runtime does not require users to install Cubism Editor or Unity.
The required Web runtime files and shaders are included with the project
frontend.
