from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS

from chat import (
    getOutputPacked,
    generate_greeting,
    apply_interaction_trust,
    regenerateReply,
    regenerate_greeting,
    setKey,
    has_api_key,
    resetMemory,
    setLLMModel,
    getLLMModel,
    get_raw_memory,
    SpecialInteraction,
    setPersonality,
    getPersonality,
    reset_llm,
    AmadeusPack,
    _store_greeting_line,
    GreetingSuperseded,
)

import attachments
import transcribe
import video
import builtin_llm
import builtin_server
import ceremony
import concerns
import defaults_refresh
import feelings
import llm
import memory
import remote_access
import restart_control
import speech_text
import stats
import timing
import voice_gate
import providers
import vertex_auth

from tts import streamVoiceChunks, renderVoiceToPath
import tts as tts_module
import requests

import chat
import json
import os
import queue
import threading
import uuid
import time
from itertools import chain
from pathlib import Path

application = Flask(__name__)
CORS(application)


# LLM provider picker (feature/provider-picker): the provider table and the
# per-provider keys live in providers.py. Its key hooks hand the chosen key
# to the running app - api_key.txt (the memory sidecar reads it) plus the
# in-memory key and a rebuilt client; the clear hook empties it (a provider
# without a key may still work, e.g. local servers).
def _clear_llm_key():
    memory.save_api_key("")
    chat.API_KEY = ""
    reset_llm()


providers.set_key_hook = setKey
providers.clear_key_hook = _clear_llm_key


# "Last seen" presence (2026-10-15, memory.record_presence): every greeting
# request means the user is in the app. It is recorded AFTER the request has
# run, so the greeting itself measured the user's PREVIOUS visit, not the
# one it is starting. Never breaks a response.
import re as _re
_PRESENCE_PATHS = _re.compile(
    r"^/(greet|conversations/\d+/(?:greet|poke)|greetings/\d+/regenerate)$")


@application.after_request
def _record_presence_after_greeting(response):
    if request.method == "POST" and _PRESENCE_PATHS.match(request.path or ""):
        try:
            # Not during the first-launch ceremony: nothing may touch the
            # store before she is online (her wake-up is a first meeting
            # anyway), and the ceremony guards promise exactly that.
            if memory.load_ceremony_state() == memory.CEREMONY_DONE:
                memory.record_presence()
        except Exception as exc:
            print("[Flask] Presence not recorded:", repr(exc))
    return response


# pre: the WebUI window is visible (it pings on an interval and when the
#      window comes back into view)
# post: the current visit is extended, or a new one started; 200 {"ok": true}
@application.route("/presence", methods=["POST"])
def presence():
    body = request.get_json(silent=True)
    conv = body.get("conversation_id") if isinstance(body, dict) else None
    if not (isinstance(conv, int) and not isinstance(conv, bool)):
        conv = None
    if memory.load_ceremony_state() != memory.CEREMONY_DONE:
        return jsonify({"ok": True, "recorded": False})  # first launch: no store
    try:
        memory.record_presence(conv)
    except Exception as exc:
        print("[Flask] Presence not recorded:", repr(exc))
        return jsonify({"ok": False}), 500
    return jsonify({"ok": True})


# Settings -> System (2026-11-01): the live state of her parts and the
# latest restart (restart_control.py).
@application.route("/system/status", methods=["GET"])
def system_status_route():
    return jsonify(restart_control.system_status())


# pre: Amadeus was started by its launcher (AMADEUS_LAUNCHER_DIR is set)
# post: a restart request is left for the launcher, which restarts her brain
#       (this process), voice and memory within a second; 200 with the
#       starting status. No launcher -> 409; one already running -> 409.
@application.route("/system/restart", methods=["POST"])
def system_restart_route():
    try:
        result = restart_control.request_restart()
    except OSError as exc:
        print("[Flask] Restart request failed:", repr(exc))
        return jsonify({"message": "Could not ask for a restart."}), 500
    if not result["ok"]:
        message = ("Restart works only when Amadeus was started with its launcher."
                   if result["reason"] == "no-launcher"
                   else "She is already restarting.")
        return jsonify({"message": message, "reason": result["reason"]}), 409
    print("[Flask] Restart requested from Settings.")
    return jsonify({"ok": True, "restart": result["status"]})


# pre: Amadeus was started by a launcher that can shut down (2026-11-06)
# post: a shutdown request is left for the launcher, which stops her brain,
#       voice, memory and web page within a second and closes its window;
#       200. No such launcher -> 409.
@application.route("/system/shutdown", methods=["POST"])
def system_shutdown_route():
    try:
        result = restart_control.request_shutdown()
    except OSError as exc:
        print("[Flask] Shutdown request failed:", repr(exc))
        return jsonify({"message": "Could not ask for a shutdown."}), 500
    if not result["ok"]:
        return jsonify({"message": "Shut down works only when Amadeus was started "
                                   "with its launcher (start it once more after this update).",
                        "reason": result["reason"]}), 409
    print("[Flask] Shutdown requested from Settings.")
    return jsonify({"ok": True})


# Remote access (remote_access.py, settings tab "Mobile and remote access"):
#   GET  /remote/access   this PC's Tailscale address and whether Funnel is on
#   POST /remote/setup    {"action": "on" | "off"} runs the two fixed `tailscale
#                         serve` commands - only from this PC itself: through
#                         `tailscale serve` a phone's request also arrives from
#                         127.0.0.1, but with the forwarding headers added.
_FORWARDED_HEADERS = ("X-Forwarded-For", "Tailscale-User-Login", "Forwarded")


def _from_this_pc() -> bool:
    return (request.remote_addr in ("127.0.0.1", "::1")
            and not any(request.headers.get(h) for h in _FORWARDED_HEADERS))


@application.route("/remote/access", methods=["GET"])
def get_remote_access():
    response = jsonify(remote_access.access())
    response.headers["Cache-Control"] = "no-store"
    return response


@application.route("/remote/setup", methods=["POST"])
def remote_setup_route():
    if not _from_this_pc():
        return jsonify({"message": "Remote access can only be changed on the PC itself."}), 403
    action = (request.get_json(silent=True) or {}).get("action")
    if action not in ("on", "off"):
        return jsonify({"message": 'action must be "on" or "off"'}), 400
    result = remote_access.turn_on() if action == "on" else remote_access.turn_off()
    print(f"[Flask] Remote access {action}: {'ok' if result['ok'] else result['reason']}")
    return jsonify(result)

_speech_requests: dict[str, tuple[float, str, int | None]] = {}
_speech_requests_lock = threading.Lock()
# One wake-up at a time (see _serve_wake_up): generation takes seconds, and
# a settings-save re-fire landing mid-generation would otherwise start a
# second wake-up and double her first line.
_wake_gate = threading.Lock()
_reaction_audio_dir = Path(__file__).resolve().parent / "assets" / "reaction_audio"


def _unlink_voice(assistant_id) -> None:
    """Best-effort removal of a message's saved voice file."""
    try:
        path = Path(__file__).resolve().parent / "generated" / f"voice_{int(assistant_id)}.wav"
        if path.exists():
            path.unlink()
    except (OSError, ValueError, TypeError):
        pass


def _model_not_found_message() -> str:
    """Plain-English 'the model name is wrong' message for a 404 from the
    model endpoint. The raw exception names (NotFound / OpenAIModelNotFound)
    are jargon to a user; the actionable fact is the model name in Settings -
    retrying never fixes it. A batch-only model id ("...:batch") gets a
    dedicated hint: OpenRouter lists those names and a test-connection can
    even find them, yet chat can't use them (results arrive up to 24 h
    later). This is the 2026-10-07 fresh-VM incident: an evening chasing a
    cryptic 404 that was just a ":batch" suffix in the model name."""
    try:
        current = getLLMModel().strip()
    except Exception:
        current = ""
    if current.endswith(":batch"):
        return (
            f'The server has no usable chat model named "{current}": names ending '
            'in ":batch" are batch-only (OpenRouter serves them only through its '
            'batch service, with results up to 24 h later). Pick the same model '
            'WITHOUT the ":batch" suffix in Settings.'
        )
    return (
        "The server says it has no model by the name configured in Settings"
        + (f' ("{current}")' if current else "")
        + " - check or correct the model name there. Retrying will not fix it."
    )


def _model_server_address() -> str:
    """The model server Amadeus is using: the built-in one when that mode is
    on, else the saved address (or the auto-detected local port)."""
    if builtin_llm.active():
        return builtin_llm.server_url()
    return memory.load_llm_server() or llm._server_url()


def _llm_error_message(exc: Exception) -> str:
    """Translate a model-server failure into a plain-English message."""
    try:
        import openai
        if isinstance(exc, openai.NotFoundError):
            return _model_not_found_message()
        if isinstance(exc, openai.APIConnectionError):
            if builtin_llm.active():
                if builtin_llm.model_missing():
                    return ("Her built-in model is not installed yet. Run the installer "
                            "again - it continues the download where it stopped.")
                state = builtin_server.status()["state"]
                if state == "unloaded":
                    return ("Her built-in model is not loaded. Load it in Settings -> "
                            "Connection.")
                if state == "failed":
                    return ("Her built-in model could not load - see Settings -> "
                            "Connection for why.")
                return ("Can't reach her built-in model. It may still be loading - "
                        "Settings -> Connection shows how it is doing.")
            return f"Can't reach the model server at {llm._server_url()}. Is it running?"
        if isinstance(exc, openai.APITimeoutError):
            return "The model server took too long to respond. Is it busy or overloaded?"
        if isinstance(exc, openai.AuthenticationError):
            return "The model server rejected the API key. Check it in Settings."
        if isinstance(exc, openai.APIStatusError) and exc.status_code == 402:
            return ("Her cloud service says the account is out of credit. Add credit on "
                    "its website (OpenRouter: Settings -> Credits), then send again.")
    except Exception:
        pass
    try:
        # langchain re-raises an OpenAI 404 as its own exception type (it is
        # a subclass of openai.NotFoundError, so the check above usually
        # already caught it; this covers versions where it is not).
        try:
            from langchain_openai import OpenAIModelNotFoundError
        except ImportError:  # older/newer versions nest it
            from langchain_openai.chat_models.base import OpenAIModelNotFoundError
        if isinstance(exc, OpenAIModelNotFoundError):
            return _model_not_found_message()
    except Exception:
        pass
    return f"The model did not return a usable reply ({type(exc).__name__}). Please try again."

# pre:
# - JSON body contains an "key" field
#
# post:
# - updates the active API key if provided
# - returns status indicating success or error
from typing import Dict

@application.route("/set_key", methods=["POST"])
def set_api_key():
    print("[Flask] /set_key route triggered")  # ← add this
    data = request.get_json(silent=True)
    key = data.get("key") if isinstance(data, dict) else None
    if isinstance(key, str) and key.strip():
        try:
            setKey(key.strip())
        except OSError:
            return jsonify({"message": "Could not save API key"}), 500
        return jsonify({"status": "ok", "message": "API key received"})
    else:
        return jsonify({"status": "error", "message": "No key received"}), 400


@application.route("/api_key_status", methods=["GET"])
def api_key_status():
    response = jsonify({"configured": has_api_key()})
    response.headers["Cache-Control"] = "no-store"
    return response

def _register_speech(speech_id, jps_text, assistant_id) -> None:
    """Give the browser a single-use speech id. The browser then opens the
    streaming WAV endpoint, so the same sound that reaches the speakers also
    drives the Live2D analyser/lip sync. Entries expire after 5 minutes and
    the table never grows past 20 pending speeches."""
    with _speech_requests_lock:
        now = time.monotonic()
        for expired in [key for key, (created, _, _) in _speech_requests.items() if now - created > 300]:
            _speech_requests.pop(expired, None)
        _speech_requests[speech_id] = (now, jps_text, assistant_id)
        while len(_speech_requests) > 20:
            _speech_requests.pop(next(iter(_speech_requests)))
    voice_gate.expect(speech_id)


# pre:
# - JSON body contains "user_input" as a string
#
# post (web access ON):
# - generates an assistant response (the search loop's single final call
#   writes both lines) and voice output
# - returns the original single JSON: English UI text + speech id
#
# post (web access OFF, voice-first):
# - call 1 (the Japanese line) is complete before this route returns, so her
#   voice can start at once; call 2 (the English display line) runs in the
#   background while she is talking
# - the response is a two-event stream (text/event-stream):
#     data: {"phase": "voice", "speech_id", "user_id", "assistant_id", "conversation_id"}
#     data: {"phase": "text", "assistant_id", "response": "<English line>"}
#   the second event is bounded (120 s); on timeout the Japanese line itself
#   is the response, so the UI never shows an empty box
# --- The web-ON reply as a live stream (2026-10-25) -----------------------------
# A web-ON reply can take several seconds (search, reading the pages), so it
# streams what is really happening: status steps (searching / found / reading
# / picking out what matters / thinking - only when the step happens), her
# "hold on, let me check" line the moment it is ready, then the reply (the
# same fields the old single JSON had), or an error. text/event-stream:
#     data: {"phase": "status", "step", "text"}
#     data: {"phase": "aside", "assistant_id", "response", "japanese", "speech_id"}
#     data: {"phase": "reply", ...}            (last)
#     data: {"phase": "error", "message"}      (last)
WEB_STREAM_KEEPALIVE = 15.0   # seconds between keep-alive comments


def _sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def _web_stream(work, finish, with_aside: bool):
    """Run `work()` (one reply, in a worker thread) and stream its progress;
    `finish(result)` builds the final "reply" event."""
    events = queue.Queue()

    def on_aside(assistant_id, pack):
        speech_id = uuid.uuid4().hex
        _register_speech(speech_id, pack.assistant_reply_JPS, assistant_id)
        events.put(("aside", {"phase": "aside", "assistant_id": assistant_id,
                              "response": pack.assistant_reply_ENG,
                              "japanese": pack.assistant_reply_JPS,
                              "speech_id": speech_id}))

    def runner():
        chat._turn.progress = lambda step, text: events.put(
            ("status", {"phase": "status", "step": step, "text": text}))
        chat._turn.aside_emit = on_aside if with_aside else None
        try:
            result = work()
        except Exception as exc:          # noqa: BLE001 - reported to the screen
            events.put(("error", exc))
        else:
            events.put(("done", result))
        finally:
            chat._turn.progress = None
            chat._turn.aside_emit = None

    threading.Thread(target=runner, daemon=True).start()

    def generate():
        while True:
            try:
                kind, payload = events.get(timeout=WEB_STREAM_KEEPALIVE)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if kind in ("status", "aside"):
                yield _sse(payload)
            elif kind == "error":
                print("[Flask] LLM failure (web stream):", repr(payload))
                yield _sse({"phase": "error", "message": _llm_error_message(payload)})
                return
            else:
                yield _sse(finish(payload))
                return

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-store"
    return response


# --- Pictures the user attaches (attachments Phase 1, 2026-10-26) --------------
# POST /attachments uploads on attach (one "card" per file, not sent yet);
# the send carries their ids ("attachment_ids") on POST /. See attachments.py.
def _check_attachments_for_send(ids, user_input):
    """A reason this send can't go (plain words), or None."""
    if not isinstance(ids, list) or not all(type(i) is int for i in ids):
        return "Bad attachment list."
    waiting = attachments.pending(ids)
    if ids and not waiting:
        return "Those pictures are no longer available - attach them again."
    for r in waiting:
        if r["kind"] != "video":
            continue
        # The screen sends only ready videos; another tab may be quicker.
        state = video.load_meta(r).get("state")
        if state == video.FAILED:
            return "This video could not be prepared - take it off and send the rest."
        if state != video.READY:
            return "This video is still being prepared - send it once its card says it is ready."
    if not waiting and not (user_input or "").strip():
        return "Nothing to send."
    ok, limit = attachments.fits(ids)
    if not ok:
        if any(r["kind"] == "video" for r in waiting):
            return ("There is no room for this video with what is on her desk. Take something "
                    "off her desk first, or send it on its own.")
        if any(r["kind"] == "document" for r in waiting):
            return ("There is no room for this with what is on her desk. Take something off "
                    "her desk first, or send fewer files.")
        return (f"That is more pictures than her current model can hold at once (about "
                f"{limit}, counting the ones on her desk). Take some off her desk or send fewer.")
    return None


@application.route("/attachments", methods=["POST"])
def upload_attachments():
    files = request.files.getlist("file") or request.files.getlist("files")
    if not files:
        return jsonify({"message": "No file received"}), 400
    conv = request.form.get("conversation_id", type=int)
    items = []
    for f in files:
        name = f.filename or "picture"
        data = f.read(attachments.MAX_FILE_BYTES + 1)
        try:
            row = attachments.save_upload(name, data, conv)
            # A document too long to read in full: her reading notes start now
            # (Phase 4b), the card shows the progress.
            if attachments.maybe_start_notes(row, conv):
                row = attachments.get(row["id"])
            items.append({"ok": True, **attachments.card(row, conv)})
        except attachments.AttachmentError as exc:
            items.append({"ok": False, "name": attachments._clean_name(name),
                          "error": exc.code, "message": str(exc)})
        except Exception as exc:
            print("[Flask] Attachment upload failed:", repr(exc))
            items.append({"ok": False, "name": attachments._clean_name(name),
                          "error": "failed", "message": "This file could not be saved."})
    return jsonify({"status": "ok", "items": items,
                    "room": attachments.room_left(conv)})


# --- Videos (video plan Phase 1, 2026-11-03) -----------------------------------
# Uploaded in pieces so a big phone video survives a dropped connection:
# POST /attachments/video {"name", "size", "conversation_id"} -> upload id;
# PUT /attachments/video/<id>?offset=N (raw bytes, at most "piece");
# GET /attachments/video/<id> -> how much arrived (to go on after a drop);
# POST /attachments/video/<id>/done -> the card (preparing starts);
# DELETE /attachments/video/<id> -> cancel. See video.py.
def _video_error(exc, name=""):
    return jsonify({"ok": False, "name": name, "error": exc.code, "message": str(exc)}), 400


@application.route("/attachments/video", methods=["POST"])
def video_upload_start():
    data = request.get_json(silent=True) or {}
    name = data.get("name") or "video.mp4"
    conv = data.get("conversation_id")
    try:
        return jsonify({"ok": True, **video.start_upload(
            name, data.get("size"), int(conv) if isinstance(conv, int) else None)})
    except attachments.AttachmentError as exc:
        return _video_error(exc, attachments._clean_name(name))


@application.route("/attachments/video/<upload_id>", methods=["GET"])
def video_upload_status(upload_id):
    try:
        return jsonify({"ok": True, **video.upload_status(upload_id)})
    except attachments.AttachmentError as exc:
        return _video_error(exc)


@application.route("/attachments/video/<upload_id>", methods=["PUT"])
def video_upload_piece(upload_id):
    offset = request.args.get("offset", type=int)
    if request.content_length is not None and request.content_length > video.PIECE_MAX:
        return jsonify({"ok": False, "error": "piece_too_big",
                        "message": "This piece is too big."}), 413
    data = request.stream.read(video.PIECE_MAX + 1)
    if len(data) > video.PIECE_MAX:
        return jsonify({"ok": False, "error": "piece_too_big",
                        "message": "This piece is too big."}), 413
    try:
        out = video.add_piece(upload_id, offset, data)
    except attachments.AttachmentError as exc:
        return _video_error(exc)
    return jsonify(out), (200 if out["ok"] else 409)


@application.route("/attachments/video/<upload_id>/done", methods=["POST"])
def video_upload_done(upload_id):
    try:
        row = video.finish_upload(upload_id)
    except attachments.AttachmentError as exc:
        return _video_error(exc)
    except Exception as exc:
        print("[Flask] Video upload failed:", repr(exc))
        video.cancel_upload(upload_id)
        return jsonify({"ok": False, "error": "failed",
                        "message": "This video could not be saved."}), 500
    conv = row.get("conversation_id")
    return jsonify({"ok": True, **attachments.card(row, conv), "room": attachments.room_left(conv)})


@application.route("/attachments/video/<upload_id>", methods=["DELETE"])
def video_upload_cancel(upload_id):
    try:
        video.cancel_upload(upload_id)
    except attachments.AttachmentError as exc:
        return _video_error(exc)
    return jsonify({"ok": True})


# The speech model for videos (V2 a: a one-time download the user agrees to).
@application.route("/attachments/speech-model", methods=["GET"])
def speech_model_status():
    return jsonify({"status": "ok", **transcribe.status()})


@application.route("/attachments/speech-model", methods=["POST"])
def speech_model_download():
    return jsonify({"status": "ok", **transcribe.start_download()})


@application.route("/attachments/<int:attachment_id>", methods=["DELETE"])
def remove_attachment(attachment_id):
    row = attachments.get(attachment_id)
    result = attachments.remove(attachment_id)
    if not result:
        return jsonify({"message": "Attachment not found"}), 404
    out = {"status": "ok", "result": result}
    # ?react=1: a card taken back after her reaction to it - she notices it
    # in her own words (MH 2026-10-27). Never blocks the removal itself.
    if result == "deleted" and request.args.get("react") == "1" and has_api_key():
        try:
            line = chat.generate_withdraw_reaction(row, row.get("conversation_id"))
        except GreetingSuperseded:
            line = None
        except Exception as exc:
            print("[Flask] Withdraw reaction failed:", repr(exc))
            line = None
        if line:
            pack, assistant_id, conv_id = line
            speech_id = uuid.uuid4().hex
            _register_speech(speech_id, pack.assistant_reply_JPS, assistant_id)
            out["reaction"] = {"response": pack.assistant_reply_ENG,
                               "japanese": pack.assistant_reply_JPS, "speech_id": speech_id,
                               "assistant_id": assistant_id, "conversation_id": conv_id}
    return jsonify(out)


def _send_attachment_file(attachment_id, thumb, original=False):
    full = attachments.file_path(attachment_id, thumb=thumb, original=original)
    if not full:
        return jsonify({"message": "Attachment not found"}), 404
    row = attachments.get(attachment_id)
    if row and row["kind"] == "document":
        # A document's own file never runs in the browser (an .html one could):
        # a PDF opens in its viewer, the text as plain text, anything else is
        # downloaded under its own name.
        if not original:
            response = send_from_directory(str(Path(full).parent), Path(full).name,
                                           mimetype="text/plain; charset=utf-8")
        elif row.get("mime") == "application/pdf":
            response = send_from_directory(str(Path(full).parent), Path(full).name,
                                           mimetype="application/pdf",
                                           download_name=row.get("name") or "document.pdf")
        else:
            response = send_from_directory(str(Path(full).parent), Path(full).name,
                                           as_attachment=True,
                                           download_name=row.get("name") or Path(full).name)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, max-age=3600"
        return response
    response = send_from_directory(str(Path(full).parent), Path(full).name, conditional=True)
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


# A document as she read it (E4 a: the screen's text view + its original).
@application.route("/attachments/<int:attachment_id>/text", methods=["GET"])
def attachment_text(attachment_id):
    row = attachments.get(attachment_id)
    if not row or row["kind"] != "document":
        return jsonify({"message": "Document not found"}), 404
    item = memory.attachments_for_ui([row])[0]
    notes = attachments.load_notes(row) if row.get("notes") else {}
    # With her notes done she can look up any page: the whole text is shown.
    text = (attachments.document_text(row) if row.get("notes") == memory.NOTES_DONE
            else attachments.read_part(row))
    return jsonify({"status": "ok", **item, "text": text,
                    "notes": attachments.notes_progress(row),
                    "notes_text": attachments._notes_lines(notes.get("sections") or [])})


# A video for the player (video plan Phase 2): its state and what was said.
@application.route("/attachments/<int:attachment_id>/watch", methods=["GET"])
def attachment_watch(attachment_id):
    row = attachments.get(attachment_id)
    if not row or row["kind"] != "video":
        return jsonify({"message": "Video not found"}), 404
    meta = video.load_meta(row)
    speech = meta.get("speech") or {}
    return jsonify({"status": "ok", **attachments.card(row), "speech_state": speech.get("state") or "none",
                    "speech_text": video.speech_text(speech.get("segments") or []),
                    "language": speech.get("language") or "",
                    "notes": video.notes_progress(row),
                    "notes_text": video.notes_lines(video.load_watch_notes(row))})


@application.route("/attachments/<int:attachment_id>/file", methods=["GET"])
def attachment_file(attachment_id):
    return _send_attachment_file(attachment_id, thumb=False)


@application.route("/attachments/<int:attachment_id>/thumb", methods=["GET"])
def attachment_thumb(attachment_id):
    return _send_attachment_file(attachment_id, thumb=True)


# The full-size picture (the viewer); the copy she sees when it was small already.
@application.route("/attachments/<int:attachment_id>/original", methods=["GET"])
def attachment_original(attachment_id):
    return _send_attachment_file(attachment_id, thumb=False, original=True)


# Cards not sent yet, worked out again (a document's "she will read the first
# N pages" / "no room" warning changes when her desk or the tray changes).
@application.route("/attachments/cards", methods=["GET"])
def attachment_cards():
    conv = request.args.get("conversation_id", type=int)
    ids = [int(i) for i in (request.args.get("ids") or "").split(",") if i.strip().isdigit()]
    return jsonify({"status": "ok",
                    "items": [attachments.card(r, conv) for r in attachments.pending(ids)]})


@application.route("/attachments/desk", methods=["GET"])
def attachments_desk():
    conv = request.args.get("conversation_id", type=int)
    return jsonify({"status": "ok", "items": memory.attachments_for_ui(attachments.desk(conv))})


@application.route("/attachments/capabilities", methods=["GET"])
def attachments_capabilities():
    response = jsonify(attachments.capabilities(request.args.get("conversation_id", type=int)))
    response.headers["Cache-Control"] = "no-store"
    return response


# Her one line when something is attached (D8): {"attachment_ids": [...]};
# answers {"status": "skipped"} when the reaction setting says quiet, 409
# when the user sent first (the line is dropped).
@application.route("/attachments/react", methods=["POST"])
def attachments_react():
    if not has_api_key():
        return jsonify({"message": "No API key. Add one in Settings."}), 400
    data = request.get_json(silent=True) or {}
    ids = data.get("attachment_ids")
    if not isinstance(ids, list) or not ids:
        return jsonify({"message": "Missing 'attachment_ids'"}), 400
    conv = data.get("conversation_id") if type(data.get("conversation_id")) is int else None
    try:
        result = chat.generate_attach_reaction(ids, conv)
    except GreetingSuperseded:
        return jsonify({"status": "superseded"}), 409
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    except Exception as exc:
        print("[Flask] Attach reaction failed:", repr(exc))
        return jsonify({"message": _llm_error_message(exc)}), 502
    if result is None:
        return jsonify({"status": "skipped"})
    pack, assistant_id, conv_id = result
    speech_id = uuid.uuid4().hex
    _register_speech(speech_id, pack.assistant_reply_JPS, assistant_id)
    return jsonify({"status": "ok", "response": pack.assistant_reply_ENG,
                    "japanese": pack.assistant_reply_JPS, "speech_id": speech_id,
                    "assistant_id": assistant_id, "conversation_id": conv_id})


@application.route("/attachments/reactionSetting", methods=["GET"])
def get_attach_reaction_setting():
    return jsonify({"status": "ok", "value": attachments.load_reaction_setting(),
                    "choices": list(attachments.REACT_CHOICES)})


@application.route("/attachments/reactionSetting", methods=["POST"])
def set_attach_reaction_setting():
    data = request.get_json(silent=True) or {}
    try:
        value = attachments.save_reaction_setting(data.get("value"))
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    return jsonify({"status": "ok", "value": value})


@application.route("/", methods=["POST"])
def request_message():
    if not has_api_key():
        return jsonify({"message": "No API key. Add one in Settings."}), 400
    print("[Flask] / route triggered")
    content = request.get_json()
    user_input = content.get("user_input", "")
    # Pictures attached to this message (ids from POST /attachments), if any.
    attachment_ids = content.get("attachment_ids") or None
    if attachment_ids is not None:
        problem = _check_attachments_for_send(attachment_ids, user_input)
        if problem:
            return jsonify({"message": problem}), 400
    started = timing.now()
    timing.start_reports()

    if chat.store.load_web_access():
        # Web access ON: the reply is the original single call (both lines at
        # once), streamed with its live progress (see _web_stream).
        def finish(result):
            pack, user_id, assistant_id, conv_id = result
            timing.line_ready(assistant_id, started, "her reply (web access on, both lines)")
            print("\n[Flask]: ENG:", pack.assistant_reply_ENG)
            print("[Flask]: JPS:", pack.assistant_reply_JPS)
            speech_id = uuid.uuid4().hex
            _register_speech(speech_id, pack.assistant_reply_JPS, assistant_id)
            return {
                "phase": "reply",
                "response": pack.assistant_reply_ENG,
                "japanese": pack.assistant_reply_JPS,
                "speech_id": speech_id,
                "user_id": user_id,
                "assistant_id": assistant_id,
                "conversation_id": conv_id,
                # The links she used (shown under her reply); [] when none.
                "sources": memory.sources_for_ui(memory.load_message_sources(assistant_id)),
                # What she read of a long document (Phase 4b); "" when none.
                "doc_reads": memory.load_message_doc_reads(assistant_id),
            }
        return _web_stream(lambda: (getOutputPacked(user_input, attachment_ids) if attachment_ids
                                    else getOutputPacked(user_input)), finish, with_aside=True)

    # Web access OFF: voice-first (see the doc block above).
    try:
        turn = (chat.getOutputPackedVoiceFirst(user_input, attachment_ids) if attachment_ids
                else chat.getOutputPackedVoiceFirst(user_input))
    except Exception as exc:
        # Call 1 failed before the stream could start: the route has not
        # switched to streaming yet, so the original JSON error protocol
        # still applies.
        print("[Flask] LLM failure (voice-first call 1):", repr(exc))
        return jsonify({"message": _llm_error_message(exc)}), 502
    timing.line_ready(turn.assistant_id, started)
    print("\n[Flask]: JPS (voice-first call 1):", turn.pack.assistant_reply_JPS)

    speech_id = uuid.uuid4().hex
    _register_speech(speech_id, turn.pack.assistant_reply_JPS, turn.assistant_id)
    # The page says whether it starts her voice before the English line
    # ("Voice playback: Early / Right away"); then, with her voice on the
    # processor, the English line waits until she has finished speaking.
    voice_ready = getattr(turn, "voice_ready", None)
    if voice_ready is not None:
        voice_ready(speech_id, hold_english=bool(content.get("voice_early")))

    def generate():
        yield "data: " + json.dumps(
            {"phase": "voice", "speech_id": speech_id, "user_id": turn.user_id,
             "assistant_id": turn.assistant_id, "conversation_id": turn.conv_id,
             "japanese": turn.pack.assistant_reply_JPS,
             "doc_reads": memory.load_message_doc_reads(turn.assistant_id)},
            ensure_ascii=False) + "\n\n"
        # Wait (bounded) for the background English backfill; her voice is
        # already playing by the time this line runs. Longer than the voice
        # gate's cap (90 s): in "early" mode on a processor voice the English
        # line is written only after she finished speaking.
        english = turn.wait_en(timeout=180.0)
        yield "data: " + json.dumps(
            {"phase": "text", "assistant_id": turn.assistant_id, "response": english},
            ensure_ascii=False) + "\n\n"

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-store"
    return response

@application.route("/speech/<speech_id>", methods=["GET"])
def speech(speech_id):
    if request.method == "HEAD":
        return "", 405
    with _speech_requests_lock:
        item = _speech_requests.pop(speech_id, None)

    if item is None or time.monotonic() - item[0] > 300:
        return jsonify({"message": "Speech request not found"}), 404

    _, jps_text, assistant_id = item
    save_path = None
    if assistant_id:
        save_path = Path(__file__).resolve().parent / "generated" / f"voice_{assistant_id}.wav"
    voice_gate.started(speech_id)
    watch = timing.VoiceWatch(assistant_id)
    chunks = streamVoiceChunks(jps_text, save_path=save_path)
    try:
        first = next(chunks)
    except Exception:
        chunks.close()
        watch.finish()
        voice_gate.finished(speech_id)
        return jsonify({"message": "Speech generation failed"}), 502

    def generate():
        try:
            for chunk in chain((first,), chunks):
                watch.feed(chunk)
                yield chunk
        finally:
            chunks.close()
            watch.finish()
            voice_gate.finished(speech_id)
            # Keep the line for replay only if it was actually generated.
            if assistant_id and save_path is not None and save_path.exists():
                memory.set_message_audio(assistant_id, f"generated/voice_{assistant_id}.wav")
                try:
                    # Enforce the "keep last N recordings" cap from Settings.
                    memory.prune_voice_files()
                except Exception:
                    pass

    response = Response(generate(), mimetype="audio/wav")
    response.headers["Cache-Control"] = "no-store"
    # How long her line is (characters): the page estimates the clip's
    # length from it (smooth start + the "preparing her voice" bar).
    response.headers["X-Amadeus-Voice-Chars"] = str(len((jps_text or "").strip()))
    response.headers["Access-Control-Expose-Headers"] = "X-Amadeus-Voice-Chars"
    def closed():
        chunks.close()
        voice_gate.finished(speech_id)
    response.call_on_close(closed)
    return response


@application.route("/reaction_audio/<path:filename>", methods=["GET"])
def reaction_audio(filename):
    # Serve prerecorded interaction lines from backend/assets/reaction_audio.
    response = send_from_directory(_reaction_audio_dir, filename, conditional=True)
    response.headers["Cache-Control"] = "no-store"
    return response


# pre
# post:
# - clears all stored conversation memory
# - returns confirmation status
@application.route("/memory_reset", methods=["POST"])
def memory_reset():
    print("[Flask] /memory_reset triggered")  
    # Without a body this clears the active session (legacy callers still work).
    data = request.get_json(silent=True)
    conv_id = None
    if isinstance(data, dict) and type(data.get("conversation_id")) is int:
        conv_id = data["conversation_id"]
    resetMemory(conv_id)
    return jsonify({"status": "ok", "message": "Memory reset"})


# pre:
# - JSON body contains "model" option as string
#
# post:
# - updates the active LLM model if provided
# - returns success or error status
@application.route("/setLLMModel", methods=["POST"])
def settingLLMModel():
    print("[Flask] /setLLMModel triggered")  
    data = request.get_json() or {}
    new_model = data.get("model", "").strip()
    if new_model:
        setLLMModel(new_model)
        return jsonify({"status": "ok", "message": "new model recieved!"})
    else:
        return jsonify({"status": "error", "message": "No model recieved"}), 400

# pre
# post:
# - returns the currently active LLM model name as a string
@application.route("/getCurrLLMModel", methods=["GET"])
def getCurrLLMModel():
    print("[Flask] /getCurrLLMModel triggered")
    # Settings' model field is the user's OWN (custom) model. In built-in
    # mode getLLMModel() names the built-in one, and saving that back would
    # overwrite the user's custom choice - so the field gets the saved one.
    if builtin_llm.active():
        LLM_Model = (chat.LLM_Model or "").strip() or "No Model Selected."
    else:
        LLM_Model = getLLMModel()
    if LLM_Model:
        return jsonify({"status": "ok", "message": LLM_Model})
    else:
        return jsonify({"status": "error", "message": "No Model Selected"}), 400

# pre
# post:
# - returns all stored conversation messages in list of Jsons
@application.route("/getMemory", methods=["POST"])
def getMemory():
    print("[Flask] /getMemory triggered")
    msgs = get_raw_memory()
    # The session this batch belongs to, so the UI can always verify the
    # pane it is showing matches the backend's active conversation.
    return jsonify({"status":"ok","messages": msgs,
                    "conversation_id": memory.load_active_conversation()})


@application.route("/getPersonality", methods=["GET"])
def get_personality():
    try:
        personality = getPersonality()
    except OSError:
        return jsonify({"message": "Could not load personality"}), 500
    response = jsonify({"status": "ok", "personality": personality})
    response.headers["Cache-Control"] = "no-store"
    return response


# pre: 
# - JSON body containing new context for personality as string.
#
# post:
# - overwrite the current personality.txt
# - return sucess or error status
@application.route("/setPersonality", methods=["POST"])
def settingPersonality():
    print("[Flask] /setPersonality triggered")  
    data = request.get_json(silent=True)
    new_personality = data.get("personality") if isinstance(data, dict) else None
    if not isinstance(new_personality, str) or not new_personality.strip():
        return jsonify({"status": "error", "message": "Enter a personality before saving."}), 400
    new_personality = new_personality.strip()
    try:
        setPersonality(new_personality)
    except OSError:
        return jsonify({"message": "Could not save personality. Please try again."}), 500
    return jsonify({"status": "ok", "message": "Personality updated", "personality": new_personality})


# Newer defaults for files the user changed (defaults_refresh): Settings shows
# a one-time note per file; untouched old defaults were already replaced at
# startup.
@application.route("/defaults/notices", methods=["GET"])
def default_notices():
    response = jsonify({"status": "ok", "notices": defaults_refresh.notices()})
    response.headers["Cache-Control"] = "no-store"
    return response


@application.route("/defaults/answer", methods=["POST"])
def default_answer():
    data = request.get_json(silent=True) or {}
    name, choice = data.get("name"), data.get("choice")
    if name not in defaults_refresh.OLD_DEFAULTS or choice not in ("use_new", "keep_mine"):
        return jsonify({"status": "error", "message": "Unknown choice."}), 400
    try:
        if choice == "use_new":
            backup = defaults_refresh.use_new_default(name)
            return jsonify({"status": "ok", "backup": backup})
        defaults_refresh.keep_mine(name)
    except (OSError, ValueError):
        return jsonify({"status": "error", "message": "Could not update the file. Please try again."}), 500
    return jsonify({"status": "ok"})


# pre: Interaction number is given. e.g., 1,2,3
# post: use SpecialInteraction() from chat to update accordingly
@application.route("/doSpecialInteraction", methods=["POST"])
def doSpecialInteraction():
    data = request.get_json(silent=True)
    interaction_value = data.get("interaction_value") if isinstance(data, dict) else None
    if type(interaction_value) is not int:
        return jsonify({"message": "Interaction must be an integer"}), 400
    try:
        reply = SpecialInteraction(interaction_value)
    except ValueError:
        return jsonify({"message": "Unknown interaction"}), 400
    try:
        apply_interaction_trust(interaction_value)
    except Exception:
        pass

    # chat.py stores reaction recordings relative to backend/. Convert those
    # internal paths into a browser-facing Flask endpoint.
    audio_url = reply.get("audio_url")
    prefix = "assets/reaction_audio/"
    if isinstance(audio_url, str) and audio_url.startswith(prefix):
        reply = {
            **reply,
            "audio_url": "/reaction_audio/" + audio_url[len(prefix):],
        }

    return jsonify({"status": "ok", **reply,
                    "conversation_id": memory.load_active_conversation()})


@application.route("/getWebAccess", methods=["GET"])
def get_web_access():
    """Current state of the web-access toggle."""
    return jsonify({"enabled": memory.load_web_access()})


@application.route("/webReading", methods=["GET"])
def get_web_reading():
    """How she reads pages after a search: the saved choice and what is used
    right now ("quick" on a model server that runs one task at a time)."""
    effective, reason = chat.web_reading_plan()[0], chat.web_reading_reason()
    return jsonify({"mode": memory.load_web_reading(), "effective": effective,
                    "reason": reason})


@application.route("/webReading", methods=["POST"])
def set_web_reading():
    data = request.get_json(silent=True)
    try:
        mode = memory.save_web_reading((data or {}).get("mode") if isinstance(data, dict) else None)
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    return jsonify({"status": "ok", "mode": mode})


@application.route("/setWebAccess", methods=["POST"])
def set_web_access():
    """Turn the web-access toggle on or off (persisted)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "enabled" not in data:
        return jsonify({"message": "Missing 'enabled' field"}), 400
    enabled = bool(data["enabled"])
    memory.save_web_access(enabled)
    return jsonify({"status": "ok", "enabled": enabled})


@application.route("/reasoning", methods=["GET"])
def get_reasoning():
    """Settings -> Reasoning: the three levels (defaults applied), which brain
    is active, and whether the custom model is a cloud service."""
    return jsonify({**llm.reasoning_levels(),
                    "mode": "builtin" if builtin_llm.active() else "custom",
                    "custom_is_cloud": not llm._is_local_host(llm._configured_server_url()),
                    "levels": list(llm.REASONING_LEVELS)})


@application.route("/reasoning", methods=["POST"])
def set_reasoning():
    """Save one or more levels: {"replies_builtin": "off", ...}."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"message": "Expected a JSON object"}), 400
    bad = [k for k, v in data.items()
           if k not in memory.REASONING_KEYS or v not in memory.REASONING_LEVELS]
    if bad or not data:
        return jsonify({"message": "Unknown setting or level: " + ", ".join(map(str, bad or ["(none)"]))}), 400
    memory.save_reasoning(data)
    return get_reasoning()


# The old Deep thinking switch (replaced 2026-10-20) - kept one release for
# older pages: "on" = her replies above Off for the active brain.
@application.route("/getDeepThinking", methods=["GET"])
def get_deep_thinking():
    return jsonify({"enabled": llm.reasoning_for("replies") != "off"})


@application.route("/setDeepThinking", methods=["POST"])
def set_deep_thinking():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "enabled" not in data:
        return jsonify({"message": "Missing 'enabled' field"}), 400
    enabled = bool(data["enabled"])
    key = "replies_builtin" if builtin_llm.active() else "replies_custom"
    memory.save_reasoning({key: "medium" if enabled else "off"})
    return jsonify({"status": "ok", "enabled": enabled})


# pre: message_id exists; JSON body contains "content"
# post: the stored message text is replaced (this changes what she remembers).
#       Her context reads the JAPANESE line of her replies, so editing one of
#       HER replies also rewrites that line from the edited English (one short
#       model call) and drops the old voice file; if that call fails the edit
#       is refused, so the two lines never disagree.
@application.route("/memory/<int:message_id>", methods=["PATCH"])
def edit_message(message_id):
    data = request.get_json(silent=True)
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, str) or not content.strip():
        return jsonify({"message": "No content received"}), 400
    content = content.strip()
    reply_ctx = memory.get_reply_edit_context(message_id)
    if reply_ctx is None:
        if not memory.update_message_content(message_id, content):
            return jsonify({"message": "Message not found"}), 404
        return jsonify({"status": "ok"})
    try:
        japanese = chat.voice_line_for_edit(content, reply_ctx["prev_user"])
    except Exception as exc:
        print("[Flask] Edit: Japanese line rewrite failed:", repr(exc))
        return jsonify({"message": "Edit not saved - her Japanese line could not be "
                                   "rewritten. " + _llm_error_message(exc)}), 502
    if not memory.update_message_content(message_id, content, japanese=japanese):
        return jsonify({"message": "Message not found"}), 404
    _unlink_voice(message_id)
    return jsonify({"status": "ok", "japanese": japanese})


# pre: message_id exists
# post: the message row is removed from her memory
@application.route("/memory/<int:message_id>", methods=["DELETE"])
def delete_message(message_id):
    titled = attachments.picture_titled_chat(message_id)   # (before the row is gone)
    if not memory.delete_message(message_id):
        return jsonify({"message": "Message not found"}), 404
    _unlink_voice(message_id)
    if titled is not None:
        attachments.untitle_chat(titled)   # its title came from the deleted picture
    return jsonify({"status": "ok"})


# pre: at least one assistant reply exists in memory
# post: her newest reply is removed so the user can rephrase and retry
@application.route("/memory/undo", methods=["POST"])
def undo_last_reply():
    result = memory.undo_last_assistant()
    if result is None:
        return jsonify({"message": "No reply to undo"}), 404
    if result["action"] == "deleted":
        for message_id in result["deleted_ids"]:
            _unlink_voice(message_id)
    return jsonify({
        "status": "ok",
        "action": result["action"],
        "deleted_id": result.get("deleted_id"),
        "activated_id": result.get("activated_id"),
    })


# pre: an API key is configured and the newest message is a user turn
# post: her last reply is replaced with a fresh one (same flow as /)
@application.route("/regenerate", methods=["POST"])
def regenerate():
    if not has_api_key():
        return jsonify({"message": "No API key. Add one in Settings."}), 400
    if chat.store.load_web_access():
        # Web ON: the same live progress as a new reply (no hold-on line -
        # the first one is still in the chat).
        last = memory.get_last_message()
        if last is not None and last["role"] == "assistant" and (
                memory.get_message_is_greeting(last["id"])
                or memory.get_message_is_aside(last["id"])):
            return jsonify({"message": "Nothing to regenerate here"}), 400

        def finish(result):
            pack, new_id = result
            speech_id = uuid.uuid4().hex
            with _speech_requests_lock:
                _speech_requests[speech_id] = (time.monotonic(), pack.assistant_reply_JPS, new_id)
            voice_gate.expect(speech_id)
            return {"phase": "reply", "response": pack.assistant_reply_ENG,
                    "japanese": pack.assistant_reply_JPS, "speech_id": speech_id,
                    "assistant_id": new_id,
                    "sources": memory.sources_for_ui(memory.load_message_sources(new_id))}
        return _web_stream(regenerateReply, finish, with_aside=False)
    try:
        pack, new_id = regenerateReply()
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    except Exception as exc:
        print("[Flask] LLM failure during regenerate:", repr(exc))
        return jsonify({"message": _llm_error_message(exc)}), 502

    speech_id = uuid.uuid4().hex
    with _speech_requests_lock:
        _speech_requests[speech_id] = (time.monotonic(), pack.assistant_reply_JPS, new_id)
    voice_gate.expect(speech_id)

    return jsonify({
        "response": pack.assistant_reply_ENG,
        "speech_id": speech_id,
        "assistant_id": new_id,
    })


# pre: JSON body contains "message_id" of a stored assistant reply
# post: that version becomes the one being viewed; her memory follows it
@application.route("/memory/versions/activate", methods=["POST"])
def activate_version():
    data = request.get_json(silent=True)
    message_id = data.get("message_id") if isinstance(data, dict) else None
    if not isinstance(message_id, int):
        return jsonify({"message": "Missing 'message_id' field"}), 400
    activated = memory.activate_version(message_id)
    if activated is None:
        return jsonify({"message": "Message not found"}), 404
    return jsonify({"status": "ok", "activated_id": activated})


# pre: message_id is an assistant reply with a saved Japanese voice line
# post: its voice is (re)synthesized on demand when the file is missing, so
#       any reply can be replayed even after its file was pruned by the cap
@application.route("/memory/<int:message_id>/voice", methods=["POST"])
def regenerate_voice(message_id):
    japanese = memory.get_message_japanese(message_id)
    if japanese is None:
        return jsonify({"message": "This reply has no saved voice line to re-speak."}), 400
    rel = f"generated/voice_{message_id}.wav"
    path = Path(__file__).resolve().parent / rel
    if not path.exists():
        if not renderVoiceToPath(japanese, path):
            return jsonify({"message": "Voice generation failed. Is GPT-SoVITS running?"}), 502
    memory.set_message_audio(message_id, rel)
    return jsonify({"status": "ok", "audio_url": "/message_audio/" + rel})


# pre: message_id is one of her lines with a saved Japanese voice line
# post: Replay without a saved clip (2026-10-24, VM test: an interrupted line
#       is never saved, and /memory/<id>/voice above renders the WHOLE clip
#       before the first sound - minutes on a processor). A saved clip ->
#       {"audio_url"}; otherwise a single-use speech id for the same
#       streaming voice path her live lines use -> {"speech_id"}. The stream
#       keeps the clip for the next Replay when it is heard to the end.
_GENERATED_DIR = Path(__file__).resolve().parent / "generated"


@application.route("/memory/<int:message_id>/speak", methods=["POST"])
def speak_message(message_id):
    japanese = memory.get_message_japanese(message_id)
    if japanese is None:
        return jsonify({"message": "This reply has no saved voice line to re-speak."}), 400
    rel = f"generated/voice_{message_id}.wav"
    if (_GENERATED_DIR / f"voice_{message_id}.wav").exists():
        memory.set_message_audio(message_id, rel)
        return jsonify({"status": "ok", "audio_url": "/message_audio/" + rel})
    return jsonify({"status": "ok",
                    "speech_id": _mint_speech_id_text(japanese, message_id)})


@application.route("/getVoiceRetention", methods=["GET"])
def get_voice_retention():
    """How many voice recordings are kept on disk (0 = unlimited)."""
    return jsonify({"limit": memory.load_voice_retention()})


@application.route("/setVoiceRetention", methods=["POST"])
def set_voice_retention():
    """Persist the voice-recording cap (0 = unlimited)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "limit" not in data:
        return jsonify({"message": "Missing 'limit' field"}), 400
    try:
        limit = int(data["limit"])
    except (TypeError, ValueError):
        return jsonify({"message": "'limit' must be a number"}), 400
    saved = memory.save_voice_retention(limit)
    return jsonify({"status": "ok", "limit": saved})


@application.route("/getNames", methods=["GET"])
def get_names():
    """The user's name list (Settings -> Names she knows): how each name is
    written in her Japanese line and said by her voice."""
    return jsonify({"names": [{"word": w, "reading": r}
                              for w, r in speech_text.load_user_names()]})


@application.route("/setNames", methods=["POST"])
def set_names():
    """Replace the user's name list. Each reading must be kana; rows that are
    not (or have no name) are refused with the first bad row named."""
    data = request.get_json(silent=True)
    rows = data.get("names") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return jsonify({"message": "Missing 'names' list"}), 400
    pairs = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        word = str(row.get("word") or "").strip()
        reading = str(row.get("reading") or "").strip()
        if not word and not reading:
            continue  # an empty row the user never filled
        if not word or "=" in word:
            return jsonify({"message": f"Name missing or not allowed: '{word}'"}), 400
        if not speech_text.is_kana(reading):
            return jsonify({"message": f"'{word}': write how she says it in katakana or hiragana"}), 400
        pairs.append((word, reading))
    saved = speech_text.save_user_names(pairs)
    return jsonify({"status": "ok", "names": [{"word": w, "reading": r} for w, r in saved]})


@application.route("/getContextBudget", methods=["GET"])
def get_context_budget():
    """The conversation-memory number Settings shows (estimated tokens): the
    built-in model's own number in built-in mode, else the custom model's.
    builtin_cap = the most the built-in model's context holds (null when
    built-in mode is off); default = what a fresh install starts with."""
    own = builtin_llm.history_budget()
    if own is not None:
        return jsonify({"budget": own, "builtin_cap": builtin_llm.history_budget_cap(),
                        "default": builtin_llm.history_default(), "mode": "builtin"})
    return jsonify({"budget": memory.load_context_budget(), "builtin_cap": None,
                    "default": memory.DEFAULT_CONTEXT_BUDGET, "mode": "custom"})


# pre: none
# post: how she thinks - the built-in model the installer set up (option A)
#       or the user's own / a cloud model. "available" = the installer set a
#       built-in model up on this install (option B never did).
@application.route("/brainMode", methods=["GET"])
def get_brain_mode():
    return jsonify(builtin_llm.describe())


# pre: body {"mode": "builtin" | "custom"}; "builtin" only when available
# post: the choice is saved in data/local_llm.json and applied at once:
#       "builtin" loads her model (in the background), "custom" unloads it
#       and frees its graphics memory.
@application.route("/setBrainMode", methods=["POST"])
def set_brain_mode():
    data = request.get_json(silent=True)
    mode = data.get("mode") if isinstance(data, dict) else None
    if mode not in ("builtin", "custom"):
        return jsonify({"message": "mode must be 'builtin' or 'custom'"}), 400
    try:
        builtin_llm.save_mode(mode)
    except LookupError as exc:
        return jsonify({"message": str(exc)}), 400
    except OSError as exc:
        return jsonify({"message": f"Could not save the choice: {exc}"}), 500
    reset_llm()   # rebuild the model clients for the new server
    if mode == "builtin":
        try:
            if builtin_server.status()["state"] not in ("loading", "loaded"):
                builtin_server.load()
        except (LookupError, ValueError) as exc:
            print(f"[Flask] /setBrainMode: her model was not loaded: {exc}")
    else:
        builtin_server.unload()
        # Back on the custom brain: if it is Vertex, renew the pass at once
        # - the keeper pauses while the built-in model is active, so the
        # pass in api_key.txt may be expired, and the first chat after the
        # switch must not race the keeper's next round.
        providers.refresh_vertex_if_due(force=True)
    info = builtin_llm.describe()
    info["restart_needed"] = False
    info["server"] = builtin_server.status()
    return jsonify(info)


# pre: none
# post: her built-in model's server: state ("unloaded" | "loading" |
#       "loaded" | "failed"), the file it runs (or tried), why it failed,
#       every chat-model GGUF in the models folder (Settings' dropdown;
#       asking again = "Refresh list"), and the Vision file choice: the
#       vision-file GGUFs in the folder (picker options), the saved choice
#       ("" = Automatic, "none" = no pictures, else a file name), and what
#       Automatic finds for the model she loads.
@application.route("/builtinModel", methods=["GET"])
def get_builtin_model():
    info = builtin_llm.describe()
    info["server"] = builtin_server.status()
    info["files"] = builtin_llm.list_models()
    info["selected"] = builtin_llm.selected_file()
    folder = builtin_llm.models_dir()
    info["folder"] = str(folder) if folder else ""
    info["catalog"] = builtin_llm.catalog()
    info["vision_files"] = builtin_llm.vision_files()
    info["vision_choice"] = builtin_llm.vision_choice()
    info["vision_auto"] = os.path.basename(builtin_llm.vision_file(info["selected"]))
    return jsonify(info)


# pre: body {"file": "<a GGUF name from /builtinModel's files>"} (optional:
#      default = the saved choice); built-in mode on
# post: her model (re)loads in the background; a file that loads fine is
#       the one she starts with next time
@application.route("/builtinModel/load", methods=["POST"])
def load_builtin_model():
    data = request.get_json(silent=True)
    file = data.get("file") if isinstance(data, dict) else None
    if file is not None and not isinstance(file, str):
        return jsonify({"message": "'file' must be a file name"}), 400
    try:
        status = builtin_server.load(file or None)
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"message": str(exc)}), 409
    return jsonify({"status": "ok", "server": status})


# pre: body {"file": "" | "none" | "<a vision GGUF name from /builtinModel's
#      visionFiles>"} ("" = Automatic, "none" = no pictures); built-in mode
#      on
# post: the Vision file choice is saved (kept like the chosen model file in
#       data/local_llm.json); the model is (re)loaded with it on the next
#       Load
@application.route("/builtinModel/vision", methods=["POST"])
def set_builtin_model_vision():
    data = request.get_json(silent=True)
    file = data.get("file") if isinstance(data, dict) else None
    if file is not None and not isinstance(file, str):
        return jsonify({"message": "'file' must be a file name, 'none', or empty"}), 400
    try:
        builtin_llm.save_selected_vision_file(file or "")
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"message": str(exc)}), 409
    return jsonify({"status": "ok", "choice": builtin_llm.vision_choice()})


# post: her models folder opens in Explorer / Finder (Settings' "Open
#       models folder", for putting a downloaded GGUF in place)
@application.route("/builtinModel/openFolder", methods=["POST"])
def open_builtin_models_folder():
    try:
        return jsonify({"status": "ok", "folder": builtin_llm.open_models_folder()})
    except LookupError as exc:
        return jsonify({"message": str(exc)}), 409
    except OSError as exc:
        return jsonify({"message": f"Could not open the folder: {exc}"}), 500


# post: her model stops and its graphics memory is free; she cannot answer
#       until it is loaded again
@application.route("/builtinModel/unload", methods=["POST"])
def unload_builtin_model():
    return jsonify({"status": "ok", "server": builtin_server.unload()})


@application.route("/setContextBudget", methods=["POST"])
def set_context_budget():
    """Persist the history token budget (clamped to a sane range)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "budget" not in data:
        return jsonify({"message": "Missing 'budget' field"}), 400
    try:
        budget = int(data["budget"])
    except (TypeError, ValueError):
        return jsonify({"message": "'budget' must be a number"}), 400
    if builtin_llm.active():
        try:
            saved = builtin_llm.save_history_budget(budget)
        except (LookupError, OSError) as exc:
            return jsonify({"message": f"Could not save: {exc}"}), 500
    else:
        saved = memory.save_context_budget(budget)
    return jsonify({"status": "ok", "budget": saved})


@application.route("/getSampling", methods=["GET"])
def get_sampling():
    """The user's Model Sampling settings. Each parameter is
    {"enabled": bool, "value": number|null}; disabled = the server's own
    default applies. 'params' carries the allowed ranges for the UI and
    'rejected' lists params the current server refused this app session."""
    return jsonify({
        "sampling": memory.load_sampling(),
        "params": memory.SAMPLING_PARAMS,
        "local_only": list(memory.SAMPLING_LOCAL_ONLY),
        "rejected": llm.rejected_sampling_params(_model_server_address()),
    })


@application.route("/setSampling", methods=["POST"])
def set_sampling():
    """Persist the Model Sampling settings (validated + clamped server-side).
    The LLM client cache includes the settings, so the new values apply to
    the next message - no app restart needed."""
    data = request.get_json(silent=True)
    try:
        saved = memory.save_sampling(data)
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    reset_llm()
    return jsonify({"status": "ok", "sampling": saved})


@application.route("/getLLMServer", methods=["GET"])
def get_llm_server():
    """The saved model-server address ('' = auto-detect local ports)."""
    return jsonify({"address": memory.load_llm_server()})


@application.route("/setLLMServer", methods=["POST"])
def set_llm_server():
    """Persist the model-server address; empty restores auto-detect."""
    data = request.get_json(silent=True)
    address = data.get("address") if isinstance(data, dict) else None
    if not isinstance(address, str):
        return jsonify({"message": "Missing 'address' field"}), 400
    saved = memory.save_llm_server(address)
    reset_llm()  # rebuild the client so the new address takes effect now
    return jsonify({"status": "ok", "address": saved})


# LLM provider picker (feature/provider-picker): Settings offers a list of
# providers; choosing one fills in the server address (for Google Vertex it
# signs in with the user's JSON key instead). Keys are saved per provider
# (data/api_keys.json, personal); api_key.txt stays "the key in use" for the
# memory sidecar.
def _can_reveal() -> bool:
    """The saved key may be shown back only on the PC itself or through the
    user's own Tailscale tunnel: `tailscale serve` adds Tailscale-User-Login
    for the user's own devices and strips it from public Funnel requests."""
    return _from_this_pc() or bool(request.headers.get("Tailscale-User-Login"))


@application.route("/llm/providers", methods=["GET"])
def llm_provider_listing():
    """The provider table (+ has_key per row), the active one, the saved
    address, Vertex's sign-in state, and whether this client may see keys."""
    data = providers.listing()
    data["can_reveal"] = _can_reveal()
    return jsonify(data)


@application.route("/llm/key", methods=["POST"])
def llm_save_key():
    """The Settings top "Save" (or Enter in the key / address box): store
    the typed key for the chosen provider (an EMPTY key leaves the saved
    one, if any, untouched), then make that provider the ACTIVE connection
    at once - its address, the key in use (api_key.txt) and a rebuilt
    client - all in this one call, so nothing is half-saved. The saved
    model is settled against the new server's model list (kept / cleared /
    left as is - _settle_model_after_switch). The reply carries the probe
    of that server so Settings can show which models it offers, plus the
    fresh listing fields (active, address, has_key) and the model now
    saved."""
    data = request.get_json(silent=True) or {}
    provider = data.get("provider")
    key = data.get("key")
    address = data.get("address")
    if not isinstance(provider, str) or not isinstance(key, str):
        return jsonify({"message": "Missing 'provider' or 'key' field"}), 400
    address = address if isinstance(address, str) else ""
    try:
        if key.strip():
            providers.save_provider_key(provider, key)
        result = providers.activate(provider, address)
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    except vertex_auth.VertexSignInError as exc:
        return jsonify({"message": str(exc)}), 400
    probe = providers.probe(provider, address)
    model = _settle_model_after_switch(provider, probe)
    return jsonify({"status": "ok", "active": result["id"],
                    "address": result["address"],
                    "has_key": providers.has_key(provider),
                    "model": model, **probe})


@application.route("/llm/models", methods=["POST"])
def llm_provider_models():
    """Probe one provider's model list (no chat, no model call)."""
    data = request.get_json(silent=True) or {}
    provider = data.get("provider")
    address = data.get("address")
    if not isinstance(provider, str) or providers.get(provider) is None:
        return jsonify({"message": "Unknown provider"}), 400
    return jsonify(providers.probe(provider, address if isinstance(address, str) else ""))


@application.route("/llm/test", methods=["POST"])
def llm_test_model():
    """The Settings "Test" button: one short message through the chosen
    model in Amadeus's own reply format (it proves the provider, the key
    and the model for HER - the model list alone does not). Nothing is
    saved or changed. 200 for every outcome; 400 only for an unknown
    provider."""
    data = request.get_json(silent=True) or {}
    provider = data.get("provider")
    address = data.get("address")
    model = data.get("model")
    if not isinstance(provider, str) or providers.get(provider) is None:
        return jsonify({"message": "Unknown provider"}), 400
    return jsonify(providers.test_model(
        provider, address if isinstance(address, str) else "",
        model if isinstance(model, str) else ""))


@application.route("/llm/key", methods=["GET"])
def llm_reveal_key():
    """The saved key for one provider - only on the PC itself or through the
    user's own Tailscale tunnel (never over a public Funnel)."""
    provider = request.args.get("provider") or ""
    row = providers.get(provider)
    if row is None:
        return jsonify({"message": "Unknown provider"}), 400
    if row["id"] == "vertex":
        return jsonify({"message": "Google Vertex uses a JSON key, not an API key."}), 400
    if not _can_reveal():
        return jsonify({"message": "Open Settings on the PC or one of your own devices to view the key."}), 403
    response = jsonify({"key": providers.key_for(provider)})
    response.headers["Cache-Control"] = "no-store"
    return response


@application.route("/llm/vertex", methods=["POST"])
def llm_save_vertex():
    """Save the Google Vertex JSON key (or just its endpoint = region). A
    saved key makes Vertex the ACTIVE provider at once - "Save connects at
    once", like the top "Save" for the other providers; a region-only
    change does the same when a key is saved (the user chose a new
    endpoint, so it is used now). A region the clean_region rule refuses is
    rejected before anything is written. A sign-in failure is reported, not
    raised - the saved key stands (the connection then keeps the previous
    provider). The saved model is settled against the new endpoint's
    catalogue like the top Save does (_settle_model_after_switch): the
    saved name is compared WITHOUT its "google/" prefix - in the list ->
    kept, not in it -> cleared, catalogue not loaded -> left as is. The
    reply carries the model now saved, Vertex's check (catalogue included)
    and the active provider."""
    data = request.get_json(silent=True) or {}
    key_text = data.get("json")
    region = data.get("region")
    region_given = isinstance(region, str) and region.strip()
    key_saved = False
    try:
        if isinstance(key_text, str) and key_text.strip():
            # No region in the request: keep the saved one (a blank region
            # would clobber it).
            key_saved = True
            vertex_auth.save_key(key_text, region if region_given else "",
                                 keep_region=not region_given)
        elif region_given:
            vertex_auth.save_region(region)
        else:
            return jsonify({"message": "Missing 'json' or 'region' field"}), 400
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    body = {"status": "ok", "vertex": vertex_auth.info()}
    # With a key saved - just stored, or standing from before - the chosen
    # endpoint becomes the connection at once.
    if key_saved or vertex_auth.info()["configured"]:
        try:
            providers.activate("vertex")
        except (ValueError, vertex_auth.VertexSignInError) as exc:
            body["message"] = str(exc)
    body["active"] = providers.active_id()
    probe = providers.probe("vertex")
    body["model"] = _settle_model_after_switch("vertex", probe)
    return jsonify({**body, **probe})


@application.route("/llm/vertex/locations", methods=["GET"])
def llm_vertex_locations():
    """The Vertex endpoints (locations) Settings may offer: "global" first,
    then the regions - the multi-regions are left out (they refuse chat
    calls). Without a saved JSON key only "global" is offered; a sign-in
    failure is read the same way (the real words then come from the check
    or the save). Same access as the other listing routes - it holds no
    key material."""
    try:
        locations = vertex_auth.list_locations()
    except vertex_auth.VertexSignInError:
        locations = [vertex_auth.DEFAULT_REGION]
    return jsonify({"locations": locations, "region": vertex_auth.load_region()})


@application.route("/llm/provider", methods=["POST"])
def llm_set_provider():
    """Switch the active provider (address auto-filled from the table; an
    optional custom address / model name may ride along). For Vertex the
    model is saved as google/<name>."""
    data = request.get_json(silent=True) or {}
    provider = data.get("id")
    address = data.get("address")
    model = data.get("model")
    if not isinstance(provider, str) or providers.get(provider) is None:
        return jsonify({"message": "Unknown provider"}), 400
    try:
        result = providers.activate(provider, address if isinstance(address, str) else "")
    except (ValueError, vertex_auth.VertexSignInError) as exc:
        return jsonify({"message": str(exc)}), 400
    if isinstance(model, str) and model.strip():
        saved_model = vertex_auth.model_name(model) if provider == "vertex" else model.strip()
        setLLMModel(saved_model)
    else:
        saved_model = memory.load_llm_model()
    return jsonify({"status": "ok", "active": result["id"],
                    "address": result["address"], "model": saved_model})


# pre: none
# post: returns the user's saved name ('' = not set - she may still learn
#       one from conversation, but the Settings value always outranks it)
@application.route("/getUsername", methods=["GET"])
def get_username():
    response = jsonify({"username": memory.load_username(),
                        "reading": memory.load_username_reading()})
    response.headers["Cache-Control"] = "no-store"
    return response


# pre: JSON body contains "username" (a string; '' clears the name) and
#      optionally "reading" (how she says it, kana; '' clears it)
# post: the name is persisted (data/username.txt, personal - never in git)
#       and takes effect in the prompt from the next turn on (replies AND
#       greetings). The reading (data/username_reading.txt) is saved when
#       given; when it is NOT given and the name changed, the old reading
#       belonged to the old name and is cleared.
@application.route("/setUsername", methods=["POST"])
def set_username():
    data = request.get_json(silent=True)
    name = data.get("username") if isinstance(data, dict) else None
    if not isinstance(name, str):
        return jsonify({"message": "Missing 'username' field"}), 400
    reading = data.get("reading")
    if reading is not None and not isinstance(reading, str):
        return jsonify({"message": "'reading' must be a string"}), 400
    previous = memory.load_username()
    saved = memory.save_username(name)
    if reading is not None:
        saved_reading = memory.save_username_reading(reading)
    elif saved != previous:
        saved_reading = memory.save_username_reading("")
    else:
        saved_reading = memory.load_username_reading()
    return jsonify({"status": "ok", "username": saved, "reading": saved_reading})


# pre: none (works on the saved name)
# post: when a name is saved but has no reading yet, one is suggested
#       (rule for initials/kana, else ONE short model call, capped at 30 s)
#       and saved; returns the reading ('' = none could be made - the user
#       can type it). An existing reading is returned untouched.
@application.route("/suggestUsernameReading", methods=["POST"])
def suggest_username_reading():
    name = memory.load_username()
    reading = memory.load_username_reading()
    if name and not reading:
        reading = chat.suggest_name_reading(name)
        if reading:
            memory.save_username_reading(reading)
    return jsonify({"status": "ok", "username": name, "reading": reading})


# pre: JSON body contains "name" (the name as typed in Settings - it need
#      not be saved yet)
# post: returns a suggested reading for it ('' = none could be made); saves
#       NOTHING - the Settings "Suggest" button fills the box and Save then
#       stores it (so Save makes no model call of its own). Initials / kana
#       need no model call; otherwise one short call, capped at 30 s.
@application.route("/suggestNameReading", methods=["POST"])
def suggest_name_reading():
    data = request.get_json(silent=True)
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, str):
        return jsonify({"message": "Missing 'name' field"}), 400
    name = name.strip()
    reading = chat.suggest_name_reading(name) if name else ""
    return jsonify({"status": "ok", "name": name, "reading": reading})


# pre: none
# post: returns the user's pronouns ('' = not set) and what she calls them
#       ('auto' = let her decide from the trust tier, a key of
#       memory.ADDRESS_FORMS, or 'custom:<text>')
@application.route("/getUserProfile", methods=["GET"])
def get_user_profile():
    response = jsonify({"pronouns": memory.load_pronouns(),
                        "address": memory.load_address()})
    response.headers["Cache-Control"] = "no-store"
    return response


# pre: JSON body with "pronouns" and/or "address" (strings; '' clears the
#      pronouns, anything unknown for address means 'auto')
# post: the given fields are persisted (data/user_pronouns.txt,
#       data/user_address.txt - personal, never in git); her prompt uses
#       them from the next turn on. The CharacterMemory sidecar reads the
#       pronouns when it starts.
@application.route("/setUserProfile", methods=["POST"])
def set_user_profile():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not ({"pronouns", "address"} & data.keys()):
        return jsonify({"message": "Send 'pronouns' and/or 'address'"}), 400
    for field in ("pronouns", "address"):
        if field in data and not isinstance(data[field], str):
            return jsonify({"message": f"'{field}' must be a string"}), 400
    if "pronouns" in data:
        memory.save_pronouns(data["pronouns"])
    if "address" in data:
        memory.save_address(data["address"])
    return jsonify({"status": "ok", "pronouns": memory.load_pronouns(),
                    "address": memory.load_address()})


def _model_in_list(model_name: str, server_models: list[str]) -> bool:
    """Does the configured model match anything the server advertises?

    Server tags differ from the name Amadeus actually sends: e.g. Unsloth
    lists a model as "unsloth/Qwen3.8-27B-GGUF" while the app talks to it as
    "Qwen3.8-27B-GGUF". Both forms work, so a match is a full-name match OR a
    base-name match (the part after the final "/"). Comparison is case-
    sensitive on the base name so a genuine typo still shows the amber dot.
    """
    name = (model_name or "").strip()
    if not name:
        return False
    if name in server_models:
        return True
    name_base = name.rsplit("/", 1)[-1]
    for mid in server_models:
        if isinstance(mid, str) and mid.strip().rsplit("/", 1)[-1] == name_base:
            return True
    return False


def _settle_model_after_switch(provider: str, probe: dict) -> str:
    """The saved model after the top "Save" (or a Vertex save) switched the
    connection: kept when the new server's model list says it is there (the
    same matching as the /testConnection dot), cleared (the "No Model
    Selected." state) when the list says it is not - she must not chat with
    a model name the new server does not list - and left as is when the list
    could not be loaded (unreachable, key rejected, still loading) or the
    server takes any model name ("any_model"). For Vertex the list is the
    endpoint's own catalogue when it loaded (catalogue True): the saved name
    is compared WITHOUT its leading "google/" prefix - in the list -> kept,
    not in it -> cleared (the catalogue lists what the endpoint OFFERS; it
    over-promises in the other direction too, a listed name may still 404,
    which is why the Test button stays the proof). With the fixed fallback
    list (the catalogue could not be loaded) the model is left as is.
    Returns the model now saved ("" = none, shown as "No Model Selected.")."""
    model = memory.load_llm_model()
    if provider == "vertex":
        if probe.get("catalogue"):
            bare = (model or "").strip()
            if bare.startswith("google/"):
                bare = bare[len("google/"):]
            if bare and bare not in (probe.get("models") or []):
                chat.setLLMModel("")
                return ""
        return model
    if probe.get("any_model"):
        return model
    if not probe.get("reachable") or probe.get("error") or probe.get("loading"):
        return model
    if not model.strip():
        return model
    if _model_in_list(model, probe.get("models") or []):
        return model
    chat.setLLMModel("")
    return ""


# pre: none
# post: probes the (saved, or auto-detected) model server's /v1/models
#       endpoint and reports reachability plus the model names it offers
@application.route("/testConnection", methods=["GET"])
def test_connection():
    builtin = builtin_llm.active()
    configured = True if builtin else memory.load_llm_server()
    address = _model_server_address()
    probe = llm.test_server(address, api_key=chat.API_KEY)
    model_name = getLLMModel()
    model_configured = bool(model_name.strip()) and model_name != "No Model Selected."
    model_found = (
        bool(probe["reachable"])
        and model_configured
        and (probe.get("any_model") or _model_in_list(model_name, probe["models"]))
    )
    # The trap that cost an evening on 2026-10-07: a ":batch" name IS in the
    # server's model list, so the test cheerfully says "model found" - but
    # chat cannot use a batch-only model. Warn the moment the name is found.
    batch_warning = None
    if model_found and model_name.strip().endswith(":batch"):
        batch_warning = (
            f'WARNING: "{model_name.strip()}" is a batch-only model - the server '
            "serves it only through its batch service (results up to 24 h later), "
            f"so chat won't work with it. Use the same model without the "
            '":batch" suffix instead.'
        )
    return jsonify({
        "address": address,
        "configured": bool(configured),
        "reachable": probe["reachable"],
        "loading": bool(probe.get("loading")),
        "models": probe["models"],
        "configured_model": model_name,
        "model_configured": model_configured,
        "model_found": model_found,
        "batch_warning": batch_warning,
        "error": probe["error"],
        "voice_reachable": _voice_engine_reachable(),
        "builtin": builtin,
        "builtin_model_missing": builtin and builtin_llm.model_missing(),
        "builtin_state": builtin_server.status()["state"] if builtin else None,
    })


def _voice_engine_reachable(timeout: float = 2.0) -> bool:
    """Is the voice engine (GPT-SoVITS) answering at all? Any HTTP reply
    counts - its root path is not a real endpoint, so a 404 still means
    "up". Read-only, never generates speech; feeds the footer's
    "voice offline" note."""
    url = getattr(tts_module, "GPTSOVITS_API_URL", "http://127.0.0.1:9880")
    try:
        requests.get(url, timeout=timeout)
        return True
    except Exception:
        return False


# pre: relpath points inside the backend folder (generated/ or assets/)
# post: a stored voice line is streamed back for replay
@application.route("/message_audio/<path:relpath>", methods=["GET"])
def message_audio(relpath):
    # Lines saved before v2.1.2 kept the engine's "0 bytes" WAV header, which
    # Firefox will not replay: write the real sizes in on first replay.
    if relpath.startswith("generated/") and relpath.endswith(".wav"):
        target = (Path(__file__).resolve().parent / relpath).resolve()
        if target.parent == (Path(__file__).resolve().parent / "generated").resolve():
            tts_module.fix_wav_sizes(target)
    response = send_from_directory(Path(__file__).resolve().parent, relpath, conditional=True)
    response.headers["Cache-Control"] = "no-store"
    return response


# pre: None
# post: hidden relationship stats (trust, and any added later), for the Settings view
@application.route("/stats", methods=["GET"])
def get_stats():
    response = jsonify({"status": "ok", "stats": stats.all_stats()})
    response.headers["Cache-Control"] = "no-store"
    return response


# pre: None
# post: her mood right now in plain words ([] = all normal) and the things
#       on her mind about the user, for Settings -> Personality & mood
@application.route("/mood", methods=["GET"])
def get_mood():
    trust = stats.load_stat("trust")
    items = concerns.load(chat._concerns_path())
    response = jsonify({
        "status": "ok",
        "feelings": feelings.describe_for_user(trust=trust, path=chat._feelings_path()),
        "concerns": [
            {"index": n, "text": i["text"], "status": i["status"],
             "added": i["added"].isoformat() if i["added"] else None,
             "due": i["due"].isoformat() if i["due"] else None,
             "done": i["done"].isoformat() if i["done"] else None}
            for n, i in enumerate(items)],
    })
    response.headers["Cache-Control"] = "no-store"
    return response


# pre: index = a position in the /mood concerns list
# post: that concern is removed from data/concerns.txt
@application.route("/concerns/<int:index>", methods=["DELETE"])
def delete_concern(index):
    if not concerns.remove(index, path=chat._concerns_path()):
        return jsonify({"message": "That item is no longer on the list."}), 404
    return jsonify({"status": "ok"})


# ---------- CONVERSATIONS (chat sessions) ----------

# post: lists all sessions (newest activity first) and which one is active
@application.route("/conversations", methods=["GET"])
def list_conversations():
    return jsonify({
        "status": "ok",
        "conversations": memory.list_conversations(),
        "active_id": memory.load_active_conversation(),
    })


# pre: optional JSON body {"title": str}
# post: a new session is created AND becomes active
@application.route("/conversations", methods=["POST"])
def create_conversation():
    data = request.get_json(silent=True)
    title = data.get("title") if isinstance(data, dict) else None
    conv_id = memory.create_conversation(title if isinstance(title, str) else None)
    return jsonify({"status": "ok", "id": conv_id})


# post: the given session becomes active (its messages then come from /getMemory)
@application.route("/conversations/<int:conversation_id>/activate", methods=["POST"])
def activate_conversation(conversation_id):
    try:
        active = memory.set_active_conversation(conversation_id)
    except ValueError:
        return jsonify({"message": "Conversation not found"}), 404
    return jsonify({"status": "ok", "active_id": active})


# pre: JSON body {"title": str}
# post: the session is renamed
@application.route("/conversations/<int:conversation_id>", methods=["PATCH"])
def rename_conversation(conversation_id):
    data = request.get_json(silent=True)
    title = data.get("title") if isinstance(data, dict) else None
    if not isinstance(title, str) or not title.strip():
        return jsonify({"message": "No title received"}), 400
    try:
        ok = memory.rename_conversation(conversation_id, title)
    except ValueError:
        return jsonify({"message": "Title must not be empty"}), 400
    if not ok:
        return jsonify({"message": "Conversation not found"}), 404
    return jsonify({"status": "ok"})


# post: the session and all of its messages are removed; at least one session
#       always remains, and when the deleted one was active the response carries
#       the id that took over so the client can load its messages
@application.route("/conversations/<int:conversation_id>", methods=["DELETE"])
def delete_conversation(conversation_id):
    try:
        was_active = memory.load_active_conversation() == conversation_id
    except Exception:
        was_active = False
    try:
        ok = memory.delete_conversation(conversation_id)
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    if not ok:
        return jsonify({"message": "Conversation not found"}), 404
    resp = {"status": "ok"}
    if was_active:
        resp["active_id"] = memory.load_active_conversation()
    return jsonify(resp)


def model_ready() -> Dict[str, object]:
    """Is the configured model actually servable right now?

    Uses the same standard /models probe (test_server) and the same model-name
    matching (_model_in_list) as the /testConnection footer dot, so the /greet
    route only fires when the footer would show 'connected'. No LLM generation
    is attempted here - this is a cheap reachability + model-name check only.
    """
    address = _model_server_address()
    model_name = getLLMModel()
    if builtin_llm.active() and builtin_llm.model_missing():
        return {"ready": False, "reason": "built-in model not installed"}
    if not model_name or model_name == "No Model Selected.":
        return {"ready": False, "reason": "no model configured"}
    probe = llm.test_server(address, api_key=chat.API_KEY, timeout=5.0)
    if not probe["reachable"]:
        return {"ready": False,
                "reason": f"model server unreachable at {address}"}
    if not (probe.get("any_model") or _model_in_list(model_name, probe["models"])):
        return {"ready": False,
                "reason": f"model '{model_name}' not served by {address}"}
    return {"ready": True, "reason": ""}


def _mint_speech_id_text(ja_text, assistant_id) -> str:
    """Register a single-use speech id for an EXPLICIT Japanese text (the
    same table the /speech/<id> streaming endpoint reads)."""
    speech_id = uuid.uuid4().hex
    with _speech_requests_lock:
        now = time.monotonic()
        for key in [k for k, (created, _, _) in _speech_requests.items()
                    if now - created > 300]:
            _speech_requests.pop(key, None)
        _speech_requests[speech_id] = (now, ja_text, assistant_id)
        while len(_speech_requests) > 20:
            _speech_requests.pop(next(iter(_speech_requests)))
    voice_gate.expect(speech_id)
    return speech_id


# pre: the configured model is servable (see model_ready) and an API key set
def _mint_speech_id(pack, assistant_id) -> str:
    """Mint a single-use speech id for a stored assistant line (the same
    mechanism /greet uses for the startup greeting)."""
    return _mint_speech_id_text(pack.assistant_reply_JPS, assistant_id)


def _connection_settings_present() -> bool:
    """The minimum saved-settings signal for the first-launch ceremony:
    a real model name. The server address may be empty (= auto-detect the
    local ports) and the API key may be empty (servers that need no key),
    so the model name is what makes "settings saved" true."""
    model = getLLMModel()
    return bool(model) and model != "No Model Selected."


def _serve_ceremony_line(kind: str, ready: Dict[str, object],
                         new_state: str):
    """Deliver one of the fixed first-launch lines (intro / trying /
    reminder): store it as a greeting line, mint the speech id from the
    SPOKEN text (separate from the shown Japanese for these lines - see
    ceremony.py), and return the /greet JSON with the ceremony fields.
    These lines never touch the model, so a fresh install can speak them
    with nothing configured yet."""
    en, ja_display, ja_voice = ceremony.line(kind)
    pack = AmadeusPack(assistant_reply_ENG=en, assistant_reply_JPS=ja_display)
    assistant_id, conv_id = _store_greeting_line(pack)
    ceremony.note(kind, new_state)
    speech_id = _mint_speech_id_text(ja_voice, assistant_id)
    return jsonify({
        "ready": bool(ready["ready"]),
        "response": en,
        "speech_id": speech_id,
        "assistant_id": assistant_id,
        "conversation_id": conv_id,
        "ceremony": new_state,
        "kind": kind,
        # A wait line: spoken, but the ceremony goes on (box stays locked).
        "wait": True,
    })


def _serve_wake_up():
    """Deliver the wake-up line and end the ceremony for good.

    The line is GENERATED (not fixed), through the same guard ladder as
    every other greeting - one forced call, one bounded retry, the
    time-lie net. If generation fails on a reachable model, the static
    fallback line ships instead: she is never left silent, and the chat
    box unlocks only after a line actually lands."""
    if not _wake_gate.acquire(blocking=False):
        # A wake-up is already being generated (e.g. a settings-save
        # re-fired the probe mid-generation): stay silent - the in-flight
        # call ships the line and flips the state to done, and the client
        # keeps polling until it does.
        return jsonify({"ready": False,
                        "reason": "wake-up in progress",
                        "ceremony": memory.load_ceremony_state()})
    try:
        kind = "wake"
        try:
            pack, assistant_id, conv_id = generate_greeting(mode="wake")
            voice = pack.assistant_reply_JPS
        except Exception as exc:
            print("[Flask] Wake-up failure:", repr(exc))
            kind = "builtin_fallback" if builtin_llm.active() else "fallback"
            en, ja_display, ja_voice = ceremony.line(kind)
            pack = AmadeusPack(assistant_reply_ENG=en, assistant_reply_JPS=ja_display)
            assistant_id, conv_id = _store_greeting_line(pack)
            voice = ja_voice
        ceremony.note(kind, memory.CEREMONY_DONE)
        speech_id = _mint_speech_id_text(voice, assistant_id)
        return jsonify({
            "ready": True,
            "response": pack.assistant_reply_ENG,
            "speech_id": speech_id,
            "assistant_id": assistant_id,
            "conversation_id": conv_id,
            "ceremony": memory.CEREMONY_DONE,
            "kind": kind,
        })
    finally:
        _wake_gate.release()


# post: if the first-launch ceremony (ceremony.py) is not done yet, this
#       route drives it: the fixed intro / trying / reminder lines (which
#       need no model at all), the generated wake-up line once the model
#       answers, or the static fallback if generation fails - adding the
#       "ceremony" + "kind" fields to the usual response. Once the
#       ceremony is done it behaves exactly as before: a key is required
#       (cloud providers only - keyless local / LAN / custom servers pass),
#       the model must be ready, and one generated greeting is stored as
#       a normal assistant message with a single-use speech id minted for
#       its Japanese line (the same voice mechanism a normal reply uses).
#       If the model is not ready yet, returns 200 with ready=false so
#       the client can wait (and re-fire when the model comes up) instead
#       of treating it as an error. The body may carry {"just_saved":
#       true} when the client just saved connection settings - a fresh
#       attempt, which re-says the "trying" line and restarts the
#       reminder clock even while already "connecting".
@application.route("/greet", methods=["POST"])
def greet():
    body = request.get_json(silent=True)
    just_saved = bool(body.get("just_saved")) if isinstance(body, dict) else False
    # The page reloaded after a restart from Settings: she says she is back.
    restarted = bool(body.get("restarted")) if isinstance(body, dict) else False
    state = memory.load_ceremony_state()

    if state != memory.CEREMONY_DONE:
        ready = model_ready()
        # A "ready" brain must also be ABLE TO TALK: a CLOUD provider
        # without a key would leave a fresh user with an unlocked box that
        # answers nothing (2026-10-07, fresh en-US VM: she woke on a
        # keyless probe and every message 400'd until a key value was
        # added), so until a key is present she stays on the waiting lines.
        # Keyless local / LAN / custom servers are NOT gated (2026-11-12,
        # MH's call): the client sends a placeholder and such a server
        # answers fine without one.
        if ready["ready"] and not has_api_key():
            ready = {"ready": False, "reason": "API key not set yet"}
        if builtin_llm.active():
            # Built-in model: nothing to connect, NAME FIRST (ceremony.py).
            kind, new_state = ceremony.decide_builtin(
                state, bool(ready["ready"]), builtin_llm.model_missing(),
                bool(memory.load_username().strip()), just_saved=just_saved,
                server_state=builtin_server.status()["state"],
                runner_missing=builtin_llm.runner_missing())
        else:
            kind, new_state = ceremony.decide(
                state, bool(ready["ready"]), _connection_settings_present(),
                just_saved=just_saved,
                name_set=bool(memory.load_username().strip()))
        if kind in ceremony.FIXED_KINDS:
            return _serve_ceremony_line(kind, ready, new_state)
        if kind == "wake":
            return _serve_wake_up()
        if kind == "normal":
            # Brand-new install whose model is already running: record the
            # ceremony as done and run the normal greeting below.
            ceremony.note(kind, new_state)
        elif kind is None:
            # Nothing new to say this call (e.g. the reminder window has
            # not elapsed): the client keeps waiting - same shape as the
            # old not-ready reply, plus where the ceremony stands.
            return jsonify({"ready": False, "reason": ready["reason"],
                            "ceremony": state})
        # kind == "normal" falls through to the normal path below.

    if not has_api_key():
        return jsonify({"message": "No API key. Add one in Settings."}), 400
    ready = model_ready()
    if not ready["ready"]:
        return jsonify({"ready": False, "reason": ready["reason"],
                        "ceremony": memory.CEREMONY_DONE})
    try:
        pack, assistant_id, conv_id = generate_greeting(
            mode="restart" if restarted else "startup")
    except GreetingSuperseded:
        # The user spoke first while the line was being written: no line,
        # not an error - the conversation is already going.
        return jsonify({"ready": False, "reason": "user spoke first",
                        "superseded": True,
                        "ceremony": memory.CEREMONY_DONE})
    except Exception as exc:
        print("[Flask] Greeting failure:", repr(exc))
        return jsonify({"message": _llm_error_message(exc)}), 502

    speech_id = _mint_speech_id(pack, assistant_id)
    return jsonify({
        "ready": True,
        "response": pack.assistant_reply_ENG,
        "speech_id": speech_id,
        "assistant_id": assistant_id,
        "conversation_id": conv_id,
        "ceremony": memory.CEREMONY_DONE,
    })


# pre: the target conversation exists and the client has already activated
#      it (/conversations/<id>/activate); this route does NOT change the
#      active tab - the greeting is pinned to conversation_id, so it is
#      generated against and stored in exactly that tab even if the user
#      switches again while it is being written. The configured model is
#      servable. There are NO staleness/cooldown gates: a topic-switch
#      acknowledgment is voiced on every switch; the client sends the tab
#      the user left ({"previous_id": n}) to name the topic change.
# post: stores ONE line as a plain assistant line in the target tab and mints
#       the speech id - the same shape as /greet. A not-ready model returns
#       200 with {"ready": false, "reason": ...}: the client treats that as
#       "no line", never an error (a tab switch must never break).
@application.route("/conversations/<int:conversation_id>/greet", methods=["POST"])
def greet_conversation(conversation_id):
    # While the first-launch ceremony runs, tab switches deliver no line:
    # the chat box stays locked until the wake-up (or fallback) line lands,
    # and nothing may speak before it. The guard sits before ANY store
    # access.
    ceremony_state = memory.load_ceremony_state()
    if ceremony_state != memory.CEREMONY_DONE:
        return jsonify({"ready": False,
                        "reason": "first launch in progress",
                        "ceremony": ceremony_state})
    if not has_api_key():
        return jsonify({"message": "No API key. Add one in Settings."}), 400
    # The tab the user LEFT - the topic-switch line names it. The client
    # activates the target BEFORE asking for this greeting, so it sends the
    # previous tab as {"previous_id": n}; the active tab is only a fallback
    # for older clients (and is the target itself after a client-side
    # switch, which load_previous_tab_summary drops). Missing / invalid /
    # unknown -> None: the line simply can't name the previous tab.
    body = request.get_json(silent=True)
    raw_previous = body.get("previous_id") if isinstance(body, dict) else None
    if isinstance(raw_previous, int) and not isinstance(raw_previous, bool):
        previous_id = raw_previous
    else:
        try:
            previous_id = memory.load_active_conversation()
        except Exception:
            previous_id = None
    # Existence check only - activating here would yank the active tab back
    # if the user has already switched on (2026-10-15 fix).
    if not memory.conversation_exists(conversation_id):
        return jsonify({"message": "Conversation not found"}), 404
    ready = model_ready()
    if not ready["ready"]:
        return jsonify({"ready": False, "reason": ready["reason"]})
    try:
        pack, assistant_id, conv_id = generate_greeting(
            mode="switch", conversation_id=conversation_id,
            previous_conversation_id=previous_id)
    except GreetingSuperseded:
        return jsonify({"ready": False, "reason": "user spoke first",
                        "superseded": True})
    except Exception as exc:
        print("[Flask] Switch-greeting failure:", repr(exc))
        return jsonify({"message": _llm_error_message(exc)}), 502
    speech_id = _mint_speech_id(pack, assistant_id)
    return jsonify({
        "ready": True,
        "response": pack.assistant_reply_ENG,
        "speech_id": speech_id,
        "assistant_id": assistant_id,
        "conversation_id": conv_id,
    })


# pre: the conversation exists and is the one the user is looking at; the
#      configured model is servable
# post: the user poked her (the Poke button or a tap on her shoulders): she
#       starts talking with ONE line, stored as a poke line in that tab, in
#       the /greet shape. Not ready / first launch / the user spoke first ->
#       200 {"ready": false, "reason": ...}; a model failure -> 502.
@application.route("/conversations/<int:conversation_id>/poke", methods=["POST"])
def poke_conversation(conversation_id):
    ceremony_state = memory.load_ceremony_state()
    if ceremony_state != memory.CEREMONY_DONE:
        return jsonify({"ready": False,
                        "reason": "first launch in progress",
                        "ceremony": ceremony_state})
    if not has_api_key():
        return jsonify({"message": "No API key. Add one in Settings."}), 400
    if not memory.conversation_exists(conversation_id):
        return jsonify({"message": "Conversation not found"}), 404
    ready = model_ready()
    if not ready["ready"]:
        return jsonify({"ready": False, "reason": ready["reason"]})
    try:
        pack, assistant_id, conv_id = generate_greeting(
            mode="poke", conversation_id=conversation_id)
    except GreetingSuperseded:
        return jsonify({"ready": False, "reason": "user spoke first",
                        "superseded": True})
    except Exception as exc:
        print("[Flask] Poke failure:", repr(exc))
        return jsonify({"message": _llm_error_message(exc)}), 502
    speech_id = _mint_speech_id(pack, assistant_id)
    return jsonify({
        "ready": True,
        "response": pack.assistant_reply_ENG,
        "speech_id": speech_id,
        "assistant_id": assistant_id,
        "conversation_id": conv_id,
    })


# pre: message_id is her latest visible line and a generated greeting
#      (chat.regenerate_greeting enforces both)
# post: a new version of that greeting is stored (the old one stays as an
#       earlier version) and returned in the /greet shape. Not allowed ->
#       400 with a plain message; unknown id -> 404; the user spoke first
#       -> 200 {"ready": false, "superseded": true}; a model failure -> 502
#       with the old version restored.
@application.route("/greetings/<int:message_id>/regenerate", methods=["POST"])
def regenerate_greeting_route(message_id):
    if memory.load_ceremony_state() != memory.CEREMONY_DONE:
        return jsonify({"message": "Not available during the first launch."}), 400
    if not has_api_key():
        return jsonify({"message": "No API key. Add one in Settings."}), 400
    ready = model_ready()
    if not ready["ready"]:
        return jsonify({"message": f"The AI server isn't ready: {ready['reason']}"}), 503
    try:
        pack, assistant_id, conv_id = regenerate_greeting(message_id)
    except LookupError:
        return jsonify({"message": "Message not found"}), 404
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    except GreetingSuperseded:
        return jsonify({"ready": False, "reason": "user spoke first",
                        "superseded": True})
    except Exception as exc:
        print("[Flask] Greeting-regenerate failure:", repr(exc))
        return jsonify({"message": _llm_error_message(exc)}), 502
    speech_id = _mint_speech_id(pack, assistant_id)
    return jsonify({
        "ready": True,
        "response": pack.assistant_reply_ENG,
        "speech_id": speech_id,
        "assistant_id": assistant_id,
        "conversation_id": conv_id,
    })
