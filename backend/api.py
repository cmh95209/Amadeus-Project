from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS

from chat import (
    getOutputPacked,
    generate_greeting,
    apply_interaction_trust,
    regenerateReply,
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
)

import ceremony
import llm
import memory
import stats

from tts import streamVoiceChunks, renderVoiceToPath

import chat
import json
import threading
import uuid
import time
from itertools import chain
from pathlib import Path

application = Flask(__name__)
CORS(application)

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


def _llm_error_message(exc: Exception) -> str:
    """Translate a model-server failure into a plain-English message."""
    try:
        import openai
        if isinstance(exc, openai.NotFoundError):
            return _model_not_found_message()
        if isinstance(exc, openai.APIConnectionError):
            return f"Can't reach the model server at {llm._server_url()}. Is it running?"
        if isinstance(exc, openai.APITimeoutError):
            return "The model server took too long to respond. Is it busy or overloaded?"
        if isinstance(exc, openai.AuthenticationError):
            return "The model server rejected the API key. Check it in Settings."
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
@application.route("/", methods=["POST"])
def request_message():
    if not has_api_key():
        return jsonify({"message": "No API key. Add one in Settings."}), 400
    print("[Flask] / route triggered")
    content = request.get_json()
    user_input = content.get("user_input", "")

    if chat.store.load_web_access():
        # Web access ON: the reply is the original single call (both lines at
        # once) and the client speaks the single-JSON protocol.
        try:
            pack, user_id, assistant_id, conv_id = getOutputPacked(user_input)
        except Exception as exc:
            print("[Flask] LLM failure:", repr(exc))
            return jsonify({"message": _llm_error_message(exc)}), 502
        print("\n[Flask]: ENG:", pack.assistant_reply_ENG)
        print("[Flask]: JPS:", pack.assistant_reply_JPS)

        speech_id = uuid.uuid4().hex
        _register_speech(speech_id, pack.assistant_reply_JPS, assistant_id)
        return jsonify({
            "response": pack.assistant_reply_ENG,
            "speech_id": speech_id,
            "user_id": user_id,
            "assistant_id": assistant_id,
            "conversation_id": conv_id,
        })

    # Web access OFF: voice-first (see the doc block above).
    try:
        turn = chat.getOutputPackedVoiceFirst(user_input)
    except Exception as exc:
        # Call 1 failed before the stream could start: the route has not
        # switched to streaming yet, so the original JSON error protocol
        # still applies.
        print("[Flask] LLM failure (voice-first call 1):", repr(exc))
        return jsonify({"message": _llm_error_message(exc)}), 502
    print("\n[Flask]: JPS (voice-first call 1):", turn.pack.assistant_reply_JPS)

    speech_id = uuid.uuid4().hex
    _register_speech(speech_id, turn.pack.assistant_reply_JPS, turn.assistant_id)

    def generate():
        yield "data: " + json.dumps(
            {"phase": "voice", "speech_id": speech_id, "user_id": turn.user_id,
             "assistant_id": turn.assistant_id, "conversation_id": turn.conv_id},
            ensure_ascii=False) + "\n\n"
        # Wait (bounded) for the background English backfill; her voice is
        # already playing by the time this line runs.
        english = turn.wait_en(timeout=120.0)
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
    chunks = streamVoiceChunks(jps_text, save_path=save_path)
    try:
        first = next(chunks)
    except Exception:
        chunks.close()
        return jsonify({"message": "Speech generation failed"}), 502

    def generate():
        try:
            yield from chain((first,), chunks)
        finally:
            chunks.close()
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
    response.call_on_close(chunks.close)
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


@application.route("/setWebAccess", methods=["POST"])
def set_web_access():
    """Turn the web-access toggle on or off (persisted)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "enabled" not in data:
        return jsonify({"message": "Missing 'enabled' field"}), 400
    enabled = bool(data["enabled"])
    memory.save_web_access(enabled)
    return jsonify({"status": "ok", "enabled": enabled})


@application.route("/getDeepThinking", methods=["GET"])
def get_deep_thinking():
    """Current state of the deep-thinking (search judgement) toggle."""
    return jsonify({"enabled": memory.load_deep_thinking()})


@application.route("/setDeepThinking", methods=["POST"])
def set_deep_thinking():
    """Turn deep thinking on or off (persisted)."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or "enabled" not in data:
        return jsonify({"message": "Missing 'enabled' field"}), 400
    enabled = bool(data["enabled"])
    memory.save_deep_thinking(enabled)
    return jsonify({"status": "ok", "enabled": enabled})


# pre: message_id exists; JSON body contains "content"
# post: the stored message text is replaced (this changes what she remembers)
@application.route("/memory/<int:message_id>", methods=["PATCH"])
def edit_message(message_id):
    data = request.get_json(silent=True)
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, str) or not content.strip():
        return jsonify({"message": "No content received"}), 400
    if not memory.update_message_content(message_id, content.strip()):
        return jsonify({"message": "Message not found"}), 404
    return jsonify({"status": "ok"})


# pre: message_id exists
# post: the message row is removed from her memory
@application.route("/memory/<int:message_id>", methods=["DELETE"])
def delete_message(message_id):
    if not memory.delete_message(message_id):
        return jsonify({"message": "Message not found"}), 404
    _unlink_voice(message_id)
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


@application.route("/getContextBudget", methods=["GET"])
def get_context_budget():
    """The user's conversation-history token budget (estimated tokens)."""
    return jsonify({"budget": memory.load_context_budget()})


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
        "rejected": llm.rejected_sampling_params(llm._server_url()),
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


# pre: none
# post: returns the user's saved name ('' = not set - she may still learn
#       one from conversation, but the Settings value always outranks it)
@application.route("/getUsername", methods=["GET"])
def get_username():
    response = jsonify({"username": memory.load_username()})
    response.headers["Cache-Control"] = "no-store"
    return response


# pre: JSON body contains "username" (a string; '' clears the name)
# post: the name is persisted (data/username.txt, personal - never in git)
#       and takes effect in the prompt from the next turn on (replies AND
#       greetings)
@application.route("/setUsername", methods=["POST"])
def set_username():
    data = request.get_json(silent=True)
    name = data.get("username") if isinstance(data, dict) else None
    if not isinstance(name, str):
        return jsonify({"message": "Missing 'username' field"}), 400
    saved = memory.save_username(name)
    return jsonify({"status": "ok", "username": saved})


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


# pre: none
# post: probes the (saved, or auto-detected) model server's /v1/models
#       endpoint and reports reachability plus the model names it offers
@application.route("/testConnection", methods=["GET"])
def test_connection():
    configured = memory.load_llm_server()
    address = configured or llm._server_url()
    probe = llm.test_server(address, api_key=chat.API_KEY)
    model_name = getLLMModel()
    model_configured = bool(model_name.strip()) and model_name != "No Model Selected."
    model_found = (
        bool(probe["reachable"])
        and model_configured
        and _model_in_list(model_name, probe["models"])
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
        "models": probe["models"],
        "configured_model": model_name,
        "model_configured": model_configured,
        "model_found": model_found,
        "batch_warning": batch_warning,
        "error": probe["error"],
    })


# pre: relpath points inside the backend folder (generated/ or assets/)
# post: a stored voice line is streamed back for replay
@application.route("/message_audio/<path:relpath>", methods=["GET"])
def message_audio(relpath):
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
    address = memory.load_llm_server() or llm._server_url()
    model_name = getLLMModel()
    if not model_name or model_name == "No Model Selected.":
        return {"ready": False, "reason": "no model configured"}
    probe = llm.test_server(address, api_key=chat.API_KEY, timeout=5.0)
    if not probe["reachable"]:
        return {"ready": False,
                "reason": f"model server unreachable at {address}"}
    if not _model_in_list(model_name, probe["models"]):
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
            en, ja_display, ja_voice = ceremony.line("fallback")
            pack = AmadeusPack(assistant_reply_ENG=en, assistant_reply_JPS=ja_display)
            assistant_id, conv_id = _store_greeting_line(pack)
            voice = ja_voice
            kind = "fallback"
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
#       ceremony is done it behaves exactly as before: a key is required,
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
    state = memory.load_ceremony_state()

    if state != memory.CEREMONY_DONE:
        ready = model_ready()
        # A "ready" brain must also be ABLE TO TALK: every conversation
        # (and the post-ceremony greeting) requires an API key, so waking
        # her on a keyless readiness probe would leave a fresh user with
        # an unlocked box that answers nothing (2026-10-07, fresh en-US
        # VM: a keyless local server answered the probe, she woke with a
        # confused line, and every message 400'd until a key value was
        # added). Until a key is present she stays on the waiting lines.
        if ready["ready"] and not has_api_key():
            ready = {"ready": False, "reason": "API key not set yet"}
        kind, new_state = ceremony.decide(
            state, bool(ready["ready"]), _connection_settings_present(),
            just_saved=just_saved)
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
        pack, assistant_id, conv_id = generate_greeting()
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


# pre: the target conversation exists (this route activates it, so the
#      greeting is generated against - and stored in - exactly that tab) and
#      the configured model is servable. There are NO staleness/cooldown
#      gates: a topic-switch acknowledgment is voiced on every switch, so the
#      previous tab is captured first (to name the topic change) and passed
#      into the timing context.
# post: stores ONE line as a plain assistant line in the target tab and mints
#       the speech id - the same shape as /greet. A not-ready model returns
#       200 with {"ready": false, "reason": ...}: the client treats that as
#       "no line", never an error (a tab switch must never break).
@application.route("/conversations/<int:conversation_id>/greet", methods=["POST"])
def greet_conversation(conversation_id):
    # While the first-launch ceremony runs, tab switches deliver no line:
    # the chat box stays locked until the wake-up (or fallback) line lands,
    # and nothing may speak before it. The guard sits BEFORE the target
    # tab is activated, so a switch can never change the active
    # conversation mid-ceremony.
    ceremony_state = memory.load_ceremony_state()
    if ceremony_state != memory.CEREMONY_DONE:
        return jsonify({"ready": False,
                        "reason": "first launch in progress",
                        "ceremony": ceremony_state})
    if not has_api_key():
        return jsonify({"message": "No API key. Add one in Settings."}), 400
    # Capture the tab the user LEFT before activating the target - the
    # topic-switch line names it. A failed read is non-fatal (None -> the
    # line simply can't name the previous tab).
    try:
        previous_id = memory.load_active_conversation()
    except Exception:
        previous_id = None
    try:
        memory.set_active_conversation(conversation_id)
    except ValueError:
        return jsonify({"message": "Conversation not found"}), 404
    ready = model_ready()
    if not ready["ready"]:
        return jsonify({"ready": False, "reason": ready["reason"]})
    try:
        pack, assistant_id, conv_id = generate_greeting(
            mode="switch", conversation_id=conversation_id,
            previous_conversation_id=previous_id)
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
