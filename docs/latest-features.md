# Latest features (v3.0)

Everything Amadeus can do today.


The current version (v3.0) includes:

- **Her own built-in local model** (v3.0): Gemma 4 through llama.cpp, set up by
  the installer to fit your graphics card - no account or API key needed
- **Pictures, documents and videos** (v3.0): she looks at pictures, reads PDF /
  Word / text documents (long ones with reading notes) and watches videos
  (still frames + what is said)
- **Closeness levels and feelings** (v3.0): finer levels, short-term feelings
  that fade, things on her mind about you; Settings -> Personality & mood
- **Poke, Restart and Quit** from the app (v3.0)
- **Phone layouts and remote access** (v3.0): open her on your own phone through
  Tailscale - see [Remote access](remote-access.md)
- **Reasoning levels** in Settings (v3.0), replacing the Deep thinking switch

- React + TypeScript browser interface
- Python / Flask backend
- Configurable LLM access: her built-in model, any local OpenAI-compatible server,
  **or** OpenRouter
- **Long-term memory** (CharacterMemory sidecar: facts, episodes, summaries)
- **Voice-first dialogue**: she writes natural spoken Japanese natively; the
  on-screen text is its translation
- **A Japanese voice for everything** (v2.1): numbers, your name and English
  words are read the Japanese way; a personal pronunciation list for fixes
- **About you** (v2.1): optional pronouns and "What she calls you" (fixed, or
  "Let her decide" by trust)
- **First-meeting ceremony**: she wakes up on her own line and is honest
  about what she can't do yet; the model is configured in Settings
- **One-shot installers**: Windows (v4.13) and macOS (v1.5) — both
  cross-platform launcher support included
- Persistent SQLite conversation history
- Runtime LLM model switching
- GPT-SoVITS character voice synthesis (Japanese, streamed, lip-synced)
- Live2D Cubism rendering directly in the WebUI
- Natural looping idle and talking-body motions
- Touch interactions with one-shot character reactions
- Browser-side streamed speech playback
- Audio-amplitude-driven Live2D lip synchronization
- Paired text + prerecorded audio variants for special interactions
- Optional web search (Firecrawl keyless search with a DuckDuckGo-style
  backup, no API key; dated results; also reads the top result's page for
  real content), reads the links you send, plus
  live weather and air quality; the pages she used are listed under her reply
- Live connection status with the server's model list and a "Test
  connection" button
- Trust-based relationship stats and trust-aware voice lines
- Multiple named chat sessions (create, rename, delete, switch)
- Reply regeneration with saved versions, plus edit / delete / undo
- Per-message voice replay and on-demand re-voice
- Voice retention cap (keep the last N recordings)
- Backend connection/status display
- Conversation memory reset controls

The Live2D character system handles idle, talking, touch reactions, motion
priority, and audio-driven mouth movement in the browser. Generated
GPT-SoVITS speech is streamed through Flask to the WebUI, where the same
audio signal sent to the speakers is analyzed to drive `ParamMouthOpenY`.

Next major work includes:

- More expression and motion control from model responses
- Richer hit-area and interaction behavior
- A user-selectable display-language dropdown (her voice stays Japanese)
- Showing her Japanese line alongside the translation
- Expanding the prerecorded interaction voice library
