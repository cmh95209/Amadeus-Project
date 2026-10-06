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
- LLM: follows the APP'S OWN settings (backend/llm_server.txt +
  backend/data/llm_model.txt + backend/data/api_key.txt), so any local or
  cloud server the app uses works. Thinking is DISABLED via
  chat_template_kwargs when the server accepts it (required where
  supported - the thinking budget otherwise truncates the extraction
  JSON); servers that reject the parameter get one plain retry (mirrors
  the app's maybe_strip_rejected_params). The LLM is NOT required at
  startup: context serving is pure local retrieval, and if the model is
  not loaded, learning simply pauses with turns queued - so switching
  servers between launches can never kill the service.
- Embeddings: Qwen3-Embedding-0.6B IN-PROCESS on CPU (zero VRAM; the GPU
  stays free for the 27 B).
- LAZY ENGINE (2026-10-07, launch #4 in the 18 GB test box): the HTTP
  surface comes up in seconds; the heavy memory engine (16-bit embedder
  + persisted lore cache + extraction state, a few GB of load-time peak)
  builds in a BACKGROUND thread after a short stagger (default 30 s,
  CM_ENGINE_DELAY_SECONDS). While it loads, /context returns the
  no-memory baseline and /save QUEUES turns (nothing is dropped; the
  queue flushes when the engine is ready). On a RAM-constrained machine
  that staggering keeps the sidecar's load peak out of the moment the
  voice engine is loading, which is when the box exhausts its memory
  (launch #4: the voice engine was killed mid-synthesis - WinError 1450 -
  while the sidecar loaded its model at the same time). If the load
  still fails for lack of memory the process exits and the launcher's
  retry loop restarts it (self-heal).
- Data dir: backend/data/character_memory/ (its own SQLite + indexes; user
  data, git-ignored). The app's memory.db is NEVER opened by this process -
  it only receives mirrored turns over HTTP.
- Port 9870 (not 8080/8000/5050/9880/8888), bound to 127.0.0.1 only.
  Test/advanced overrides (every fresh install uses the defaults):
  CM_SIDECAR_PORT, CM_DATA_DIR, CM_ASSETS_DIR, CM_SEED_DIR,
  CM_SERVER_FILE, CM_MODEL_FILE, CM_KEY_FILE, CM_ENGINE_DELAY_SECONDS.

Endpoints:
  GET  /health
  POST /save     {chat_id, role, content, user?, title?, occurred_at?}
  GET  /context?chat_id=...&user=...&exclude_chat_id=...
       -> rendered memory block ("" if empty). exclude_chat_id drops memories
          learned FROM that conversation (two-tier memory: the active
          conversation is already in the app's prompt, so recalling it would
          double-inject its facts).
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
# Low-RAM machines (e.g. the 18 GB test sandbox): the math libraries
# (OpenBLAS/OMP/MKL, used by numpy + the in-process embedder) default to one
# thread PER CPU CORE, and the per-thread buffers exhaust memory while the
# 0.6 B embedder loads - the sidecar died at startup on the fresh-box run
# (2026-10-07). Four threads is plenty for its workload; setdefault lets a
# tuned environment override.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("MKL_NUM_THREADS", "4")

HOST = "127.0.0.1"
PORT = int(os.environ.get("CM_SIDECAR_PORT", "9870"))
USER_ID = "the user"
EXTRACT_INTERVAL = 10               # learn after every N new user turns
DATA_DIR = os.environ.get("CM_DATA_DIR") or \
    os.path.join(PROJECT_ROOT, "backend", "data", "character_memory")
ASSETS = os.environ.get("CM_ASSETS_DIR") or os.path.join(HERE, "assets", "Amadeus")
SEED_DIR = os.environ.get("CM_SEED_DIR") or os.path.join(HERE, "seed_index")
# Persona blurb for the memory extractor (2026-10-05, decision D7): without it
# the extractor prompt knows only the character's NAME, so extracted memories
# can attribute Amadeus's role to Kurisu. Works with lore recall OFF.
PERSONA_BLURB = (
    "Amadeus is an AI assistant built on the memories and personality of "
    "Makise Kurisu, a young neuroscientist. She is a separate individual from "
    "Kurisu: Kurisu is her source - the person she was built from - not the "
    "person she is. In these conversations the user is talking to Amadeus; "
    "Kurisu is only ever referenced as her source."
)
# LLM target: the app's own settings (same files the backend reads), so
# the sidecar always follows whatever server/model the app talks to.
SERVER_FILE = os.environ.get("CM_SERVER_FILE") or os.path.join(PROJECT_ROOT, "backend", "llm_server.txt")
MODEL_FILE = os.environ.get("CM_MODEL_FILE") or os.path.join(PROJECT_ROOT, "backend", "data", "llm_model.txt")
KEY_FILE = os.environ.get("CM_KEY_FILE") or os.path.join(PROJECT_ROOT, "backend", "data", "api_key.txt")


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def _read_app_llm():
    """(server, model, key) from the app's own config files. Defaults only
    if a file is missing/empty - the app always writes them, so this is
    belt-and-braces."""
    def _read(path, default):
        try:
            v = open(path, encoding="utf-8").read().strip()
            return v or default
        except OSError:
            return default
    # Defaults only if a file is missing/empty - the app always writes them, so
    # this is belt-and-braces. The server guess is the standard local-LLM
    # loopback; the MODEL and KEY must NOT guess a specific name/key (2026-10-07:
    # the dev machine's model name leaked into fresh-install logs) - unset stays
    # unset, and every consumer below says "not configured" instead.
    return (_read(SERVER_FILE, "http://127.0.0.1:8080/v1"),
            _read(MODEL_FILE, ""),
            _read(KEY_FILE, ""))


def llm_ready(server, model, key, timeout=5.0):
    """Read-only /models probe (the app does the same in test_server). True
    only when the server answers AND the configured model is actually
    loaded - the guard that prevents hitting a downloaded-but-not-loaded
    model (the 2026-09-26 crash incident)."""
    req = urllib.request.Request(server.rstrip("/") + "/models",
                                 headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            loaded = [m["id"] for m in json.load(r)["data"]]
        return model in loaded, loaded
    except Exception:
        return False, []


def startup_llm_note():
    """Best-effort startup note: is the configured LLM reachable RIGHT NOW?
    Never fatal - the service starts either way; learning just resumes
    once the model is loaded (llm_ready guards every learning batch)."""
    server, model, key = _read_app_llm()
    if not model:
        log("LLM target not configured yet (no model selected in the app) - "
            "starting anyway; long-term memory begins learning once the user "
            "connects a model in Settings (the app's LLM settings are re-read "
            "on every learning batch).")
        return
    ok, loaded = llm_ready(server, model, key)
    if ok:
        log(f"LLM target OK: {server} model={model} (loaded: {loaded})")
    else:
        log(f"LLM target not reachable/loaded yet: {server} model={model} - "
            f"starting anyway; learning resumes once it is (the app's LLM "
            f"settings are re-read on every learning batch).")


log("== amadeus charactermemory sidecar starting ==")
t_start = time.time()
startup_llm_note()

# Light imports only: the HTTP surface below must come up in seconds. The
# heavy engine (torch + 16-bit embedder + lore cache) is built later in a
# background thread by _build_engine() - see the LAZY ENGINE note in the
# module docstring.
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from character_memory.llm.embedding_base import EmbeddingProvider
from character_memory.llm.openai_client import OpenAICompatibleLLM
from character_memory.config import LLMConfig

# Engine state (see _build_engine). The 30 s stagger keeps the engine's
# load-time peak out of the window in which the voice engine is still
# loading (launch #4: simultaneous loads exhausted the 18 GB box); set
# CM_ENGINE_DELAY_SECONDS=0 to load as soon as possible.
_ENGINE_DELAY = float(os.environ.get("CM_ENGINE_DELAY_SECONDS", "30"))
_engine_lock = threading.Lock()
_engine_state = {"status": "pending", "ready": False, "seconds": None, "error": None}
store = None          # set by _build_engine
memories = None       # set by _build_engine
agent = None          # set by _build_engine
_emb = None           # set by _build_engine
_pending_saves = []   # /save turns that arrived while the engine loads
_queue_lock = threading.Lock()
_SAVE_QUEUE_MAX = 1000            # soft cap: the queue is a bridge, not a database
_queue_truncated = False


class STEmbedder(EmbeddingProvider):
    """In-process CPU embedder. NAME+MODULE MATTER: persisted lore indexes
    were built by __main__.STEmbedder (fingerprint-verified) so seeded
    indexes load without rebuild. Do not rename (module-level definition is
    part of the fingerprint)."""
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
    """Their client + Amadeus's thinking-off extra_body. Thinking off is
    REQUIRED where the server accepts it (the thinking budget otherwise
    truncates the extraction JSON); servers that REJECT the parameter
    (HTTP 400/422 - foreign local servers, cloud) get one plain retry,
    mirroring the app's maybe_strip_rejected_params."""
    def chat(self, messages, *, temperature=None, max_tokens=None):
        kw = dict(
            model=self.config.model, messages=messages,
            temperature=self.config.temperature if temperature is None else temperature,
            max_tokens=self.config.max_tokens if max_tokens is None else max_tokens,
        )
        try:
            resp = self._client.chat.completions.create(
                extra_body={"chat_template_kwargs": {"enable_thinking": False}}, **kw)
        except Exception as e:
            msg = repr(e)
            if not ("400" in msg or "422" in msg
                    or "Bad Request" in msg or "Unprocessable" in msg):
                raise
            log("server rejected the thinking-off parameter - plain retry")
            resp = self._client.chat.completions.create(**kw)
        return resp.choices[0].message.content or ""


_llm = None
_llm_sig = None


def get_llm():
    """LLM client built from the app's LIVE settings (hot reload: any change
    of server, model or key in the app is picked up on the next learning
    batch). The key may be empty (no model connected yet): the LLM is
    OPTIONAL - context serving is pure local retrieval and learning just
    stays paused (the llm_ready gate) until the user connects a model. A
    placeholder token keeps the client constructor happy; its requests
    would 401 and are never sent while the gate says the target is down."""
    global _llm, _llm_sig
    server, model, key = _read_app_llm()
    sig = (server, model, key)
    if _llm is None or _llm_sig != sig:
        _llm = NonThinkingLLM(LLMConfig(base_url=server, api_key=key or "not-configured-yet",
                                        model=model, temperature=0.0, max_tokens=2048,
                                        timeout=240.0))
        _llm_sig = sig
        if model:
            log(f"LLM client -> {server} model={model}")
        else:
            log("LLM client pending: no model selected yet (it follows the "
                "app's settings and is rebuilt on the next learning batch)")
    return _llm


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


def _build_engine():
    """The heavy startup, run in a background thread: the 16-bit embedder,
    the persisted lore cache and the extraction state. Until it finishes,
    the endpoints serve the no-memory baseline and /save queues turns.

    The model ships half-precision weights (its config declares bfloat16);
    pin 16-bit explicitly so a fresh install can never silently double the
    footprint by loading full-precision, and load through the low-memory
    path (weights are materialized shard by shard instead of double-copied)
    to keep the load-time memory peak as small as it can be (2026-10-07:
    the 18 GB test box, sharing RAM with a local 27 B model, failed a 55 MB
    allocation). 16-bit vs 32-bit moves retrieval rankings only at ~1e-3
    relative precision - imperceptible for cosine similarity. Encoded
    vectors are cast back to float32 before storage.
    """
    global store, memories, agent, _emb
    with _engine_lock:
        if _engine_state["ready"]:
            return
        _engine_state["status"] = "loading"
        t0 = time.time()
        try:
            import torch
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(
                "Qwen/Qwen3-Embedding-0.6B", device="cpu",
                model_kwargs={"torch_dtype": torch.float16,
                              "low_cpu_mem_usage": True})
            log(f"embedder loaded in {time.time()-t0:.1f}s (CPU 16-bit, "
                f"dim={model.get_embedding_dimension()})")

            from character_memory.rag.hybrid import HybridSearch
            from character_memory.agent import CharacterAgent
            from character_memory.memory.character_base import CharacterInfoMemory
            from character_memory.memory.user_facts import UserFactMemory
            from character_memory.memory.episodic import EpisodicMemory
            from character_memory.memory.user_summary import UserSummaryMemory
            from character_memory.memory.emotion import EmotionStatus
            from character_memory.memory.user_directives import UserDirectiveMemory
            from character_memory.memory.store import SQLiteStore

            _seed_index()
            store = SQLiteStore(os.path.join(DATA_DIR, "memory.db"))
            _emb = STEmbedder(model)
            memories = [
                CharacterInfoMemory(HybridSearch(_emb)),        # lore (RAG, no extraction)
                UserFactMemory(store, HybridSearch(_emb)),
                EpisodicMemory(store, HybridSearch(_emb)),
                UserSummaryMemory(store, HybridSearch(_emb)),
                EmotionStatus(store),
                UserDirectiveMemory(store, HybridSearch(_emb)),
            ]
            agent = CharacterAgent(directory=ASSETS, name="Amadeus", save_directory=DATA_DIR,
                                   persona=PERSONA_BLURB)
            agent.load(llm=get_llm(), embedder=_emb, memories=memories)
            t1 = time.time()
            agent.build()
            log(f"agent built in {time.time()-t1:.1f}s (a ~508s number would mean a lore REBUILD)")

            # Flush the turns that /save queued while the engine loaded
            # (nothing is ever dropped), then resume any unprocessed turns
            # from before a restart.
            with _queue_lock:
                queued = list(_pending_saves)
                _pending_saves.clear()
            if queued:
                log(f"flushing {len(queued)} turn(s) that arrived while the engine loaded")
            for item in queued:
                try:
                    _process_save(item)
                except ValueError as e:
                    log(f"dropping queued turn for chat {item.chat_id} ({e})")
            for _c in agent.list_chats():
                if _c.unextracted():
                    _start_background_extract(_c.id)

            _engine_state.update(status="ready", ready=True, seconds=time.time()-t0)
            log(f"== memory engine ready in {time.time()-t0:.0f}s ==")
        except Exception as e:
            _engine_state.update(status="failed", error=repr(e))
            log(f"WARNING: memory engine failed to load ({e!r}) - the service "
                "keeps serving the no-memory baseline and /save keeps "
                "queuing turns; relaunching Amadeus retries the load")


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
                ok = False
                server, model, key = _read_app_llm()
                ok = bool(model)   # an unconfigured target is never "ready"
                if ok:
                    for _attempt in range(5):   # bounded wait: the model server
                        ok, _loaded = llm_ready(server, model, key)  # may start after the app
                        if ok:
                            break
                        time.sleep(30)
                if not ok:
                    if model:
                        log(f"extraction paused for chat {chat_id}: LLM not ready "
                            f"({server} model={model}) - turns stay queued; they "
                            f"resume on the next learning trigger")
                    else:
                        log(f"extraction paused for chat {chat_id}: no model "
                            f"selected yet - turns stay queued; they resume once "
                            f"the user connects a model in Settings")
                    break
                agent.llm = get_llm()   # follow any app-side server switch
                agent.extract(target=chat)
            log(f"extraction complete for chat {chat_id} ({time.time()-t0:.0f}s)")
        except Exception as e:
            log(f"extraction ERROR for chat {chat_id}: {e!r}")
        finally:
            with _extract_lock:
                _extracting_chats.discard(chat_id)

    threading.Thread(target=work, daemon=True, name=f"extract-{chat_id[:8]}").start()
    return True


# --------------------------------------------------------------------------- #
# Active-conversation exclusion (two-tier memory, 2026-10-05)
# --------------------------------------------------------------------------- #
# The app's prompt already carries the active conversation (recent turns
# verbatim + rolling summary), so memories learned FROM it must not also be
# recalled (double injection, and her own words echoed back at her). While
# the block below is active, every memory recall drops items whose row was
# stamped with `exclude_chat_id` (user_facts / episodic carry a chat_id
# column; memories without one - user_summary, emotion, directives - pass
# through, they cannot be attributed to a single conversation). No library
# files are modified: each memory's _recall_with_temporal is shadowed on the
# instance for the duration of the snapshot.
def _recall_exclude_chat(exclude_chat_id: Optional[str]):
    import contextlib

    @contextlib.contextmanager
    def _guard():
        if not exclude_chat_id:
            yield
            return
        patched = []
        for mem in agent.memories.values():
            orig = mem._recall_with_temporal

            def _wrapped(orig=orig):
                def call(*args, **kwargs):
                    items = orig(*args, **kwargs)
                    return [it for it in items
                            if str(it.metadata.get("chat_id") or "") != exclude_chat_id]
                return call
            mem._recall_with_temporal = _wrapped()
            patched.append((mem, orig))
        try:
            yield
        finally:
            for mem, orig in patched:
                mem._recall_with_temporal = orig

    return _guard()


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


def _get_or_create_chat(chat_id: str, user: str, title: str):
    from character_memory.chat import Chat   # heavy import; engine is ready by now
    chat = agent.load_chat(chat_id)
    if chat is not None:
        return chat
    now = time.time()
    t = title or f"amadeus-{chat_id}"
    agent.store.upsert("chats", {"id": chat_id, "user_id": user, "title": t,
                                 "created_at": now}, pk="id")
    return Chat(chat_id, user, agent.store, title=t, created_at=now)


def _process_save(req) -> "tuple[int, bool]":
    """The /save work for one turn (chat row + message + learning trigger).
    Shared by the live endpoint and the queue flush after the engine loads."""
    if req.role not in ("user", "assistant"):
        raise ValueError("role must be 'user' or 'assistant'")
    if not req.content.strip():
        raise ValueError("empty content")
    with _write_lock:
        chat = _get_or_create_chat(req.chat_id, req.user, req.title)
        chat.add_message(req.role, req.content, occurred_at=req.occurred_at)
    un = len(chat.unextracted())
    started = False
    if req.role == "user" and un >= EXTRACT_INTERVAL:
        started = _start_background_extract(chat.id)
    return un, started


@app.get("/health")
def health():
    if _engine_state["ready"]:
        un = sum(len(c.unextracted()) for c in agent.list_chats())
        chats = len(agent.list_chats())
    else:
        un = 0
        chats = 0
    with _queue_lock:
        queued = len(_pending_saves)
    server, model, key = _read_app_llm()
    ok, _loaded = llm_ready(server, model, key, timeout=3.0)
    return {"ok": True, "service": "amadeus-cm-sidecar", "character": "Amadeus",
            "port": PORT, "engine": _engine_state["status"],
            "engine_seconds": _engine_state["seconds"],
            "queued": queued, "llm": {"base": server, "model": model, "ready": ok},
            "chats": chats, "unextracted": un,
            "extracting": sorted(_extracting_chats), "pid": os.getpid()}


@app.post("/save")
def save(req: SaveIn):
    """Mirror one persisted turn. Fast (one SQLite write); learning runs in
    the background once EXTRACT_INTERVAL new user turns have arrived. While
    the engine is still loading the turn is QUEUED (and flushed when the
    load finishes) - nothing is dropped."""
    global _queue_truncated
    if not _engine_state["ready"]:
        with _queue_lock:
            _pending_saves.append(req)
            if len(_pending_saves) > _SAVE_QUEUE_MAX:
                # Keep only the newest turns: older ones are lost either way
                # (the engine failed or is stalled), and the queue must not
                # become a second, unbounded database.
                del _pending_saves[:-_SAVE_QUEUE_MAX]
                if not _queue_truncated:
                    _queue_truncated = True
                    log(f"WARNING: save queue exceeded {_SAVE_QUEUE_MAX} turns "
                        "while the engine was unavailable - oldest drops are "
                        "now discarded (the memory engine failed to load; "
                        "relaunching Amadeus retries it)")
            queued = len(_pending_saves)
        return {"ok": True, "chat_id": req.chat_id, "queued": True,
                "unextracted": queued, "extraction_started": False}
    try:
        un, started = _process_save(req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "chat_id": req.chat_id, "queued": False,
            "unextracted": un, "extraction_started": started}


@app.get("/context")
def context(chat_id: str, user: str = USER_ID,
            memories: Optional[str] = None,
            exclude_chat_id: Optional[str] = None):
    """Rendered memory block for the chat (query = its latest user message).
    Empty string = nothing to recall -> the caller's prompt stays
    byte-identical to the no-memory baseline (the answer while the engine
    is still loading, too).

    exclude_chat_id (two-tier memory, 2026-10-05): the app passes the ACTIVE
    conversation's id - that conversation is already in its prompt (recent
    turns verbatim + rolling summary), so memories learned FROM it are dropped
    here; everything from other conversations / before this one is kept."""
    if not _engine_state["ready"]:
        return {"chat_id": chat_id, "context_text": "", "sections": {},
                "note": "memory engine still loading - baseline until it is ready"}
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
    with _recall_exclude_chat(exclude_chat_id):
        snap = agent.build_context_snapshot(chat, user_id=user, **opts)
    text = "\n\n".join(snap.sections.values())
    log(f"context chat={chat_id}: {len(text)} chars, {len(snap.sections)} "
        f"sections, {time.time()-t0:.2f}s")
    return {"chat_id": chat_id, "context_text": text,
            "sections": snap.sections, "context_order": list(snap.sections)}


@app.post("/extract")
def extract_now(chat_id: Optional[str] = None):
    if not _engine_state["ready"]:
        return {"ok": False, "targets": [chat_id] if chat_id else [],
                "started": 0, "note": "memory engine still loading"}
    targets = [chat_id] if chat_id else [c.id for c in agent.list_chats()]
    started = 0
    for t in targets:
        if agent.load_chat(t) is not None:
            started += 1 if _start_background_extract(t) else 0
    return {"ok": True, "targets": targets, "started": started}


@app.get("/memories")
def memories_endpoint():
    if not _engine_state["ready"]:
        return {"note": "memory engine still loading"}
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
    log(f"== sidecar service up in {time.time()-t_start:.0f}s; serving on "
        f"http://{HOST}:{PORT} (the memory engine loads in the background "
        f"after ~{int(_ENGINE_DELAY)} s; turns mirror into a queue until "
        f"then - nothing is lost) ==")
    # Stagger the heavy load past the voice engine's own load window (see
    # the LAZY ENGINE note in the docstring). The thread is a daemon: the
    # engine is optional, and its load must never hold up a clean shutdown.
    _engine_thread = threading.Thread(target=_build_engine, daemon=True,
                                      name="cm-engine-load")
    if _ENGINE_DELAY > 0:
        _timer = threading.Timer(_ENGINE_DELAY, _engine_thread.start)
        _timer.daemon = True          # never hold up a clean shutdown
        _timer.start()
    else:
        _engine_thread.start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
