from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS

from chat import (
    getOutputPacked,
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
)

import llm
import memory
import stats

from tts import streamVoiceChunks, renderVoiceToPath

import chat
import threading
import uuid
import time
from itertools import chain
from pathlib import Path

application = Flask(__name__)
CORS(application)

_speech_requests: dict[str, tuple[float, str, int | None]] = {}
_speech_requests_lock = threading.Lock()
_reaction_audio_dir = Path(__file__).resolve().parent / "assets" / "reaction_audio"


def _unlink_voice(assistant_id) -> None:
    """Best-effort removal of a message's saved voice file."""
    try:
        path = Path(__file__).resolve().parent / "generated" / f"voice_{int(assistant_id)}.wav"
        if path.exists():
            path.unlink()
    except (OSError, ValueError, TypeError):
        pass


def _llm_error_message(exc: Exception) -> str:
    """Translate a model-server failure into a plain-English message."""
    try:
        import openai
        if isinstance(exc, openai.APIConnectionError):
            return f"Can't reach the model server at {llm._server_url()}. Is it running?"
        if isinstance(exc, openai.APITimeoutError):
            return "The model server took too long to respond. Is it busy or overloaded?"
        if isinstance(exc, openai.AuthenticationError):
            return "The model server rejected the API key. Check it in Settings."
    except Exception:
        pass
    return f"The model did not return a usable reply ({type(exc).__name__}). Please try again."

# pre:
# - JSON body contains an "key" field
#
# post:
# - updates the active API key if provided
# - returns status indicating success or error
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

# pre:
# - JSON body contains "user_input" as a string
#
# post:
# - generates an assistant response and voice output
# - returns English UI text to the client
@application.route("/", methods=["POST"])
def request_message():
    if not has_api_key():
        return jsonify({"message": "No API key. Add one in Settings."}), 400
    print("[Flask] / route triggered")  
    content = request.get_json()
    user_input = content.get("user_input", "")

    try:
        pack, user_id, assistant_id = getOutputPacked(user_input)
    except Exception as exc:
        print("[Flask] LLM failure:", repr(exc))
        return jsonify({"message": _llm_error_message(exc)}), 502
    print("\n[Flask]: ENG:", pack.assistant_reply_ENG)
    print("[Flask]: JPS:", pack.assistant_reply_JPS)
    
    # Give the browser a single-use speech id. The browser then opens the
    # streaming WAV endpoint, so the same sound that reaches the speakers also
    # drives the Live2D analyser/lip sync.
    speech_id = uuid.uuid4().hex
    with _speech_requests_lock:
        now = time.monotonic()
        for expired in [key for key, (created, _, _) in _speech_requests.items() if now - created > 300]:
            _speech_requests.pop(expired, None)
        _speech_requests[speech_id] = (now, pack.assistant_reply_JPS, assistant_id)
        while len(_speech_requests) > 20:
            _speech_requests.pop(next(iter(_speech_requests)))

    return jsonify({
        "response": pack.assistant_reply_ENG,
        "speech_id": speech_id,
        "user_id": user_id,
        "assistant_id": assistant_id,
    })

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
    return jsonify({"status":"ok","messages": msgs})


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

    return jsonify({"status": "ok", **reply})


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
    return jsonify({
        "address": address,
        "configured": bool(configured),
        "reachable": probe["reachable"],
        "models": probe["models"],
        "configured_model": model_name,
        "model_configured": model_configured,
        "model_found": model_found,
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
