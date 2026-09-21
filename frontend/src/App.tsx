import { FormEvent, useEffect, useRef, useState } from "react";
import Live2DCharacter from "./components/Live2DCharacter";
import SamplingSettings from "./components/SamplingSettings";
import type { Live2DCharacterHandle } from "./components/Live2DCharacter";
import {
  API_BASE,
  getCurrentModel,
  getPersonality,
  setPersonality,
  getApiKeyStatus,
  setApiKey,
  getMemory,
  MemoryMessage,
  resetMemory,
  sendMessage,
  getGreeting,
  setModel,
  getWebAccess,
  setWebAccess,
  getDeepThinking,
  setDeepThinking,
  sendInteraction,
  editMessage,
  deleteMessage,
  undoLastReply,
  regenerate,
  activateReplyVersion,
  regenerateMessageVoice,
  getVoiceRetention,
  setVoiceRetention,
  getContextBudget,
  setContextBudget,
  getLLMServer,
  setLLMServer,
  testConnection,
  ConnectionStatus,
  Conversation,
  listConversations,
  createConversation,
  activateConversation,
  renameConversation,
  deleteConversation as deleteConversationApi,
  getStats,
  StatInfo,
} from "./api";

import { interactions } from "./interactions";
import type { InteractionName } from "./interactions";

// Server tags can carry an "org/" prefix (e.g. "unsloth/Qwen3.8-27B-GGUF")
// while the model Amadeus actually talks to is the bare name. Compare on the
// part after the final "/" so the matching chip still highlights as active.
function baseModelName(name: string): string {
  return name.trim().split("/").pop() ?? "";
}

export default function App() {
  const [messages, setMessages] = useState<MemoryMessage[]>([]);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState("");
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<number | null>(null);
  // The conversation the MESSAGE PANE is actually showing. If it ever
  // diverges from the backend's active conversation, the pane reloads.
  const [displayedConvId, setDisplayedConvId] = useState<number | null>(null);
  const displayedConvIdRef = useRef<number | null>(null);
  displayedConvIdRef.current = displayedConvId;
  const [renamingConvId, setRenamingConvId] = useState<number | null>(null);
  const [convDraft, setConvDraft] = useState("");
  const [sessionsBusy, setSessionsBusy] = useState(false);
  const [input, setInput] = useState("");
  const [model, setModelName] = useState("");
  const [webAccess, setWebAccessState] = useState<boolean | null>(null);
  const [webToggling, setWebToggling] = useState(false);
  const [deepThinking, setDeepThinkingState] = useState<boolean | null>(null);
  const [deepThinkingBusy, setDeepThinkingBusy] = useState(false);
  const [voiceRetention, setVoiceRetentionState] = useState<number>(100);
  const [contextBudget, setContextBudgetState] = useState<number>(40000);
  const [serverAddress, setServerAddressState] = useState<string>("");
  const [connStatus, setConnStatus] = useState<ConnectionStatus | null>(null);
  const [connTesting, setConnTesting] = useState(false);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("Connecting to Amadeus...");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [hasApiKey, setHasApiKey] = useState<boolean | null>(null);
  const [apiKey, setApiKeyInput] = useState("");
  const [savingSettings, setSavingSettings] = useState(false);
  const [settingsError, setSettingsError] = useState("");
  const [settingsNotice, setSettingsNotice] = useState("");
  const [settingsSection, setSettingsSection] = useState<"connection" | "personality" | "sampling">("connection");
  const [statsInfo, setStatsInfo] = useState<StatInfo[]>([]);
  const [personality, setPersonalityText] = useState("");
  const [savedPersonality, setSavedPersonality] = useState("");
  const [personalityLoading, setPersonalityLoading] = useState(true);
  const [personalityLoaded, setPersonalityLoaded] = useState(false);
  const [personalitySaving, setPersonalitySaving] = useState(false);
  const [personalityError, setPersonalityError] = useState("");
  const [personalityNotice, setPersonalityNotice] = useState("");
  const [personalityReload, setPersonalityReload] = useState(0);
  const personalityDirty = personalityLoaded && personality !== savedPersonality;
  const settingsBusy = savingSettings || personalitySaving;
  const modalRef = useRef<HTMLDivElement>(null);
  const missingKey = hasApiKey === false;
  let lastAssistantIndex = -1;
  messages.forEach((m, i) => {
    if (m.role !== "user") lastAssistantIndex = i;
  });
  const idleStatus = status === "Online" || status === "Memory cleared" || status === "This conversation was cleared" || status.startsWith("Model set to ");
  const footerStatus = missingKey && idleStatus ? "No API key" : status;
  const connStatusText = !connStatus
    ? "Not tested yet."
    : !connStatus.reachable
      ? `Can't reach ${connStatus.address}. ${connStatus.error ? connStatus.error : "Is the server running?"}`
      : connStatus.model_configured
        ? connStatus.model_found
          ? `Connected to ${connStatus.address} - model "${connStatus.configured_model}" found.`
          : `Connected to ${connStatus.address}, but "${connStatus.configured_model}" is not in its model list.`
        : `Connected to ${connStatus.address} - enter a model name in the field above.`;

  function closeSettings() {
    if (settingsBusy) return;
    if (personalityDirty && !window.confirm("Discard your unsaved personality changes?")) return;
    setApiKeyInput("");
    setSettingsError("");
    setSettingsNotice("");
    setSettingsOpen(false);
  }

  const bottomRef = useRef<HTMLDivElement>(null);
  const characterRef = useRef<Live2DCharacterHandle>(null);
  // Startup greeting: once per app start (sessionStorage blocks a refresh
  // from re-greeting) plus a re-fire the moment the model server becomes
  // servable (the "I forgot to start it" case). The backend stores her line,
  // so a later startup can naturally notice repeated restarts.
  const greetedThisSession = useRef<boolean>(
    typeof sessionStorage !== "undefined" && sessionStorage.getItem("amadeusGreeted") === "1"
  );
  const modelWasFound = useRef<boolean | null>(null);
  const greetingInFlight = useRef<boolean>(false);

  useEffect(() => {
    void initialize();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({
      behavior: "smooth",
    });
  }, [messages, loading]);

  useEffect(() => {
    if (!settingsOpen) return;
    let cancelled = false;
    setPersonalityLoading(true);
    setPersonalityLoaded(false);
    setPersonalityError("");
    setPersonalityNotice("");
    void getPersonality().then((text) => {
      if (cancelled) return;
      setPersonalityText(text);
      setSavedPersonality(text);
      setPersonalityLoaded(true);
    }).catch((error) => {
      if (!cancelled) setPersonalityError(error instanceof Error ? error.message : "Could not load personality");
    }).finally(() => {
      if (!cancelled) setPersonalityLoading(false);
    });
    return () => { cancelled = true; };
  }, [settingsOpen, personalityReload]);

  useEffect(() => {
    if (!settingsOpen) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    modalRef.current?.focus();
    return () => { previousFocus?.focus(); };
  }, [settingsOpen]);

  useEffect(() => {
    if (!settingsOpen) return;
    let cancelled = false;
    void getStats()
      .then((all) => { if (!cancelled) setStatsInfo(all); })
      .catch(() => { if (!cancelled) setStatsInfo([]); });
    return () => { cancelled = true; };
  }, [settingsOpen]);

  useEffect(() => {
    if (!settingsOpen || !personalityDirty) return;
    const warnBeforeLeaving = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeLeaving);
    return () => window.removeEventListener("beforeunload", warnBeforeLeaving);
  }, [settingsOpen, personalityDirty]);
  // Browsers suspend audio until a user gesture (the launcher's Chromium
  // flag makes this a no-op there; this is the silent fallback for Firefox
  // and other browsers): unlock Web Audio on the first click/keypress, once.
  useEffect(() => {
    const unlock = () => {
      void characterRef.current?.prepareSpeech();
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
    window.addEventListener("pointerdown", unlock);
    window.addEventListener("keydown", unlock);
    return () => {
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
  }, []);

  async function savePersonality() {
    if (settingsBusy || loading || !personalityLoaded || !personalityDirty || !personality.trim()) return;
    setPersonalitySaving(true);
    setPersonalityError("");
    setPersonalityNotice("");
    try {
      const saved = await setPersonality(personality);
      setPersonalityText(saved);
      setSavedPersonality(saved);
      setPersonalityNotice("Personality saved. Changes apply to your next message.");
    } catch (error) {
      setPersonalityError(error instanceof Error ? error.message : "Could not save personality");
    } finally {
      setPersonalitySaving(false);
    }
  }

  async function fireGreeting() {
    if (greetingInFlight.current || greetedThisSession.current) return;
    if (loading) return; // do not interrupt an in-flight turn
    greetingInFlight.current = true;
    try {
      const g = await getGreeting();
      const line = g.response;
      if (!g.ready || !line) return; // model not up yet; the probe re-fires
      greetedThisSession.current = true;
      sessionStorage.setItem("amadeusGreeted", "1");
      setMessages((current) => [
        ...current,
        { role: "assistant", content: line, id: g.assistantId, is_greeting: true },
      ]);
      // She animates even when the browser blocks audio on a fresh load.
      characterRef.current?.playMotion("TapReaction");
      if (g.speechUrl) {
        void characterRef.current?.playSpeech(g.speechUrl).catch(() => undefined);
        rememberVoiceLine(g.assistantId); // surfaces the existing Replay button
      }
    } catch {
      // A greeting failure is silent by design - never shown as an error.
    } finally {
      greetingInFlight.current = false;
    }
  }

  async function refreshConnection() {
    try {
      const status = await testConnection();
      setConnStatus(status);
      const found = status.reachable && status.model_found;
      if (found && modelWasFound.current !== true && !greetedThisSession.current) {
        void fireGreeting(); // fires the moment the model becomes servable
      }
      modelWasFound.current = found;
    } catch {
      setConnStatus(null); // even the backend is unreachable
    }
  }

  // Gentle background probe so the footer dot reflects the model server's
  // real state (a dead server shows red without waiting for a failed message).
  // It also re-syncs the conversation list, so if the active conversation
  // ever changes outside the UI the pane catches up within ~30 seconds.
  useEffect(() => {
    void refreshConnection();
    void refreshConversations();
    const id = window.setInterval(() => {
      void refreshConnection();
      void refreshConversations();
    }, 30000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleTestConnection() {
    if (connTesting) return;
    setConnTesting(true);
    try {
      // Apply the address being edited first, so the test matches what
      // "Save connection" would store.
      await setLLMServer(serverAddress);
      setConnStatus(await testConnection());
    } catch {
      setConnStatus(null);
    } finally {
      setConnTesting(false);
    }
  }

  async function initialize() {
    try {
      const [memory, currentModel, configured, webOn, deepThinkingOn, convs, voiceCap, budgetCap, serverAddr] =
        await Promise.all([
          getMemory(),
          getCurrentModel(),
          getApiKeyStatus(),
          getWebAccess().catch(() => true),
          getDeepThinking().catch(() => false),
          listConversations().catch(() => null),
          getVoiceRetention().catch(() => 100),
          getContextBudget().catch(() => 40000),
          getLLMServer().catch(() => ""),
        ]);

      setMessages(memory);
      setModelName(currentModel);
      setHasApiKey(configured);
      setWebAccessState(webOn);
      setDeepThinkingState(deepThinkingOn);
      setVoiceRetentionState(voiceCap);
      setContextBudgetState(budgetCap);
      setServerAddressState(serverAddr);
      if (convs) {
        setConversations(convs.conversations);
        setActiveConvId(convs.active_id);
        // The startup /getMemory and /conversations come from the same
        // backend state, so the pane is showing the active conversation.
        setDisplayedConvId(convs.active_id);
      }
      setStatus("Online");
      // Startup greeting: fires once now if the model is already servable,
      // and again (still once per session) the instant it comes up. Silent
      // on failure - a greeting must never break the app loading.
      void fireGreeting();
    } catch (error) {
      setStatus(
        error instanceof Error
          ? error.message
          : "Backend unavailable"
      );
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();

    const text = input.trim();

    if (!text || loading) {
      return;
    }

    if (hasApiKey !== true) {
      setSettingsOpen(true);
      return;
    }

    // Unlock Web Audio while this function still runs from a real user gesture.
    characterRef.current?.stopSpeech();
    const speechReady = characterRef.current?.prepareSpeech().then(
      () => true,
      () => false
    );

    setMessages((current) => [
      ...current,
      {
        role: "user",
        content: text,
      },
    ]);

    setInput("");
    setLoading(true);
    setStatus("Amadeus is thinking...");

    try {
      const reply = await sendMessage(text);

      // The reply tells us which conversation the turn was stored in. If the
      // pane is showing a different one, the pane went stale - reload it.
      if (
        reply.conversationId != null &&
        displayedConvIdRef.current !== null &&
        reply.conversationId !== displayedConvIdRef.current
      ) {
        const fresh = await getMemory();
        setMessages(fresh);
        setDisplayedConvId(reply.conversationId);
      }

      setMessages((current) => {
        const next = [...current];
        for (let i = next.length - 1; i >= 0; i--) {
          if (next[i].role === "user" && next[i].id == null) {
            next[i] = { ...next[i], id: reply.userId };
            break;
          }
        }
        next.push({
          role: "assistant",
          content: reply.response,
          id: reply.assistantId,
        });
        return next;
      });

      setStatus("Online");
      if (reply.speechUrl && await speechReady) {
        void characterRef.current?.playSpeech(reply.speechUrl).catch((error) => {
          setStatus(error instanceof Error ? error.message : "Speech playback failed");
        });
        rememberVoiceLine(reply.assistantId);
      } else if (reply.speechUrl) {
        setStatus("Audio could not start. Check browser audio permissions and send again.");
      }
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          content:
            error instanceof Error ? error.message : "The request failed. Please try again.",
        },
      ]);

      setStatus(
        error instanceof Error
          ? error.message
          : "Request failed"
      );
      void refreshConnection();
    } finally {
      setLoading(false);
      void refreshConversations();
    }
  }

  async function saveModel() {
    const nextModel = model.trim();

    if (settingsBusy || loading) return;
    if (!nextModel) {
      setSettingsError("Enter an LLM model.");
      return;
    }

    setSavingSettings(true);
    setSettingsError("");
    setSettingsNotice("");
    let keySaved = false;
    try {
      if (apiKey.trim()) {
        await setApiKey(apiKey.trim());
        keySaved = true;
        setHasApiKey(true);
        setApiKeyInput("");
      }
      await setModel(nextModel);
      await setVoiceRetention(voiceRetention);
      await setContextBudget(contextBudget);
      await setLLMServer(serverAddress);
      void refreshConnection();

      setStatus(`Model set to ${nextModel}`);
      setSettingsNotice("Connection settings saved.");
      // Keep Settings open so drafts in the Personality section are preserved.
    } catch (error) {
      setSettingsError(
        (keySaved ? "API key saved, but model update failed. " : "") +
        (error instanceof Error ? error.message : "Could not save settings")
      );
    } finally {
      setSavingSettings(false);
    }
  }

  async function toggleWebAccess() {
    if (webToggling || webAccess === null) return;

    const next = !webAccess;
    setWebAccessState(next);
    setWebToggling(true);

    try {
      await setWebAccess(next);
      setStatus(next ? "Web access on" : "Web access off");
    } catch (error) {
      setWebAccessState(!next);
      setStatus(
        error instanceof Error
          ? error.message
          : "Could not toggle web access"
      );
    } finally {
      setWebToggling(false);
    }
  }

  async function toggleDeepThinking() {
    if (deepThinkingBusy || deepThinking === null) return;

    const next = !deepThinking;
    setDeepThinkingState(next);
    setDeepThinkingBusy(true);

    try {
      await setDeepThinking(next);
      setStatus(next ? "Deep thinking on" : "Deep thinking off");
    } catch (error) {
      setDeepThinkingState(!next);
      setStatus(
        error instanceof Error
          ? error.message
          : "Could not toggle deep thinking"
      );
    } finally {
      setDeepThinkingBusy(false);
    }
  }

  async function clearMemory() {
    const confirmed = window.confirm(
      "Clear this conversation? The other chat sessions stay untouched."
    );

    if (!confirmed) {
      return;
    }

    try {
      await resetMemory(activeConvId ?? undefined);

      setMessages([]);
      setStatus("This conversation was cleared");
    } catch (error) {
      setStatus(
        error instanceof Error
          ? error.message
          : "Could not clear memory"
      );
    }
  }

  async function handleInteraction(name: InteractionName) {
    if (loading) return;

    const interaction = interactions[name];
    const result = characterRef.current?.playMotion(interaction.motion) ?? "not-ready";
    if (result === "busy") return;
    if (result !== "started") {
      setStatus(result === "missing" ? "Reaction animation is missing" : "Character is still loading");
      return;
    }

    // Unlock audio during the click; the recording URL arrives with the reply.
    const speechReady = characterRef.current?.prepareSpeech().then(
      () => true,
      () => false
    );
    setLoading(true);

    try {
      const reply = await sendInteraction(interaction.backendId);

      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          content: reply.response,
          id: reply.assistantId,
          audio_url: reply.audioRelUrl,
        },
      ]);

      setStatus("Online");
      if (reply.speechUrl && await speechReady) {
        void characterRef.current?.playSpeech(reply.speechUrl).catch((error) => {
          setStatus(error instanceof Error ? error.message : "Interaction audio failed");
        });
      } else if (reply.speechUrl) {
        setStatus("Audio could not start. Check browser audio permissions and try again.");
      }
    } catch (error) {
      setStatus(
        error instanceof Error
          ? error.message
          : "Interaction failed"
      );
    } finally {
      setLoading(false);
      void refreshConversations();
    }
  }

  // The saved WAV only exists once GPT-SoVITS finished streaming the line.
  // Poll briefly after playback starts so the Replay button can appear.
  function rememberVoiceLine(assistantId: number | undefined) {
    if (assistantId == null) return;
    const rel = `/message_audio/generated/voice_${assistantId}.wav`;
    let tries = 0;
    const check = async () => {
      tries += 1;
      try {
        const probe = await fetch(API_BASE + rel, { method: "HEAD", cache: "no-store" });
        if (probe.ok) {
          setMessages((current) =>
            current.map((m) => (m.id === assistantId ? { ...m, audio_url: rel } : m))
          );
          return;
        }
      } catch {
        // Not ready yet; the line may still be generating.
      }
      if (tries < 20) window.setTimeout(() => void check(), 2000);
    };
    void check();
  }

  async function refreshConversations() {
    try {
      const convs = await listConversations();
      setConversations(convs.conversations);
      setActiveConvId(convs.active_id);
      // If the backend's active conversation no longer matches what the
      // pane is showing, the pane is stale - reload it from the backend.
      if (displayedConvIdRef.current != null && convs.active_id !== displayedConvIdRef.current) {
        const fresh = await getMemory();
        setMessages(fresh);
        setDisplayedConvId(convs.active_id);
      }
    } catch {
      // The rail simply keeps showing the previous list.
    }
  }

  async function newChat() {
    if (sessionsBusy || loading) return;
    characterRef.current?.stopSpeech();
    setSessionsBusy(true);
    try {
      const id = await createConversation();
      setActiveConvId(id);
      setMessages([]);
      setDisplayedConvId(id);
      setStatus("New conversation started");
      void refreshConversations();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not start a new chat");
    } finally {
      setSessionsBusy(false);
    }
  }

  async function switchConversation(id: number) {
    if (id === activeConvId || sessionsBusy || loading) return;
    characterRef.current?.stopSpeech();
    setSessionsBusy(true);
    setStatus("Switching conversation...");
    try {
      await activateConversation(id);
      setActiveConvId(id);
      setEditingId(null);
      setMessages(await getMemory());
      setDisplayedConvId(id);
      setStatus("Online");
      void refreshConversations();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not switch conversations");
    } finally {
      setSessionsBusy(false);
    }
  }

  function startConvRename(conv: Conversation) {
    if (sessionsBusy || loading) return;
    setRenamingConvId(conv.id);
    setConvDraft(conv.title);
  }

  function cancelConvRename() {
    setRenamingConvId(null);
    setConvDraft("");
  }

  async function saveConvRename(id: number) {
    const text = convDraft.trim();
    if (renamingConvId !== id) return;
    cancelConvRename();
    if (!text) return;
    try {
      await renameConversation(id, text);
      setConversations((current) =>
        current.map((x) => (x.id === id ? { ...x, title: text } : x))
      );
      setStatus("Conversation renamed");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not rename the conversation");
    }
  }

  async function deleteConvSession(conv: Conversation) {
    if (sessionsBusy || loading) return;
    if (!window.confirm(`Delete “${conv.title}” and all of its messages?`)) return;
    characterRef.current?.stopSpeech();
    setSessionsBusy(true);
    try {
      const newActive = await deleteConversationApi(conv.id);
      setConversations((current) => current.filter((x) => x.id !== conv.id));
      if (newActive != null) {
        // The session we were in was deleted; move to the one that took over.
        setActiveConvId(newActive);
        setMessages(await getMemory());
        setDisplayedConvId(newActive);
      }
      setStatus("Conversation deleted");
      void refreshConversations();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not delete the conversation");
    } finally {
      setSessionsBusy(false);
    }
  }

  async function switchReplyVersion(message: MemoryMessage, direction: -1 | 1) {
    if (!message.id || loading || !message.version_ids) return;
    const idx = message.version_ids.indexOf(message.id);
    const nextIdx = idx + direction;
    if (idx === -1 || nextIdx < 0 || nextIdx >= message.version_ids.length) return;
    const targetId = message.version_ids[nextIdx];
    try {
      setStatus("Loading version...");
      await activateReplyVersion(targetId);
      setMessages(await getMemory());
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not switch version");
    }
  }

  async function replayVoice(message: MemoryMessage) {
    if (!message.id || loading) return;
    characterRef.current?.stopSpeech();
    const ready = await characterRef.current?.prepareSpeech().then(
      () => true,
      () => false
    );
    if (!ready) {
      setStatus("Audio could not start. Check browser audio permissions.");
      return;
    }
    try {
      if (message.audio_url) {
        await characterRef.current?.playSpeech(API_BASE + message.audio_url).catch((error) => {
          setStatus(error instanceof Error ? error.message : "Replay failed");
        });
      } else {
        // The voice file was cleared; re-synthesize it from her saved line.
        setStatus("Generating voice line...");
        const audioUrl = await regenerateMessageVoice(message.id);
        setMessages((current) =>
          current.map((m) => (m.id === message.id ? { ...m, audio_url: audioUrl } : m))
        );
        await characterRef.current?.playSpeech(API_BASE + audioUrl).catch((error) => {
          setStatus(error instanceof Error ? error.message : "Replay failed");
        });
        setStatus("Online");
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Replay failed");
    }
  }

  function startEdit(message: MemoryMessage) {
    if (message.id == null || loading) return;
    setEditingId(message.id);
    setEditDraft(message.content);
  }

  function cancelEdit() {
    setEditingId(null);
    setEditDraft("");
  }

  async function saveEdit(id: number) {
    const text = editDraft.trim();
    if (!text) return;
    try {
      await editMessage(id, text);
      setMessages((current) =>
        current.map((m) => (m.id === id ? { ...m, content: text } : m))
      );
      setStatus("Message updated");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not save the edit");
    } finally {
      cancelEdit();
    }
  }

  async function removeMessage(message: MemoryMessage) {
    if (message.id == null || loading) return;
    if (!window.confirm("Remove this message from the conversation?")) return;
    try {
      await deleteMessage(message.id);
      setMessages((current) => current.filter((m) => m.id !== message.id));
      setStatus("Message removed");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not remove the message");
    }
  }

  async function handleUndo() {
    if (loading) return;
    try {
      const result = await undoLastReply();
      if (!result) {
        setStatus("No reply to undo");
        return;
      }
      setMessages(await getMemory());
      setStatus(
        result.action === "switch"
          ? "Went back to the previous version"
          : "Her last reply was undone"
      );
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not undo the reply");
    }
  }

  async function handleRegenerate() {
    if (loading) return;
    characterRef.current?.stopSpeech();
    const speechReady = characterRef.current?.prepareSpeech().then(
      () => true,
      () => false
    );
    setLoading(true);
    setStatus("Amadeus is rethinking...");
    try {
      const reply = await regenerate();
      // The previous reply is kept as an earlier version; reload the active
      // view so the new version and the version arrows appear correctly.
      setMessages(await getMemory());
      setStatus("Online");
      if (reply.speechUrl && (await speechReady)) {
        void characterRef.current?.playSpeech(reply.speechUrl).catch(() => undefined);
        rememberVoiceLine(reply.assistantId);
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Regeneration failed");
      void refreshConnection();
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="shell">
      {/* Character Panel */}
      <section className="character-panel">
        <header className="brand">
          <div className="brand-mark">
            A
          </div>

          <div>
            <h1>AMADEUS</h1>
            <p>Personal AI Companion</p>
          </div>
        </header>

        <div className="character-stage">
          <div className="scanline" />

          <div className="character-viewport">
            <Live2DCharacter ref={characterRef} onSpeechError={setStatus} />

            {(Object.keys(interactions) as InteractionName[]).map((name) => {
              const interaction = interactions[name];
              return (
                <button
                  key={name}
                  type="button"
                  className="touch-button"
                  style={interaction.position}
                  aria-label={interaction.label}
                  disabled={loading}
                  onClick={() => void handleInteraction(name)}
                >
                  {interaction.label}
                </button>
              );
            })}
          </div>
        </div>

        <footer className="system-footer" role="status" aria-live="polite">
          <span
            className={
              connStatus && !connStatus.reachable ? "status-dot offline"
              : footerStatus === "No API key" ? "status-dot warning"
              : status === "Online" ? "status-dot online"
              : "status-dot"
            }
            title={
              connStatus && !connStatus.reachable
                ? `Model server offline: ${connStatus.address}`
                : connStatus?.reachable
                  ? `Model server online: ${connStatus.address}`
                  : "Checking model server..."
            }
          />

          <span>
            {footerStatus}
          </span>
        </footer>
      </section>

      {/* Conversation Sessions Rail */}
      <nav className="sessions-rail" aria-label="Conversations">
        <div className="sessions-head">
          <button type="button" onClick={() => void newChat()} disabled={loading || sessionsBusy}>
            + New chat
          </button>
        </div>

        <div className="sessions-list">
          {conversations.map((conv) => (
            <div
              key={conv.id}
              className={`session-item${conv.id === activeConvId ? " active" : ""}`}
            >
              {renamingConvId === conv.id ? (
                <input
                  className="session-rename"
                  value={convDraft}
                  onChange={(event) => setConvDraft(event.target.value)}
                  onBlur={() => void saveConvRename(conv.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void saveConvRename(conv.id);
                    if (event.key === "Escape") cancelConvRename();
                  }}
                  autoFocus
                />
              ) : (
                <button
                  type="button"
                  className="session-title"
                  title={conv.title}
                  onClick={() => void switchConversation(conv.id)}
                >
                  {conv.title}
                </button>
              )}

              <span className="session-actions">
                <button
                  type="button"
                  title="Rename conversation"
                  onClick={() => startConvRename(conv)}
                >
                  ✎️
                </button>
                <button
                  type="button"
                  title="Delete conversation"
                  onClick={() => void deleteConvSession(conv)}
                >
                  🗑️
                </button>
              </span>
            </div>
          ))}
        </div>
      </nav>

      {/* Chat Panel */}
      <section className="chat-panel">
        <div className="chat-toolbar">
          <div className="chat-toolbar-id">
            <span className="eyebrow">
              LAB MEMBER 004
            </span>

            <h2>
              {conversations.find((c) => c.id === activeConvId)?.title ?? "Conversation"}
            </h2>
          </div>

          <div className="toolbar-actions">
            <button
              className="ghost-button"
              onClick={() => setSettingsOpen(true)}
            >
              Settings
            </button>

            <button
              className="ghost-button danger"
              onClick={clearMemory}
            >
              Reset memory
            </button>
          </div>
        </div>

        {/* Messages */}
        <div className="messages">
          {messages.length === 0 && (
            <div className="empty-state">
              <span>
                AMADEUS SYSTEM READY
              </span>

              <h3>
                Start a conversation.
              </h3>

              <p>
                Your existing Flask backend and memory system
                are still doing the actual work.
              </p>
            </div>
          )}

          {messages.map((message, index) => (
            <article
              key={`${message.id ?? "message"}-${index}`}
              className={`message ${
                message.role === "user"
                  ? "user"
                  : "assistant"
              }`}
            >
              <div className="message-meta">
                {message.role === "user"
                  ? "YOU"
                  : "AMADEUS"}

                {message.created_at && (
                  <time>
                    {message.created_at}
                  </time>
                )}

                <span className="message-actions">
                  {message.role !== "user" &&
                    (message.has_japanese || message.audio_url) &&
                    !loading && (
                      <button
                        type="button"
                        title={
                          message.audio_url
                            ? "Replay voice line"
                            : "Voice was cleared — it will be re-synthesized"
                        }
                        onClick={() => void replayVoice(message)}
                      >
                        🔊 Replay
                      </button>
                    )}

                  {message.role !== "user" &&
                    (message.total_versions ?? 1) > 1 &&
                    !loading && (
                      <span
                        className="version-nav"
                        title="This reply has several versions"
                      >
                        <button
                          type="button"
                          disabled={(message.version ?? 1) <= 1}
                          onClick={() => void switchReplyVersion(message, -1)}
                        >
                          ◀
                        </button>
                        <span className="version-count">
                          {message.version}/{message.total_versions}
                        </span>
                        <button
                          type="button"
                          disabled={(message.version ?? 1) >= (message.total_versions ?? 1)}
                          onClick={() => void switchReplyVersion(message, 1)}
                        >
                          ▶
                        </button>
                      </span>
                    )}

                  {message.id != null && !loading && editingId !== message.id && (
                    <>
                      <button
                        type="button"
                        title="Edit this message"
                        onClick={() => startEdit(message)}
                      >
                        ✏️ Edit
                      </button>

                      <button
                        type="button"
                        title="Delete this message"
                        onClick={() => void removeMessage(message)}
                      >
                        🗑️ Delete
                      </button>
                    </>
                  )}

                  {/* A startup greeting has no user message behind it: Regenerate
                      and Undo are no-ops for it, so the buttons stay hidden (Edit
                      and Delete still work on the line itself). */}
                  {message.role !== "user" && index === lastAssistantIndex && !loading &&
                    !message.is_greeting && (
                    <>
                      <button
                        type="button"
                        title="Make her answer this again"
                        onClick={() => void handleRegenerate()}
                      >
                        ↻ Regenerate
                      </button>

                      <button
                        type="button"
                        title="Remove her last reply so you can rephrase"
                        onClick={() => void handleUndo()}
                      >
                        ↩️ Undo
                      </button>
                    </>
                  )}
                </span>
              </div>

              {editingId === message.id ? (
                <div className="edit-box">
                  <textarea
                    value={editDraft}
                    onChange={(event) => setEditDraft(event.target.value)}
                    rows={3}
                  />
                  <div className="edit-actions">
                    <button
                      type="button"
                      onClick={() => void saveEdit(message.id!)}
                    >
                      Save
                    </button>
                    <button type="button" onClick={cancelEdit}>
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div className="bubble">
                  {message.content}
                </div>
              )}
            </article>
          ))}

          {/* Typing Indicator */}
          {loading && (
            <article className="message assistant">
              <div className="message-meta">
                AMADEUS
              </div>

              <div className="bubble typing">
                <i />
                <i />
                <i />
              </div>
            </article>
          )}

          <div ref={bottomRef} />
        </div>

        {/* Message Input */}
        <form
          className="composer"
          onSubmit={submit}
        >
          <textarea
            value={input}
            onChange={(event) => {
              setInput(event.target.value);
            }}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey
              ) {
                event.preventDefault();

                event.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder="Message Amadeus..."
            rows={1}
          />

          <button
            type="button"
            className={`web-toggle${webAccess ? " on" : ""}`}
            onClick={toggleWebAccess}
            disabled={webToggling || webAccess === null}
            aria-pressed={!!webAccess}
            title={
              webAccess
                ? "Web access is ON — she can search the internet for fresh information"
                : "Web access is OFF — she only uses her own knowledge"
            }
          >
            <span className="web-toggle-globe" aria-hidden="true">🌐</span>
            <span>{webAccess ? "Web on" : "Web off"}</span>
          </button>

          <button
            type="submit"
            disabled={!input.trim() || loading}
          >
            Send
          </button>
        </form>
        <div className="build-label">
          <span className="build-dot" aria-hidden="true" />
          DEVELOPER BUILD
        </div>
      </section>

      {/* Settings Modal */}
      {settingsOpen && (
        <div
          className="modal-backdrop"
          onMouseDown={closeSettings}
        >
          <div
            className="modal settings-modal"
            ref={modalRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="settings-title"
            tabIndex={-1}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                closeSettings();
              }
              if (event.key === "Tab") {
                const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
                  'button:not(:disabled), input:not(:disabled), textarea:not(:disabled)'
                )).filter((element) => element.getClientRects().length > 0);
                const first = controls[0];
                const last = controls[controls.length - 1];
                if (event.shiftKey && (document.activeElement === first || document.activeElement === event.currentTarget)) {
                  event.preventDefault(); last?.focus();
                } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === event.currentTarget)) {
                  event.preventDefault(); first?.focus();
                }
              }
            }}
            onMouseDown={(event) => {
              event.stopPropagation();
            }}
          >
            <div className="modal-heading">
              <div>
                <span className="eyebrow">
                  SYSTEM CONFIGURATION
                </span>

                <h3 id="settings-title">
                  Settings
                </h3>
              </div>

              <button
                className="close-button"
                aria-label="Close settings"
                onClick={closeSettings}
                disabled={settingsBusy}
              >
                ×
              </button>
            </div>

            <nav className="settings-sections" aria-label="Settings sections">
              <button type="button" aria-pressed={settingsSection === "connection"}
                onClick={() => setSettingsSection("connection")}>Connection</button>
              <button type="button" aria-pressed={settingsSection === "personality"}
                onClick={() => setSettingsSection("personality")}>
                Personality{personalityDirty && <span className="unsaved-dot" aria-label="Unsaved changes" />}
              </button>
              <button type="button" aria-pressed={settingsSection === "sampling"}
                onClick={() => setSettingsSection("sampling")}>
                Model Sampling
              </button>
            </nav>

            <div hidden={settingsSection !== "connection"}>
              <p className="settings-intro">
                Amadeus works with any OpenAI-compatible model server - local (Unsloth
                Desktop, Ollama, llama.cpp, LM Studio, vLLM) or cloud (OpenRouter,
                OpenAI, and similar). For a local server, leave the address blank to
                auto-detect it, or type the address your app shows (usually
                http://localhost:8888/v1). For a cloud server, paste its API base URL
                and your key below.
              </p>

              <label>
                Model server address
                <input
                  value={serverAddress}
                  disabled={settingsBusy}
                  onChange={(event) => setServerAddressState(event.target.value)}
                  placeholder="http://localhost:8888/v1  (blank = auto-detect)"
                  spellCheck={false}
                  autoComplete="off"
                  aria-describedby="server-address-help"
                />
              </label>
              <p className="settings-help" id="server-address-help">
                {serverAddress
                  ? "Requests go to this address."
                  : "Blank = auto-detect local servers on ports 8888 / 8000."}
                {" "}Press "Save connection" to apply a change.
              </p>

              <label>
                API key
                <input
                  type="password"
                  value={apiKey}
                  onChange={(event) => setApiKeyInput(event.target.value)}
                  placeholder={hasApiKey ? "Enter a replacement key" : "Enter your API key"}
                  autoComplete="new-password"
                  spellCheck={false}
                  disabled={settingsBusy}
                  aria-describedby="api-key-help"
                />
              </label>
              <p className="settings-help" id="api-key-help">
                {hasApiKey ? "A key is saved. Leave blank to keep it." : "No API key is saved."}
                {" "}Local servers usually accept any value; cloud providers (OpenRouter,
                OpenAI) require your real key.
              </p>

              <label>
                LLM model

                <input
                  value={model}
                  disabled={settingsBusy}
                  onChange={(event) => {
                    setModelName(event.target.value);
                  }}
                  placeholder="your model name"
                />
              </label>

              {connStatus && connStatus.reachable && connStatus.models.length > 0 && (
                <div className="model-chips">
                  <span className="model-chips-label">Models on the server:</span>
                  {connStatus.models.map((name) => (
                    <button
                      key={name}
                      type="button"
                      className={baseModelName(model) === baseModelName(name) ? "active" : ""}
                      disabled={settingsBusy}
                      title="Use this model"
                      onClick={() => setModelName(name)}
                    >
                      {name}
                    </button>
                  ))}
                </div>
              )}

              <div
                className={`conn-status${
                  !connStatus
                    ? ""
                    : !connStatus.reachable
                      ? " fail"
                      : connStatus.model_configured && !connStatus.model_found
                        ? " warn"
                        : " ok"
                }`}
                role="status"
              >
                <span className="conn-dot" aria-hidden="true" />
                <span className="conn-text">{connStatusText}</span>
                <button
                  type="button"
                  className="ghost-button"
                  onClick={() => void handleTestConnection()}
                  disabled={connTesting || settingsBusy}
                >
                  {connTesting ? "Testing..." : "Test connection"}
                </button>
              </div>

              <label>
                Keep last voice recordings
                <input
                  type="number"
                  min={0}
                  step={1}
                  value={voiceRetention}
                  disabled={settingsBusy}
                  aria-describedby="voice-retention-help"
                  onChange={(event) => {
                    const value = Number(event.target.value);
                    setVoiceRetentionState(
                      Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0
                    );
                  }}
                />
              </label>
              <p className="settings-help" id="voice-retention-help">
                How many voice recordings to keep on disk. 0 keeps everything.
                Older ones are deleted automatically, but any reply can always be
                replayed — its line is re-synthesized on demand.
              </p>

              <label>
                Conversation memory (tokens)
                <input
                  type="number"
                  min={500}
                  max={1000000}
                  step={500}
                  value={contextBudget}
                  disabled={settingsBusy}
                  aria-describedby="context-budget-help"
                  onChange={(event) => {
                    const value = Number(event.target.value);
                    setContextBudgetState(
                      Number.isFinite(value) ? Math.max(0, Math.floor(value)) : 0
                    );
                  }}
                />
              </label>
              <p className="settings-help" id="context-budget-help">
                How much recent conversation she keeps in each prompt, in
                estimated tokens. Lower = remembers less but replies faster and
                uses less memory — good for small local models. Default is
                40000. The model's context window should hold this number plus
                about 6000.
              </p>

              <div className="settings-toggle-row" aria-label="Deep thinking">
                <div className="settings-toggle-text">
                  <span className="settings-toggle-title">Deep thinking</span>
                  <span className="settings-toggle-desc">
                    Lets her reason before deciding to search the web. Slower,
                    so off by default. Only affects messages while web access is on.
                  </span>
                </div>
                <button
                  type="button"
                  className={`settings-toggle${deepThinking ? " on" : ""}`}
                  onClick={toggleDeepThinking}
                  disabled={deepThinkingBusy || deepThinking === null}
                  aria-pressed={!!deepThinking}
                >
                  <span className="settings-toggle-knob" aria-hidden="true" />
                  <span>{deepThinking ? "On" : "Off"}</span>
                </button>
              </div>

              {statsInfo.length > 0 && (
                <div className="stats-block" aria-label="Relationship stats">
                  <p className="stats-intro">
                    How it works: closeness shifts a little at a time across many
                    messages. Warm, friendly chats slowly raise it a few points; tense
                    or rude ones lower it. Higher levels unlock warmer, more playful
                    conversation.
                  </p>
                  {statsInfo.map((s) => (
                    <div key={s.key} className="stat-row">
                      <div className="stat-line">
                        <span className="stat-label">{s.label}</span>
                        <span className="stat-value">
                          {Math.round(s.value)}/{s.max}{s.tier ? ` — ${s.tier}` : ""}
                        </span>
                      </div>
                      {s.description && <p className="settings-help">{s.description}</p>}
                    </div>
                  ))}
                </div>
              )}

              {settingsError && <p className="settings-error" role="alert">{settingsError}</p>}
              {settingsNotice && <p className="settings-success" role="status">{settingsNotice}</p>}

              <div className="modal-actions">
                <button
                  className="ghost-button"
                  onClick={closeSettings}
                  disabled={settingsBusy}
                >
                  Close
                </button>

                <button
                  className="primary-button"
                  onClick={saveModel}
                  disabled={settingsBusy || loading}
                >
                  {savingSettings ? "Saving..." : "Save connection"}
                </button>
              </div>
            </div>

            <section hidden={settingsSection !== "personality"} aria-label="Personality editor">
              <p className="personality-intro" id="personality-help">
                Shape how Amadeus speaks and responds. Edit the current personality below.
              </p>
              {personalityLoading ? <p className="settings-help" role="status">Loading personality...</p> : (
                <>
                  {personalityLoaded && <>
                    <label htmlFor="personality-text">Personality instructions</label>
                    <textarea id="personality-text" className="personality-text"
                      value={personality} disabled={personalitySaving}
                      aria-describedby="personality-help personality-hint"
                      placeholder="Describe Amadeus’s personality, tone, and behavior..."
                      onChange={(event) => {
                        setPersonalityText(event.target.value);
                        setPersonalityNotice("");
                        setPersonalityError("");
                      }} />
                    <div className="personality-meta">
                      <span>{personalityDirty ? "Unsaved changes" : "Current personality"}</span>
                      <span>{personality.length.toLocaleString()} characters</span>
                    </div>
                    <p className="settings-help" id="personality-hint">
                      Saved changes apply to your next message. Your conversation memory stays intact.
                    </p>
                    {personalityDirty && !personality.trim() && <p className="settings-help">Enter a personality before saving.</p>}
                  </>}
                  {personalityError && <p className="settings-error" role="alert">{personalityError}</p>}
                  {personalityNotice && <p className="settings-success" role="status">{personalityNotice}</p>}
                  <div className="modal-actions personality-actions">
                    {!personalityLoaded ? <button className="ghost-button"
                      onClick={() => setPersonalityReload((value) => value + 1)}>Retry loading</button> : <>
                      <button className="ghost-button" disabled={settingsBusy || !personalityDirty}
                        onClick={() => {
                          setPersonalityText(savedPersonality);
                          setPersonalityError("");
                          setPersonalityNotice("");
                        }}>Discard changes</button>
                      <button className="primary-button" onClick={() => void savePersonality()}
                        disabled={settingsBusy || loading || !personalityDirty || !personality.trim()}>
                        {personalitySaving ? "Saving..." : "Save personality"}
                      </button>
                    </>}
                  </div>
                </>
              )}
            </section>

            <section hidden={settingsSection !== "sampling"} aria-label="Model sampling">
              <SamplingSettings busy={settingsBusy || loading} />
            </section>
          </div>
        </div>
      )}
    </main>
  );
}
