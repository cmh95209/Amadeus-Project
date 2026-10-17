import { FormEvent, useEffect, useRef, useState } from "react";
import Live2DCharacter from "./components/Live2DCharacter";
import SamplingSettings from "./components/SamplingSettings";
import { deriveFooter, placeGreeting, isCeremonyWaitLine, waitUntilQuiet } from "./greetingFlow";
import type { GreetPhase } from "./greetingFlow";
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
  getConversationGreeting,
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
  regenerateGreeting,
  sendPresence,
  activateReplyVersion,
  regenerateMessageVoice,
  getVoiceRetention,
  setVoiceRetention,
  getContextBudgetInfo,
  getBrainMode,
  setBrainMode,
  setContextBudget,
  getLLMServer,
  setLLMServer,
  getUsername,
  setUsername,
  suggestUsernameReading,
  suggestNameReading,
  getUserProfile,
  setUserProfile,
  testConnection,
  ConnectionStatus,
  BrainMode,
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

// Settings choices for the "about you" section.
const PRONOUN_PRESETS = ["he/him", "she/her", "they/them"];
// [value, label]; labels show the user's own name ("Alex" until one is set).
function addressOptions(name: string): [string, string][] {
  const n = name.trim() || "Alex";
  return [
    ["auto", "Let her decide (changes with trust)"],
    ["san", `さん — polite (${n}-san)`],
    ["kun", `くん — friendly (${n}-kun)`],
    ["chan", `ちゃん — cute / affectionate (${n}-chan)`],
    ["none", `No ending — very familiar (${n})`],
    ["senpai", `先輩 — playful "senior" (${n}-senpai)`],
    ["sama", `様 — over-the-top formal (${n}-sama)`],
    ["custom", "Custom…"],
  ];
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
  // Display-only, so it lives in this browser (no backend setting file).
  const [showJapanese, setShowJapanese] = useState<boolean>(() => {
    try {
      return localStorage.getItem("amadeusShowJapanese") === "1";
    } catch {
      return false;
    }
  });
  const [editSaving, setEditSaving] = useState(false);
  const [voiceRetention, setVoiceRetentionState] = useState<number>(100);
  const [contextBudget, setContextBudgetState] = useState<number>(40000);
  const [serverAddress, setServerAddressState] = useState<string>("");
  const [userName, setUserName] = useState("");
  const [userNameReading, setUserNameReading] = useState("");
  const [readingSuggesting, setReadingSuggesting] = useState(false);
  const [readingNote, setReadingNote] = useState("");
  // Pronouns: "" (not set), a preset, or "custom" + pronounsCustom.
  const [pronounsChoice, setPronounsChoice] = useState("");
  const [pronounsCustom, setPronounsCustom] = useState("");
  // What she calls you: "auto" (let her decide), a preset key, or "custom".
  const [addressChoice, setAddressChoice] = useState("auto");
  const [addressCustom, setAddressCustom] = useState("");
  const [connStatus, setConnStatus] = useState<ConnectionStatus | null>(null);
  // How she thinks (built-in model vs the user's own); null = unknown.
  const [brain, setBrain] = useState<BrainMode | null>(null);
  const [brainSwitching, setBrainSwitching] = useState(false);
  const builtinMode = brain?.mode === "builtin";
  // The built-in model's cap on the history budget (null = no cap).
  const [builtinBudgetCap, setBuiltinBudgetCap] = useState<number | null>(null);
  const [connTesting, setConnTesting] = useState(false);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("Connecting to Amadeus...");
  // The footer's health inputs (deriveFooter): is the backend answering at
  // all, and where the startup greeting stands. A refresh that already
  // greeted this session starts settled ("done").
  const [backendDown, setBackendDown] = useState(false);
  const [greetPhase, setGreetPhase] = useState<GreetPhase>(() =>
    typeof sessionStorage !== "undefined" && sessionStorage.getItem("amadeusGreeted") === "1"
      ? "done" : "idle");
  // When the AI server first answered WITHOUT listing the configured model
  // (null = not in that state): "loading..." for a grace period, then
  // "check the model name".
  const notListedSince = useRef<number | null>(null);
  const slowGreetTimer = useRef<number | null>(null);
  // How the startup load (initialize) went. If it FAILED (backend down at
  // launch), the connection probe re-runs it once the backend answers.
  const initState = useRef<"pending" | "ok" | "failed">("pending");
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
  const footer = deriveFooter({
    status,
    backendDown,
    missingKey,
    conn: connStatus,
    notListedForMs: notListedSince.current === null ? null : Date.now() - notListedSince.current,
    greetPhase,
  });
  const connStatusText = !connStatus
    ? "Not tested yet."
    : connStatus.builtin
      ? connStatus.builtin_model_missing
        ? "Her built-in model is not installed yet - run the installer again (it continues where it stopped)."
        : !connStatus.reachable
          ? "Her built-in model is not running. Restart Amadeus to start it."
          : connStatus.loading || !connStatus.model_found
            ? "Her built-in model is loading..."
            : "Her built-in model is running."
    : !connStatus.reachable
      ? `Can't reach ${connStatus.address}. ${connStatus.error ? connStatus.error : "Is the server running?"}`
      : connStatus.model_configured
        ? connStatus.model_found
          ? `Connected to ${connStatus.address} - model "${connStatus.configured_model}" found.${connStatus.batch_warning ? ` ${connStatus.batch_warning}` : ""}`
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
  const switchGreetingInFlight = useRef<boolean>(false);
  // Bumped on every message the user sends. A greeting compares it before
  // and after its request: a change means the user spoke while she was
  // writing, so a late line is slotted above their message and stays quiet
  // (her reply to them speaks instead).
  const userSpokeSeq = useRef<number>(0);
  // First-launch ceremony (the backend /greet drives it): where she stands
  // per the last /greet report - null until the first report, "intro" /
  // "connecting" while she is still coming online, "done" when the
  // ceremony finished (the normal app from then on, forever). While the
  // ceremony is unfinished the chat box stays locked: she cannot answer
  // until she has generated her first line.
  const [ceremonyState, setCeremonyState] = useState<string | null>(null);
  const ceremonyPending = ceremonyState !== null && ceremonyState !== "done";
  const ceremonyPendingRef = useRef<boolean>(false);
  ceremonyPendingRef.current = ceremonyPending;

  useEffect(() => {
    void initialize();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({
      behavior: "smooth",
    });
  }, [messages, loading]);

  useEffect(() => {
    try {
      localStorage.setItem("amadeusShowJapanese", showJapanese ? "1" : "0");
    } catch {
      // Storage blocked: the switch still works for this visit.
    }
  }, [showJapanese]);

  // Chat replies arrive with her Japanese line, but greetings and touch
  // reactions are added locally without it. While the Japanese line is
  // shown, pick those up once from the server's copy (each id only once,
  // so a line that really has no Japanese never causes a reload loop).
  const japaneseFetched = useRef<Set<number>>(new Set());
  useEffect(() => {
    if (!showJapanese || loading || editingId !== null) return;
    const missing = messages.filter(
      (m) => m.role !== "user" && m.id != null && m.japanese === undefined &&
        !japaneseFetched.current.has(m.id)
    );
    if (missing.length === 0) return;
    missing.forEach((m) => japaneseFetched.current.add(m.id!));
    const shownConv = displayedConvIdRef.current;
    void getMemory()
      .then((fresh) => {
        if (displayedConvIdRef.current !== shownConv) return; // tab moved on
        const byId = new Map(fresh.map((m) => [m.id, m.japanese]));
        setMessages((current) =>
          current.map((m) =>
            m.id != null && m.japanese === undefined && byId.has(m.id)
              ? { ...m, japanese: byId.get(m.id) ?? "" }
              : m
          )
        );
      })
      .catch(() => undefined);
  }, [showJapanese, loading, editingId, messages]);

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

  // The footer follows the greeting (deriveFooter): "waking up" while it is
  // written, "still waking up" after 15 s (a model that loads on the first
  // request), then Ready. A failure on a reachable model gets ONE automatic
  // retry 10 s later; if that fails too the footer says she couldn't greet
  // but the user can talk - never a silent "is it even working?".
  function settleGreetPhase(phase: GreetPhase) {
    if (slowGreetTimer.current !== null) {
      window.clearTimeout(slowGreetTimer.current);
      slowGreetTimer.current = null;
    }
    setGreetPhase(phase);
  }

  async function fireGreeting(justSaved = false, isRetry = false) {
    if (greetingInFlight.current || greetedThisSession.current) return;
    if (loading) {
      // Do not interrupt an in-flight turn. A retry that cannot run must
      // still settle the footer (her reply will prove the line works).
      if (isRetry) settleGreetPhase("failed");
      return;
    }
    greetingInFlight.current = true;
    const spokeBefore = userSpokeSeq.current;
    setGreetPhase((p) => (p === "slow" ? p : "waking"));
    if (slowGreetTimer.current === null) {
      slowGreetTimer.current = window.setTimeout(() => {
        slowGreetTimer.current = null;
        setGreetPhase((p) => (p === "waking" ? "slow" : p));
      }, 15000);
    }
    let failed = false;
    try {
      const g = await getGreeting(justSaved);
      // A first-launch line never cuts off the one she is still saying
      // (e.g. the built-in model got ready mid-intro): wait until she has
      // finished before it is shown, voiced, or unlocks the chat box. The
      // backend has already stored it.
      if (g.kind !== undefined && g.response) {
        await waitUntilQuiet(() => characterRef.current?.isSpeechBusy() ?? false);
      }
      if (g.ceremony) setCeremonyState(g.ceremony); // gate + note follow this
      if (g.superseded) {
        // The user spoke while she was writing: the backend dropped the
        // line, and the conversation is already going - no re-fire.
        greetedThisSession.current = true;
        sessionStorage.setItem("amadeusGreeted", "1");
        settleGreetPhase("done");
        return;
      }
      const line = g.response;
      if (!line || (!g.ready && !isCeremonyWaitLine(g.kind, g.wait))) {
        // Nothing to say this probe (model not servable yet, or the
        // ceremony is waiting): back to idle, and let the next probe that
        // finds the model re-fire it.
        modelWasFound.current = false;
        settleGreetPhase("idle");
        return;
      }
      // The ceremony wait-lines (intro / trying / reminder) are spoken but
      // do NOT finish the ceremony - they keep the box locked and the
      // probes going. A wake-up, the fallback, or an ordinary greeting
      // ends it and unlocks the box (and counts as "greeted" for this
      // session, so a refresh never re-fires it). The wait-lines arrive with
      // ready=false (the model is down by definition on a fresh install) and
      // still must be shown: they are what teach a first-time user to
      // connect their model.
      const ceremonialWait = isCeremonyWaitLine(g.kind, g.wait);
      if (!ceremonialWait) {
        greetedThisSession.current = true;
        sessionStorage.setItem("amadeusGreeted", "1");
      }
      settleGreetPhase(ceremonialWait ? "idle" : "done");
      // Scripted first-launch lines (intro / trying / reminder / fallback,
      // and their built-in variants) can never be regenerated; ordinary
      // greetings and the wake-up can.
      const regenerable = g.kind === undefined || g.kind === "wake";
      setMessages((current) => placeGreeting(current, {
        role: "assistant", content: line, id: g.assistantId, is_greeting: true,
        greeting_regenerable: regenerable,
      }));
      if (g.speechUrl) rememberVoiceLine(g.assistantId); // the Replay button
      // The user spoke while she was writing: the line is shown (above
      // their message) but not voiced - her reply to them speaks instead.
      if (userSpokeSeq.current !== spokeBefore) return;
      // She animates even when the browser blocks audio on a fresh load.
      characterRef.current?.playMotion("TapReaction");
      if (g.speechUrl) {
        void characterRef.current?.playSpeech(g.speechUrl).catch(() => undefined);
      }
    } catch {
      // A greeting failure never shows as an error popup; the footer says
      // what happened instead (after the one retry).
      failed = true;
    } finally {
      greetingInFlight.current = false;
    }
    if (failed) {
      if (isRetry) {
        settleGreetPhase("failed");
      } else {
        window.setTimeout(() => void fireGreeting(false, true), 10000);
      }
    }
  }

  // A switch greeting is aimed at one tab: fired on every switch (no
  // cooldown), stored as its own greeting line in THAT tab (even if the
  // user hops on before it lands - it then shows on return), and silent on
  // failure (a tab switch must never break). previousId is the tab the user
  // left, so she can name the topic change. The is_greeting flag already
  // gives it its own line in the UI (no version arrows, Regenerate/Undo
  // hidden).
  async function fireSwitchGreeting(convId: number, previousId: number | null) {
    if (switchGreetingInFlight.current || loading) return;
    switchGreetingInFlight.current = true;
    const spokeBefore = userSpokeSeq.current;
    try {
      const g = await getConversationGreeting(convId, previousId);
      // Gated, model not up, or the user spoke first (superseded): no line.
      if (!g.ready || !g.response) return;
      if (displayedConvIdRef.current !== convId) return; // tab moved on; drop
      const line = g.response;
      setMessages((current) => placeGreeting(current, {
        role: "assistant", content: line, id: g.assistantId, is_greeting: true,
        greeting_regenerable: true,
      }));
      if (g.speechUrl) rememberVoiceLine(g.assistantId);
      if (userSpokeSeq.current !== spokeBefore) return; // her reply speaks
      characterRef.current?.playMotion("TapReaction");
      if (g.speechUrl) {
        void characterRef.current?.playSpeech(g.speechUrl).catch(() => undefined);
      }
    } catch {
      // A switch-greeting failure is silent by design - never breaks the switch.
    } finally {
      switchGreetingInFlight.current = false;
    }
  }

  async function refreshConnection() {
    try {
      const status = await testConnection();
      setConnStatus(status);
      setBackendDown(false);
      if (initState.current === "failed") { // backend came back
        initState.current = "pending";
        void initialize();
      }
      // Reachable but the configured model is not listed (yet): remember
      // since when - "loading..." for a grace period, then "check the name".
      if (status.reachable && !status.loading && status.model_configured && !status.model_found) {
        if (notListedSince.current === null) notListedSince.current = Date.now();
      } else {
        notListedSince.current = null;
      }
      const found = status.reachable && status.model_found;
      // Two re-fires: the classic one (the model just became servable -
      // the "I forgot to start it" case), and the ceremony one (while she
      // is still coming online the probe keeps asking /greet every 30 s -
      // the backend answers with a new line when it has one, or with
      // silence).
      const ceremonialWait = ceremonyPendingRef.current && !greetedThisSession.current;
      if (!greetedThisSession.current &&
          ((found && modelWasFound.current !== true) || ceremonialWait)) {
        void fireGreeting();
      }
      modelWasFound.current = found;
    } catch {
      setConnStatus(null); // even the backend is unreachable
      setBackendDown(true);
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

  // "Last seen" presence: ping every 5 minutes while the window is visible,
  // and the moment it comes back into view. The app-open itself is recorded
  // by the greeting request (after it measured the previous visit); a
  // refresh that already greeted this session pings right away instead.
  useEffect(() => {
    const ping = () => {
      if (document.visibilityState === "visible") {
        void sendPresence(displayedConvIdRef.current);
      }
    };
    if (greetedThisSession.current) ping();
    const id = window.setInterval(ping, 5 * 60 * 1000);
    document.addEventListener("visibilitychange", ping);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", ping);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // While the AI server is loading the model (or has not listed it yet),
  // check every 5 s instead of every 30 s, so the footer flips to Ready
  // as soon as it can.
  useEffect(() => {
    const loadingish = !!connStatus && connStatus.reachable &&
      (connStatus.loading === true || (connStatus.model_configured && !connStatus.model_found));
    if (!loadingish) return;
    const id = window.setTimeout(() => void refreshConnection(), 5000);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connStatus]);

  // One-off notices ("Conversation renamed", an audio hint, ...) settle back
  // after a few seconds so the footer returns to the app's real state.
  // Running actions (texts ending in "...") stay until their code clears them.
  useEffect(() => {
    if (status === "Online" || status.endsWith("...")) return;
    const shown = status;
    const id = window.setTimeout(() => {
      setStatus((current) => (current === shown ? "Online" : current));
    }, 8000);
    return () => window.clearTimeout(id);
  }, [status]);

  async function handleTestConnection() {
    if (connTesting) return;
    setConnTesting(true);
    try {
      // Apply the address being edited first, so the test matches what
      // "Save connection" would store (not in built-in mode: the address
      // field is hidden there and the test checks her built-in model).
      if (!builtinMode) await setLLMServer(serverAddress);
      setConnStatus(await testConnection());
    } catch {
      setConnStatus(null);
    } finally {
      setConnTesting(false);
    }
  }

  async function initialize() {
    try {
      const [memory, currentModel, configured, webOn, deepThinkingOn, convs, voiceCap, budgetInfo, serverAddr, savedName, profile, brainInfo] =
        await Promise.all([
          getMemory(),
          getCurrentModel(),
          getApiKeyStatus(),
          getWebAccess().catch(() => true),
          getDeepThinking().catch(() => false),
          listConversations().catch(() => null),
          getVoiceRetention().catch(() => 100),
          getContextBudgetInfo().catch(() => ({ budget: 40000, builtinCap: null })),
          getLLMServer().catch(() => ""),
          getUsername().catch(() => ({ name: "", reading: "" })),
          getUserProfile().catch(() => ({ pronouns: "", address: "auto" })),
          getBrainMode().catch(() => null),
        ]);

      setMessages(memory);
      setModelName(currentModel === "No Model Selected." ? "" : currentModel);
      setHasApiKey(configured);
      setWebAccessState(webOn);
      setDeepThinkingState(deepThinkingOn);
      setVoiceRetentionState(voiceCap);
      setContextBudgetState(budgetInfo.budget);
      setBuiltinBudgetCap(budgetInfo.builtinCap);
      setBrain(brainInfo);
      setServerAddressState(serverAddr);
      setUserName(savedName.name);
      setUserNameReading(savedName.reading);
      if (!profile.pronouns || PRONOUN_PRESETS.includes(profile.pronouns)) {
        setPronounsChoice(profile.pronouns);
      } else {
        setPronounsChoice("custom");
        setPronounsCustom(profile.pronouns);
      }
      if (profile.address.startsWith("custom:")) {
        setAddressChoice("custom");
        setAddressCustom(profile.address.slice("custom:".length));
      } else {
        setAddressChoice(profile.address || "auto");
      }
      if (convs) {
        setConversations(convs.conversations);
        setActiveConvId(convs.active_id);
        // The startup /getMemory and /conversations come from the same
        // backend state, so the pane is showing the active conversation.
        setDisplayedConvId(convs.active_id);
      }
      setStatus("Online");
      setBackendDown(false);
      initState.current = "ok";
      // Startup greeting: fires once now if the model is already servable,
      // and again (still once per session) the instant it comes up. Never
      // an error popup - the footer reports how it went.
      void fireGreeting();
    } catch {
      // The footer says "Backend not running"; the connection probe re-runs
      // this the moment the backend answers again.
      initState.current = "failed";
      setBackendDown(true);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();

    const text = input.trim();

    if (!text || loading) {
      return;
    }

    // First-launch ceremony: she cannot reply until she has come online
    // (her wake-up or fallback line has landed). The box below says why.
    if (ceremonyPending) {
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

    userSpokeSeq.current += 1;
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
      const turn = await sendMessage(text);
      const reply = turn.reply;

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

      // Voice-first (web OFF): the bubble appears as soon as her voice can
      // start; its text is still empty and is filled by turn.text below.
      // Web ON: reply.response already carries the full line.
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
          japanese: reply.japanese,
          id: reply.assistantId,
        });
        return next;
      });

      setStatus("Online");
      // A real reply proves the line works: an unsettled or failed startup
      // greeting no longer needs to be reported.
      settleGreetPhase("done");
      if (reply.speechUrl && await speechReady) {
        void characterRef.current?.playSpeech(reply.speechUrl).catch((error) => {
          setStatus(error instanceof Error ? error.message : "Speech playback failed");
        });
        rememberVoiceLine(reply.assistantId);
      } else if (reply.speechUrl) {
        setStatus("Audio could not start. Check browser audio permissions and send again.");
      }

      // The English display line. For web-ON replies it already landed with
      // the reply (setting it again is a no-op); for voice-first replies it
      // arrives a moment after she starts speaking. If it never arrives the
      // stream was cut - the backend still stores it, so pick the turn up
      // from the server's copy instead of showing an empty bubble.
      void turn.text
        .then(async (english) => {
          if (english) {
            setMessages((current) => {
              const next = [...current];
              for (let i = next.length - 1; i >= 0; i--) {
                if (next[i].role === "assistant" && next[i].id === reply.assistantId) {
                  next[i] = { ...next[i], content: english };
                  break;
                }
              }
              return next;
            });
          } else {
            const fresh = await getMemory();
            setMessages(fresh);
          }
        })
        .catch(() => {
          // The turn already failed loudly in the catch below; the text
          // promise just never settles usefully.
        });
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

  // Settings "Suggest": a reading for the name as typed, right now (nothing
  // is saved - Save stores the box, and then makes no model call itself).
  async function handleSuggestReading() {
    const name = userName.trim();
    if (!name || readingSuggesting) return;
    setReadingSuggesting(true);
    setReadingNote("");
    try {
      const reading = await suggestNameReading(name);
      if (reading) {
        setUserNameReading(reading);
      } else {
        setReadingNote("Couldn't suggest one right now - type it in katakana instead.");
      }
    } catch {
      setReadingNote("Couldn't suggest one right now - type it in katakana instead.");
    } finally {
      setReadingSuggesting(false);
    }
  }

  async function saveModel() {
    const nextModel = model.trim();

    if (settingsBusy || loading) return;
    // Built-in mode: her model, server and key come from the installer, so
    // Save stores only the rest (name, pronouns, ...) and never touches the
    // user's own model settings (kept for when they switch back).
    if (!nextModel && !builtinMode) {
      setSettingsError("Enter an LLM model.");
      return;
    }

    setSavingSettings(true);
    setSettingsError("");
    setSettingsNotice("");
    let keySaved = false;
    try {
      if (!builtinMode && apiKey.trim()) {
        await setApiKey(apiKey.trim());
        keySaved = true;
        setHasApiKey(true);
        setApiKeyInput("");
      }
      if (!builtinMode) await setModel(nextModel);
      const savedName = await setUsername(userName, userNameReading);
      await setVoiceRetention(voiceRetention);
      await setContextBudget(contextBudget);
      if (!builtinMode) await setLLMServer(serverAddress);
      // Saved last and never fatal: a backend older than this WebUI has no
      // /setUserProfile yet, and that must not undo the settings above.
      let profileSaved = true;
      try {
        await setUserProfile({
          pronouns: pronounsChoice === "custom" ? pronounsCustom.trim() : pronounsChoice,
          address:
            addressChoice === "custom"
              ? (addressCustom.trim() ? `custom:${addressCustom.trim()}` : "auto")
              : addressChoice,
        });
      } catch {
        profileSaved = false;
      }
      void refreshConnection();
      // A name with no reading yet: let the backend suggest how she says it
      // (one short model call). Never overwrites what the user typed meanwhile.
      if (savedName.name && !savedName.reading) {
        void suggestUsernameReading()
          .then((reading) => {
            if (reading) setUserNameReading((current) => current || reading);
          })
          .catch(() => undefined);
      }
      // The connection settings were JUST saved: during the first-launch
      // ceremony this is a fresh attempt - ask the backend to re-check the
      // line at once (she re-says "checking the line" / wakes up if the
      // model is already answering). Outside the ceremony this is a no-op
      // (already greeted this session, or the model not being servable is
      // the probe's job).
      void fireGreeting(true);

      setStatus(builtinMode ? "Settings saved" : `Model set to ${nextModel}`);
      setSettingsNotice(
        profileSaved
          ? "Connection settings saved."
          : "Connection settings saved, but pronouns / what she calls you were not (restart the backend, then save again)."
      );
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

  // Settings -> Connection: "Built-in" / "Custom Model". Saved at once; the
  // launcher starts (or stops) her built-in model only when Amadeus starts,
  // so the note asks for a restart.
  async function chooseBrain(mode: "builtin" | "custom") {
    if (brainSwitching || !brain || brain.mode === mode) return;
    if (mode === "builtin" && !brain.available) return;
    setBrainSwitching(true);
    setSettingsError("");
    setSettingsNotice("");
    try {
      const next = await setBrainMode(mode);
      setBrain(next);
      const budget = await getContextBudgetInfo().catch(() => null);
      if (budget) setBuiltinBudgetCap(budget.builtinCap);
      setSettingsNotice(
        mode === "builtin"
          ? "Switched to her built-in model. Restart Amadeus to start it."
          : "Switched to your own model. Restart Amadeus to free the memory her built-in model uses."
      );
      void refreshConnection();
    } catch (error) {
      setSettingsError(error instanceof Error ? error.message : "Could not switch.");
    } finally {
      setBrainSwitching(false);
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
    // Captured BEFORE the switch: once the target is activated the backend
    // can no longer tell which tab the user came from.
    const previousId = activeConvId;
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
      void fireSwitchGreeting(id, previousId);
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
    if (!text || editSaving) return;
    const isHers = messages.some((m) => m.id === id && m.role !== "user");
    setEditSaving(true);
    if (isHers) setStatus("Saving edit - rewriting her Japanese line...");
    try {
      const japanese = await editMessage(id, text);
      setMessages((current) =>
        current.map((m) => {
          if (m.id !== id) return m;
          if (japanese === undefined) return { ...m, content: text };
          // Her line changed: the old voice clip is gone; Replay re-speaks it.
          return { ...m, content: text, japanese, has_japanese: true, audio_url: undefined };
        })
      );
      setStatus("Message updated");
      cancelEdit();
    } catch (error) {
      // Keep the edit box open so the draft is not lost.
      setStatus(error instanceof Error ? error.message : "Could not save the edit");
    } finally {
      setEditSaving(false);
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

  // Her latest greeting, written again for the same moment. The old line is
  // kept as an earlier version (the version arrows appear after the reload).
  async function handleRegenerateGreeting(messageId: number) {
    if (loading) return;
    characterRef.current?.stopSpeech();
    const speechReady = characterRef.current?.prepareSpeech().then(
      () => true,
      () => false
    );
    setLoading(true);
    setStatus("Amadeus is rethinking her greeting...");
    try {
      const g = await regenerateGreeting(messageId);
      setMessages(await getMemory());
      setStatus("Online");
      if (g.ready && g.speechUrl && (await speechReady)) {
        void characterRef.current?.playSpeech(g.speechUrl).catch(() => undefined);
        rememberVoiceLine(g.assistantId);
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not regenerate her greeting");
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
            className={`status-dot ${footer.dot}${footer.pulse ? " pulse" : ""}`}
            title={
              connStatus && !connStatus.reachable
                ? `Model server offline: ${connStatus.address}`
                : connStatus?.reachable
                  ? `Model server online: ${connStatus.address}`
                  : "Checking model server..."
            }
          />

          <span>
            {footer.text}
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

                  {/* Her greeting has no user message behind it, so the reply
                      Regenerate/Undo don't apply. Instead, while it is the very
                      last line of the chat, it gets its own Regenerate (a new
                      version for the same moment); no Undo - Delete covers it.
                      Scripted first-launch lines never get the button. */}
                  {message.is_greeting && message.greeting_regenerable !== false &&
                    typeof message.id === "number" &&
                    index === messages.length - 1 && !loading && (
                    <button
                      type="button"
                      title="Have her greet you again, differently"
                      onClick={() => void handleRegenerateGreeting(message.id as number)}
                    >
                      ↻ Regenerate
                    </button>
                  )}
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
                      disabled={editSaving}
                      onClick={() => void saveEdit(message.id!)}
                    >
                      {editSaving ? "Saving..." : "Save"}
                    </button>
                    <button type="button" disabled={editSaving} onClick={cancelEdit}>
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div className="bubble">
                  {message.content}
                  {showJapanese && message.role !== "user" && message.japanese && (
                    <span className="bubble-japanese" lang="ja">
                      {message.japanese}
                    </span>
                  )}
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
            disabled={ceremonyPending}
            placeholder={ceremonyPending ? "..." : "Message Amadeus..."}
            rows={1}
            aria-disabled={ceremonyPending}
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
            disabled={!input.trim() || loading || ceremonyPending}
          >
            Send
          </button>
        </form>
        {ceremonyPending && (
          <p className="ceremony-note" role="status">
            She's still coming online — finish the setup in Settings.
          </p>
        )}
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
              {brain && (
                <div className="brain-choice" role="group" aria-label="How she thinks">
                  <span className="brain-choice-label">How she thinks</span>
                  <div className="brain-choice-buttons">
                    <button
                      type="button"
                      aria-pressed={builtinMode}
                      disabled={brainSwitching || settingsBusy || !brain.available}
                      title={brain.available ? "Her own model, running on this PC" : "Set up by the installer"}
                      onClick={() => void chooseBrain("builtin")}
                    >
                      Built-in
                    </button>
                    <button
                      type="button"
                      aria-pressed={!builtinMode}
                      disabled={brainSwitching || settingsBusy}
                      onClick={() => void chooseBrain("custom")}
                    >
                      Custom Model (your own local or Cloud model)
                    </button>
                  </div>
                  {!brain.available && (
                    <p className="settings-help">
                      Built-in is set up by the installer: run it again and
                      choose option A to give her a model that runs on this PC.
                    </p>
                  )}
                  {builtinMode && (
                    <p className="settings-help">
                      {brain.model_missing
                        ? `${brain.model_label || "Her built-in model"} is not installed yet - run the installer again (it continues the download where it stopped).`
                        : `She thinks with ${brain.model_label || "her built-in model"}, privately on this PC. Nothing to connect.`}
                    </p>
                  )}
                </div>
              )}

              {!builtinMode && (<>
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
              </>)}

              <label>
                Your name
                <input
                  value={userName}
                  disabled={settingsBusy}
                  onChange={(event) => {
                    setUserName(event.target.value);
                    // The reading belonged to the old name.
                    setUserNameReading("");
                    setReadingNote("");
                  }}
                  placeholder="what she should call you (optional)"
                  autoComplete="off"
                  spellCheck={false}
                  aria-describedby="your-name-help"
                />
              </label>
              <p className="settings-help" id="your-name-help">
                Optional. If you give her a name, she calls you by it. Leave
                blank to clear it.
              </p>

              <label>
                How she says it
                <div className="reading-row">
                  <input
                    value={userNameReading}
                    disabled={settingsBusy || !userName.trim()}
                    onChange={(event) => {
                      setUserNameReading(event.target.value);
                      setReadingNote("");
                    }}
                    placeholder={userName.trim() ? "press Suggest, or type katakana" : "set your name first"}
                    autoComplete="off"
                    spellCheck={false}
                    lang="ja"
                    aria-describedby="name-reading-help"
                  />
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() => void handleSuggestReading()}
                    disabled={settingsBusy || readingSuggesting || !userName.trim()}
                  >
                    {readingSuggesting ? "Suggesting…" : "Suggest"}
                  </button>
                </div>
              </label>
              {readingNote && <p className="settings-error" role="status">{readingNote}</p>}
              <p className="settings-help" id="name-reading-help">
                Her voice speaks Japanese, so your name is said from this
                katakana (e.g. Alex → アレックス). Press Suggest to fill it in,
                or leave it empty and it's suggested when you save. Change it
                if it sounds wrong.
              </p>

              <label>
                Your pronouns
                <select
                  value={pronounsChoice}
                  disabled={settingsBusy}
                  onChange={(event) => setPronounsChoice(event.target.value)}
                  aria-describedby="pronouns-help"
                >
                  <option value="">Not set</option>
                  {PRONOUN_PRESETS.map((p) => (
                    <option key={p} value={p}>{p}</option>
                  ))}
                  <option value="custom">Custom…</option>
                </select>
              </label>
              {pronounsChoice === "custom" && (
                <label>
                  Custom pronouns
                  <input
                    value={pronounsCustom}
                    disabled={settingsBusy}
                    onChange={(event) => setPronounsCustom(event.target.value)}
                    placeholder="e.g. xe/xem"
                    maxLength={40}
                    autoComplete="off"
                    spellCheck={false}
                  />
                </label>
              )}
              <p className="settings-help" id="pronouns-help">
                Optional. Used when she, her memory and the translation talk
                about you. Her spoken Japanese rarely needs them.
              </p>

              <label>
                What she calls you
                <select
                  value={addressChoice}
                  disabled={settingsBusy}
                  onChange={(event) => setAddressChoice(event.target.value)}
                  aria-describedby="address-help"
                >
                  {addressOptions(userName).map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </label>
              {addressChoice === "custom" && (
                <label>
                  Custom form of address
                  <input
                    value={addressCustom}
                    disabled={settingsBusy}
                    onChange={(event) => setAddressCustom(event.target.value)}
                    placeholder="e.g. a nickname, in katakana or kanji"
                    maxLength={30}
                    autoComplete="off"
                    spellCheck={false}
                    lang="ja"
                  />
                </label>
              )}
              <p className="settings-help" id="address-help">
                "Let her decide" starts polite (さん) and gets more familiar as
                trust grows, taking your pronouns into account. The ending also
                shows in the English line (e.g. {(userName.trim() || "Alex") + "-kun"}).
              </p>

              {!builtinMode && connStatus && connStatus.reachable && connStatus.models.length > 0 && (
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
                {builtinMode && builtinBudgetCap !== null && (
                  <>
                    {" "}Her built-in model holds at most {builtinBudgetCap} of
                    history, so a higher number is used as {builtinBudgetCap}.
                  </>
                )}
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

              <div className="settings-toggle-row" aria-label="Show her Japanese line">
                <div className="settings-toggle-text">
                  <span className="settings-toggle-title">Show her Japanese line</span>
                  <span className="settings-toggle-desc">
                    Shows the Japanese she actually spoke under each of her
                    messages - this is also what she remembers. Editing one of
                    her messages rewrites it to match (her long-term memory may
                    already have learned the old line). Saved in this browser.
                  </span>
                </div>
                <button
                  type="button"
                  className={`settings-toggle${showJapanese ? " on" : ""}`}
                  onClick={() => setShowJapanese((v) => !v)}
                  aria-pressed={showJapanese}
                >
                  <span className="settings-toggle-knob" aria-hidden="true" />
                  <span>{showJapanese ? "On" : "Off"}</span>
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
                  {savingSettings ? "Saving..." : builtinMode ? "Save" : "Save connection"}
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
