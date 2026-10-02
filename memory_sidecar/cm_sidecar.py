"""CharacterMemory sidecar for Amadeus - standalone local memory service.

Runs the vendored charactermemory library (0.1.2, see lib/ and
LICENSE-NOTE.md) as its own local HTTP process, exactly like GPT-SoVITS.
The Amadeus backend mirrors each persisted turn to /save and fetches
/context before each reply.

Design decisions:
- Context-only adoption (their "mode B"): the app keeps its whole reply
  pipeline; this service only LEARNS from mirrored turns and serves a
  rendered memory block.
- Memories IN: character_info (lore RAG) + user_facts, episodic,
  user_summary, emotion, user_directives.
- Memories OUT: world, knowledge graph, calendar (user decisions).
- LLM: ninfer 127.0.0.1:8080 / qwen3.8-27b-nvfp4 ONLY (hard rule), thinking
  DISABLED via chat_template_kwargs (required for this model - extraction
  truncates otherwise).
- Embeddings: Qwen3-Embedding-0.6B IN-PROCESS on CPU (zero VRAM; the GPU
  stays free for the 27B).
- Data dir: backend/data/character_memory/ (its own SQLite + indexes; user
  data, git-ignored). The app's memory.db is NEVER opened by this process -
  it only receives mirrored turns over HTTP.
- Port 9870 (not 8080/8000/5050/9880/8888), bound to 127.0.0.1 only.

Endpoints:
  GET  /health
  POST /save     {chat_id, role, content, user?, title?, occurred_at?}
  GET  /context?chat_id=...&user=...   -> rendered memory block ("" if empty)
  POST /extract  (optional chat_id)    -> flush pending learning now
  GET  /memories                       -> per-memory rows (inspection)

Run: started by the launcher (first run creates venv/ and pip-installs
requirements.txt); or directly: venv/Scripts/python.exe cm_sidecar.py
"""
import json, os, shutil, sys, threading, time, urllib.request
from datetime import datetime
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "lib"))   # vendored character_memory 0.1.2

# Detached-process home defaults (WORKING_BRIEF 2026-09-30 gotcha b):
# processes spawned outside a full user session lack USERPROFILE/HOME, which
# breaks sentence-transformers' cache resolution.
os.environ.setdefault("USERPROFILE", r"C:\Users\xlhhm")
os.environ.setdefault("HOME", r"C:\Users\xlhhm")
os.environ.setdefault("LOCALAPPDATA", r"C:\Users\xlhhm\AppData\Local")

BASE = "http://127.0.0.1:8080/v1"   # HARD RULE: ninfer 8080 only
MODEL = "qwen3.8-27b-nvfp4"
HOST = "127.0.0.1"
PORT = 9870
USER_ID = "the user"
EXTRACT_INTERVAL = 10               # learn after every N new user turns
DATA_DIR = os.path.join(PROJECT_ROOT, "backend", "data", "character_memory")
ASSETS = os.path.join(HERE, "assets", "Kurisu")
SEED_DIR = os.path.join(HERE, "seed_index")


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def preflight():
    """Read-only /v1/models probe (max allowed by the hard rule). Abort before
    the service can serve if the LLM target is not the loaded model."""
    assert ":8080" in BASE and ":8888" not in BASE, "LLM target must be ninfer 8080"
    with urllib.request.urlopen(BASE + "/models", timeout=10) as r:
        loaded = [m["id"] for m in json.load(r)["data"]]
    if MODEL not in loaded:
        raise SystemExit(f"LLM preflight FAILED: {MODEL} not loaded on 8080: {loaded}")
    log(f"LLM preflight OK: {BASE} models={loaded}")


log("== amadeus charactermemory sidecar starting ==")
t_start = time.time()
preflight()

t0 = time.time()
from sentence_transformers import SentenceTransformer
_model = SentenceTransformer("Qwen/Qwen3-Embedding-0.6B", device="cpu")
log(f"embedder loaded in {time.time()-t0:.1f}s (CPU, dim={_model.get_embedding_dimension()})")

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from character_memory.llm.embedding_base import EmbeddingProvider
from character_memory.rag.hybrid import HybridSearch
from character_memory.agent import CharacterAgent
from character_memory.chat import Chat
from character_memory.memory.character_base import CharacterInfoMemory
from character_memory.memory.user_facts import UserFactMemory
from character_memory.memory.episodic import EpisodicMemory
from character_memory.memory.user_summary import UserSummaryMemory
from character_memory.memory.emotion import EmotionStatus
from character_memory.memory.user_directives import UserDirectiveMemory
from character_memory.memory.store import SQLiteStore
from character_memory.llm.openai_client import OpenAICompatibleLLM
from character_memory.config import LLMConfig


class STEmbedder(EmbeddingProvider):
    """In-process CPU embedder. NAME+MODULE MATTER: persisted lore indexes
    were built by __main__.STEmbedder (fingerprint-verified) so seeded
    indexes load without rebuild. Do not rename."""
    def __init__(self, model):
        self._model = model
    @property
    def dim(self):
        return self._model.get_embedding_dimension()
    def embed(self, texts):
        if isinstance(texts, str):
            texts = [texts]
        return self._model.encode(list(texts), normalize_embeddings=True,
                                  batch_size=16, show_progress_bar=False).astype("float32")


class NonThinkingLLM(OpenAICompatibleLLM):
    """Their client + the exact extra_body Amadeus's own thinking-off client
    sends. REQUIRED: the model's thinking budget otherwise truncates the
    extraction JSON (crash-incident rule; see WORKING_BRIEF)."""
    def chat(self, messages, *, temperature=None, max_tokens=None):
        resp = self._client.chat.completions.create(
            model=self.config.model, messages=messages,
            temperature=self.config.temperature if temperature is None else temperature,
            max_tokens=self.config.max_tokens if max_tokens is None else max_tokens,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        return resp.choices[0].message.content or ""


KEY = open(os.path.join(PROJECT_ROOT, "backend", "data", "api_key.txt"),
           encoding="utf-8").read().strip()
llm = NonThinkingLLM(LLMConfig(base_url=BASE, api_key=KEY, model=MODEL,
                               temperature=0.0, max_tokens=2048, timeout=240.0))
_emb = STEmbedder(_model)


def _seed_index():
    """One-time: seed the persisted lore index + the dialogue_index placeholder
    so the first startup skips the ~8.5-min lore rebuild (gotcha a). The
    placeholder is never loaded - dialogue memory is OFF; it only satisfies
    the library's load-or-rebuild gate."""
    os.makedirs(DATA_DIR, exist_ok=True)
    for sub in ("info_index", "dialogue_index"):
        src, dst = os.path.join(SEED_DIR, sub), os.path.join(DATA_DIR, sub)
        if os.path.isdir(src) and not os.path.exists(os.path.join(dst, "nodes.json")):
            shutil.copytree(src, dst)
            log(f"seeded {sub}/ from {os.path.basename(SEED_DIR)}")


_seed_index()

store = SQLiteStore(os.path.join(DATA_DIR, "memory.db"))
memories = [
    CharacterInfoMemory(HybridSearch(_emb)),        # lore (RAG, no extraction)
    UserFactMemory(store, HybridSearch(_emb)),
    EpisodicMemory(store, HybridSearch(_emb)),
    UserSummaryMemory(store, HybridSearch(_emb)),
    EmotionStatus(store),
    UserDirectiveMemory(store, HybridSearch(_emb)),
]
agent = CharacterAgent(directory=ASSETS, name="Kurisu", save_directory=DATA_DIR)
agent.load(llm=llm, embedder=_emb, memories=memories)
t0 = time.time()
agent.build()
log(f"agent built in {time.time()-t0:.1f}s (a ~508s number would mean a lore REBUILD)")

# --------------------------------------------------------------------------- #
# Extraction worker (background; idempotent, resumable, one thread per chat)
# --------------------------------------------------------------------------- #
_write_lock = threading.Lock()
_extract_lock = threading.Lock()
_extracting_chats: set = set()


def _start_background_extract(chat_id: str) -> bool:
    with _extract_lock:
        if chat_id in _extracting_chats:
            return False
        _extracting_chats.add(chat_id)

    def work():
        t0 = time.time()
        try:
            while True:
                chat = agent.load_chat(chat_id)
                if chat is None or not chat.unextracted():
                    break
                agent.extract(target=chat)
            log(f"extraction complete for chat {chat_id} ({time.time()-t0:.0f}s)")
        except Exception as e:
            log(f"extraction ERROR for chat {chat_id}: {e!r}")
        finally:
            with _extract_lock:
                _extracting_chats.discard(chat_id)

    threading.Thread(target=work, daemon=True, name=f"extract-{chat_id[:8]}").start()
    return True


# Resume any unprocessed turns (e.g. after a restart mid-learning).
for _c in agent.list_chats():
    if _c.unextracted():
        _start_background_extract(_c.id)

# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #
app = FastAPI(title="amadeus-charactermemory-sidecar")


class SaveIn(BaseModel):
    chat_id: str
    role: str
    content: str
    user: str = USER_ID
    title: str = ""
    occurred_at: Optional[float] = None


def _get_or_create_chat(chat_id: str, user: str, title: str) -> Chat:
    chat = agent.load_chat(chat_id)
    if chat is not None:
        return chat
    now = time.time()
    t = title or f"amadeus-{chat_id}"
    agent.store.upsert("chats", {"id": chat_id, "user_id": user, "title": t,
                                 "created_at": now}, pk="id")
    return Chat(chat_id, user, agent.store, title=t, created_at=now)


@app.get("/health")
def health():
    un = sum(len(c.unextracted()) for c in agent.list_chats())
    return {"ok": True, "service": "amadeus-cm-sidecar", "character": "Kurisu",
            "port": PORT, "llm": {"base": BASE, "model": MODEL},
            "chats": len(agent.list_chats()), "unextracted": un,
            "extracting": sorted(_extracting_chats), "pid": os.getpid()}


@app.post("/save")
def save(req: SaveIn):
    """Mirror one persisted turn. Fast (one SQLite write); learning runs in
    the background once EXTRACT_INTERVAL new user turns have arrived."""
    if req.role not in ("user", "assistant"):
        raise HTTPException(status_code=400, detail="role must be 'user' or 'assistant'")
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="empty content")
    with _write_lock:
        chat = _get_or_create_chat(req.chat_id, req.user, req.title)
        chat.add_message(req.role, req.content, occurred_at=req.occurred_at)
    un = len(chat.unextracted())
    started = False
    if req.role == "user" and un >= EXTRACT_INTERVAL:
        started = _start_background_extract(chat.id)
    return {"ok": True, "chat_id": chat.id, "unextracted": un,
            "extraction_started": started}


@app.get("/context")
def context(chat_id: str, user: str = USER_ID,
          memories: Optional[str] = None):
    """Rendered memory block for the chat (query = its latest user message).
    Empty string = nothing to recall -> the caller's prompt stays
    byte-identical to the no-memory baseline."""
    t0 = time.time()
    chat = agent.load_chat(chat_id)
    if chat is None:
        return {"chat_id": chat_id, "context_text": "", "sections": {}}
    n_user = agent.store.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE chat_id = ? AND role = 'user'",
        [chat_id])[0]["n"]
    if n_user == 0:
        return {"chat_id": chat_id, "context_text": "", "sections": {}}
    opts: dict = {}
    if memories:
        # Comma-separated memory names, e.g. 'user_facts,episodic,emotion'.
        # Lets the bridge choose which sections land in the prompt.
        opts["memory_types"] = [m.strip() for m in memories.split(",") if m.strip()]
    snap = agent.build_context_snapshot(chat, user_id=user, **opts)
    text = "\n\n".join(snap.sections.values())
    log(f"context chat={chat_id}: {len(text)} chars, {len(snap.sections)} "
        f"sections, {time.time()-t0:.2f}s")
    return {"chat_id": chat_id, "context_text": text,
            "sections": snap.sections, "context_order": list(snap.sections)}


@app.post("/extract")
def extract_now(chat_id: Optional[str] = None):
    targets = [chat_id] if chat_id else [c.id for c in agent.list_chats()]
    started = 0
    for t in targets:
        if agent.load_chat(t) is not None:
            started += 1 if _start_background_extract(t) else 0
    return {"ok": True, "targets": targets, "started": started}


@app.get("/memories")
def memories():
    out = {}
    for name, m in agent.memories.items():
        if hasattr(m, "table"):
            try:
                rows = agent.store.select(m.table, limit=200)
                out[name] = {"count": len(rows), "rows": rows[:50]}
            except Exception as e:
                out[name] = {"error": repr(e)}
        else:
            out[name] = {"note": "no table (RAG or state memory)"}
    return out


if __name__ == "__main__":
    import uvicorn
    log(f"== sidecar ready in {time.time()-t_start:.0f}s total; serving on "
        f"http://{HOST}:{PORT} ==")
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
