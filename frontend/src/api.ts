export type Conversation = {
  id: number;
  title: string;
  created_at?: string;
  updated_at?: string;
};

export type MemoryMessage = {
  id?: number;
  role: string;
  content: string;
  created_at?: string;
  conversation_id?: number;
  audio_url?: string;
  has_japanese?: boolean;
  active?: boolean;
  version?: number;
  total_versions?: number;
  version_ids?: number[];
  is_greeting?: boolean;
};

export type MessageReply = {
  response: string;
  speechUrl?: string;
  userId?: number;
  assistantId?: number;
  audioRelUrl?: string;
  conversationId?: number;
};

export const API_BASE = "http://127.0.0.1:5050";

export async function getPersonality(): Promise<string> {
  const data = await parseResponse(await fetch(`${API_BASE}/getPersonality`, { cache: "no-store" }));
  if (typeof data.personality !== "string") throw new Error("Could not read personality");
  return data.personality;
}

export async function setPersonality(personality: string): Promise<string> {
  const data = await parseResponse(await fetch(`${API_BASE}/setPersonality`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ personality }),
  }));
  if (typeof data.personality !== "string") throw new Error("Could not confirm saved personality");
  return data.personality;
}

export async function getApiKeyStatus(): Promise<boolean> {
  const response = await fetch(`${API_BASE}/api_key_status`, { cache: "no-store" });
  const data = await parseResponse(response);
  if (typeof data.configured !== "boolean") throw new Error("Could not read API key status");
  return data.configured;
}

export async function setApiKey(key: string): Promise<void> {
  await parseResponse(await fetch(`${API_BASE}/set_key`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key }),
  }));
}

async function parseResponse(response: Response) {
  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new Error(
      data?.message || `Request failed (${response.status})`
    );
  }

  return data;
}

export type MessageTurn = {
  /** The turn as soon as her voice can start (web-ON: as soon as the single
   * JSON reply lands). */
  reply: MessageReply;
  /** The English display line. Resolves when the backend's fast translation
   * call lands (voice-first, web OFF - her voice is already playing by then;
   * falls back to her Japanese line if the call fails or times out).
   * Resolves immediately for web-ON replies, which carry it in one JSON. */
  text: Promise<string>;
};

export async function sendMessage(userInput: string): Promise<MessageTurn> {
  const response = await fetch(`${API_BASE}/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      user_input: userInput,
    }),
  });

  // Voice-first (web OFF) streams two events: "voice" (start audio at once)
  // then "text" (the display line, filled by the backend's fast call 2).
  const contentType = (response.headers.get("content-type") || "").toLowerCase();
  if (contentType.includes("text/event-stream")) {
    return await readVoiceFirstStream(response);
  }

  const data = await parseResponse(response);
  if (typeof data.response !== "string") {
    throw new Error("Backend returned an invalid response");
  }

  const reply = {
    response: data.response,
    speechUrl:
      typeof data.speech_id === "string"
        ? `${API_BASE}/speech/${encodeURIComponent(data.speech_id)}`
        : undefined,
    userId: typeof data.user_id === "number" ? data.user_id : undefined,
    assistantId: typeof data.assistant_id === "number" ? data.assistant_id : undefined,
    conversationId:
      typeof data.conversation_id === "number" ? data.conversation_id : undefined,
  };
  return { reply, text: Promise.resolve(reply.response) };
}

type VoiceFirstEvent = {
  phase?: string;
  speech_id?: string;
  user_id?: number;
  assistant_id?: number;
  conversation_id?: number;
  response?: string;
};

async function readVoiceFirstStream(response: Response): Promise<MessageTurn> {
  if (!response.ok) {
    const data = await parseResponse(response);
    throw new Error(data?.message || `Request failed (${response.status})`);
  }
  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("This browser does not support streaming responses");
  }

  let resolveReply: ((reply: MessageReply) => void) | null = null;
  let resolveText: ((text: string) => void) | null = null;
  const replyPromise = new Promise<MessageReply>((r) => (resolveReply = r));
  const textPromise = new Promise<string>((r) => (resolveText = r));

  let reply: MessageReply | null = null;
  let text = "";
  let replySettled = false;
  let textSettled = false;

  const settleReply = () => {
    if (!replySettled && reply && resolveReply) {
      replySettled = true;
      resolveReply(reply);
    }
  };
  const settleText = (value: string) => {
    if (!textSettled && resolveText) {
      textSettled = true;
      resolveText(value);
    }
  };

  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // Events are "data: <json>" frames terminated by a blank line.
      let cut: number;
      while ((cut = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        for (const line of frame.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          let event: VoiceFirstEvent;
          try {
            event = JSON.parse(line.slice(6)) as VoiceFirstEvent;
          } catch {
            continue; // a torn frame - the server always terminates frames
          }
          if (event.phase === "voice") {
            reply = {
              response: "",
              speechUrl:
                typeof event.speech_id === "string"
                  ? `${API_BASE}/speech/${encodeURIComponent(event.speech_id)}`
                  : undefined,
              userId: typeof event.user_id === "number" ? event.user_id : undefined,
              assistantId:
                typeof event.assistant_id === "number" ? event.assistant_id : undefined,
              conversationId:
                typeof event.conversation_id === "number" ? event.conversation_id : undefined,
            };
            settleReply();
          } else if (event.phase === "text") {
            if (typeof event.response === "string") text = event.response;
            settleText(text);
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }

  // The server always sends both events (the text event is bounded by a
  // server-side timeout and then carries her Japanese line). If the stream
  // still ends early, settle what arrived and surface the missing voice
  // phase as an error.
  settleReply();
  settleText(text);
  if (!replySettled) {
    throw new Error("The backend stream ended before the voice event");
  }

  const resolvedReply = await replyPromise;
  return { reply: resolvedReply, text: textPromise };
}

export type GreetingReply = {
  ready: boolean;
  reason?: string;
  response?: string;
  speechUrl?: string;
  assistantId?: number;
  conversationId?: number;
  /** Where the first-launch ceremony stands ("intro" | "connecting" |
   *  "done"); absent from backends that predate the ceremony. */
  ceremony?: string;
  /** Which line this report carries: "intro" | "trying" | "reminder"
   *  (ceremony wait-lines that do NOT finish the ceremony), "wake" |
   *  "fallback" (ceremony finished), or absent (ordinary greeting). */
  kind?: string;
};

async function fetchGreetingReport(path: string, justSaved = false): Promise<GreetingReply> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // just_saved tells the backend the user JUST saved connection settings
    // (a fresh attempt: re-say "checking the line", restart its reminder
    // clock) - the 30 s probes send nothing.
    body: JSON.stringify(justSaved ? { just_saved: true } : {}),
  });
  const data = await parseResponse(response);
  if (typeof data.ready !== "boolean") {
    throw new Error("Backend returned an invalid greeting report");
  }
  const ceremony = typeof data.ceremony === "string" ? data.ceremony : undefined;
  if (!data.ready) {
    // A fixed first-launch wait-line (intro / trying / reminder) ships in the
    // report even while the model is DOWN: those lines need no model, and on a
    // fresh install the model is down until the user connects it - which is
    // exactly what the intro line teaches. Carry the line through instead of
    // dropping it (2026-10-07: the fresh-box run lost the welcome this way).
    const kind = typeof data.kind === "string" ? data.kind : undefined;
    const isWaitLine =
      (kind === "intro" || kind === "trying" || kind === "reminder") &&
      typeof data.response === "string";
    if (!isWaitLine) {
      return { ready: false, reason: typeof data.reason === "string" ? data.reason : undefined, ceremony };
    }
    return {
      ready: false,
      reason: typeof data.reason === "string" ? data.reason : undefined,
      response: data.response,
      speechUrl:
        typeof data.speech_id === "string"
          ? `${API_BASE}/speech/${encodeURIComponent(data.speech_id)}`
          : undefined,
      assistantId: typeof data.assistant_id === "number" ? data.assistant_id : undefined,
      conversationId:
        typeof data.conversation_id === "number" ? data.conversation_id : undefined,
      ceremony,
      kind,
    };
  }
  if (typeof data.response !== "string") {
    throw new Error("Backend returned an invalid greeting");
  }
  return {
    ready: true,
    response: data.response,
    speechUrl:
      typeof data.speech_id === "string"
        ? `${API_BASE}/speech/${encodeURIComponent(data.speech_id)}`
        : undefined,
    assistantId: typeof data.assistant_id === "number" ? data.assistant_id : undefined,
    conversationId:
      typeof data.conversation_id === "number" ? data.conversation_id : undefined,
    ceremony,
    kind: typeof data.kind === "string" ? data.kind : undefined,
  };
}

export async function getGreeting(justSaved = false): Promise<GreetingReply> {
  return fetchGreetingReport(`${API_BASE}/greet`, justSaved);
}

export async function getConversationGreeting(id: number): Promise<GreetingReply> {
  return fetchGreetingReport(`${API_BASE}/conversations/${id}/greet`);
}


export async function getMemory(): Promise<MemoryMessage[]> {
  const response = await fetch(`${API_BASE}/getMemory`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
  });

  const data = await parseResponse(response);
  return data.messages ?? [];
}

export async function resetMemory(conversationId?: number): Promise<void> {
  const response = await fetch(`${API_BASE}/memory_reset`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ conversation_id: conversationId ?? null }),
  });

  await parseResponse(response);
}

export async function getCurrentModel(): Promise<string> {
  const response = await fetch(`${API_BASE}/getCurrLLMModel`);

  const data = await parseResponse(response);
  return data.message ?? "";
}

export async function setModel(model: string): Promise<void> {
  const response = await fetch(`${API_BASE}/setLLMModel`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model,
    }),
  });

  await parseResponse(response);
}

export async function getWebAccess(): Promise<boolean> {
  const data = await parseResponse(await fetch(`${API_BASE}/getWebAccess`, { cache: "no-store" }));
  if (typeof data.enabled !== "boolean") throw new Error("Could not read web access state");
  return data.enabled;
}

export async function setWebAccess(enabled: boolean): Promise<void> {
  await parseResponse(await fetch(`${API_BASE}/setWebAccess`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  }));
}

export async function getDeepThinking(): Promise<boolean> {
  const data = await parseResponse(await fetch(`${API_BASE}/getDeepThinking`, { cache: "no-store" }));
  if (typeof data.enabled !== "boolean") throw new Error("Could not read deep thinking state");
  return data.enabled;
}

export async function setDeepThinking(enabled: boolean): Promise<void> {
  await parseResponse(await fetch(`${API_BASE}/setDeepThinking`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  }));
}

export async function sendInteraction(interactionValue: number): Promise<MessageReply> {
  const response = await fetch(`${API_BASE}/doSpecialInteraction`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      interaction_value: interactionValue,
    }),
  });

  const data = await parseResponse(response);
  if (typeof data.response !== "string") throw new Error("Invalid interaction response");
  return {
    response: data.response,
    speechUrl:
      typeof data.audio_url === "string" &&
      data.audio_url.startsWith("/reaction_audio/")
        ? `${API_BASE}${data.audio_url}`
        : undefined,
    userId: typeof data.event_id === "number" ? data.event_id : undefined,
    assistantId: typeof data.response_id === "number" ? data.response_id : undefined,
    conversationId:
      typeof data.conversation_id === "number" ? data.conversation_id : undefined,
    audioRelUrl:
      typeof data.audio_url === "string" && data.audio_url.startsWith("/")
        ? data.audio_url
        : undefined,
  };
}

export async function editMessage(id: number, content: string): Promise<void> {
  await parseResponse(await fetch(`${API_BASE}/memory/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  }));
}

export async function deleteMessage(id: number): Promise<void> {
  await parseResponse(await fetch(`${API_BASE}/memory/${id}`, { method: "DELETE" }));
}

export type UndoResult = {
  action: "switch" | "deleted";
  deleted_id: number | null;
  activated_id?: number;
};

export async function undoLastReply(): Promise<UndoResult | null> {
  const data = await parseResponse(await fetch(`${API_BASE}/memory/undo`, { method: "POST" }));
  if (typeof data.action !== "string") return null;
  return {
    action: data.action as "switch" | "deleted",
    deleted_id: typeof data.deleted_id === "number" ? data.deleted_id : null,
    activated_id: typeof data.activated_id === "number" ? data.activated_id : undefined,
  };
}

export async function regenerate(): Promise<MessageReply> {
  const response = await fetch(`${API_BASE}/regenerate`, { method: "POST" });
  const data = await parseResponse(response);
  if (typeof data.response !== "string") throw new Error("Backend returned an invalid response");
  return {
    response: data.response,
    speechUrl:
      typeof data.speech_id === "string"
        ? `${API_BASE}/speech/${encodeURIComponent(data.speech_id)}`
        : undefined,
    assistantId: typeof data.assistant_id === "number" ? data.assistant_id : undefined,
  };
}

export async function activateReplyVersion(messageId: number): Promise<number> {
  const data = await parseResponse(
    await fetch(`${API_BASE}/memory/versions/activate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message_id: messageId }),
    })
  );
  if (typeof data.activated_id !== "number") throw new Error("Could not switch reply version");
  return data.activated_id;
}

export async function regenerateMessageVoice(messageId: number): Promise<string> {
  const data = await parseResponse(
    await fetch(`${API_BASE}/memory/${messageId}/voice`, { method: "POST" })
  );
  if (typeof data.audio_url !== "string") throw new Error("Could not regenerate the voice line");
  return data.audio_url;
}

export async function getVoiceRetention(): Promise<number> {
  const data = await parseResponse(await fetch(`${API_BASE}/getVoiceRetention`, { cache: "no-store" }));
  return typeof data.limit === "number" ? data.limit : 100;
}

export async function setVoiceRetention(limit: number): Promise<number> {
  const data = await parseResponse(
    await fetch(`${API_BASE}/setVoiceRetention`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ limit }),
    })
  );
  return typeof data.limit === "number" ? data.limit : limit;
}

export async function getContextBudget(): Promise<number> {
  const data = await parseResponse(await fetch(`${API_BASE}/getContextBudget`, { cache: "no-store" }));
  return typeof data.budget === "number" ? data.budget : 40000;
}

export async function setContextBudget(budget: number): Promise<number> {
  const data = await parseResponse(
    await fetch(`${API_BASE}/setContextBudget`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ budget }),
    })
  );
  return typeof data.budget === "number" ? data.budget : budget;
}

// ---- Model sampling settings ------------------------------------------------
export type SamplingEntry = { enabled: boolean; value: number | null };
export type SamplingSettings = Record<string, SamplingEntry>;
export type SamplingParamSpec = { min: number; max: number; step: number; integer: boolean };

export type SamplingConfig = {
  sampling: SamplingSettings;
  params: Record<string, SamplingParamSpec>;
  local_only: string[];
  rejected: string[];
};

export async function getSampling(): Promise<SamplingConfig> {
  const data = await parseResponse(await fetch(`${API_BASE}/getSampling`, { cache: "no-store" }));
  if (typeof data?.sampling !== "object" || data.sampling === null) {
    throw new Error("Backend returned invalid sampling settings");
  }
  return {
    sampling: data.sampling as SamplingSettings,
    params: typeof data.params === "object" && data.params !== null ? (data.params as Record<string, SamplingParamSpec>) : {},
    local_only: Array.isArray(data.local_only) ? data.local_only : [],
    rejected: Array.isArray(data.rejected) ? data.rejected : [],
  };
}

export async function setSampling(sampling: SamplingSettings): Promise<void> {
  await parseResponse(
    await fetch(`${API_BASE}/setSampling`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(sampling),
    })
  );
}

export type ConnectionStatus = {
  address: string;
  configured: boolean;
  reachable: boolean;
  models: string[];
  configured_model: string;
  model_configured: boolean;
  model_found: boolean;
  batch_warning?: string;
  error?: string;
};

export async function getLLMServer(): Promise<string> {
  const data = await parseResponse(await fetch(`${API_BASE}/getLLMServer`, { cache: "no-store" }));
  return typeof data.address === "string" ? data.address : "";
}

export async function setLLMServer(address: string): Promise<string> {
  const data = await parseResponse(
    await fetch(`${API_BASE}/setLLMServer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address }),
    })
  );
  return typeof data.address === "string" ? data.address : address;
}

/** What the user goes by ("" = not set). Personal, like the other
 *  connection settings. */
export async function getUsername(): Promise<string> {
  const data = await parseResponse(await fetch(`${API_BASE}/getUsername`, { cache: "no-store" }));
  return typeof data.username === "string" ? data.username : "";
}

export async function setUsername(name: string): Promise<string> {
  const data = await parseResponse(
    await fetch(`${API_BASE}/setUsername`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: name }),
    })
  );
  return typeof data.username === "string" ? data.username : name;
}

export async function testConnection(): Promise<ConnectionStatus> {
  const data = await parseResponse(await fetch(`${API_BASE}/testConnection`, { cache: "no-store" }));
  if (typeof data.reachable !== "boolean" || typeof data.address !== "string") {
    throw new Error("Backend returned an invalid connection report");
  }
  return {
    address: data.address,
    configured: data.configured !== false,
    reachable: data.reachable,
    models: Array.isArray(data.models) ? data.models.filter((m: unknown) => typeof m === "string") : [],
    configured_model: typeof data.configured_model === "string" ? data.configured_model : "",
    model_configured: data.model_configured !== false,
    model_found: data.model_found === true,
    batch_warning: typeof data.batch_warning === "string" ? data.batch_warning : undefined,
    error: typeof data.error === "string" ? data.error : undefined,
  };
}

export async function listConversations(): Promise<{ conversations: Conversation[]; active_id: number }> {
  const data = await parseResponse(await fetch(`${API_BASE}/conversations`, { cache: "no-store" }));
  return {
    conversations: Array.isArray(data.conversations) ? data.conversations : [],
    active_id: typeof data.active_id === "number" ? data.active_id : -1,
  };
}

export async function createConversation(title?: string): Promise<number> {
  const data = await parseResponse(await fetch(`${API_BASE}/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title ?? null }),
  }));
  if (typeof data.id !== "number") throw new Error("Could not create conversation");
  return data.id;
}

export async function activateConversation(id: number): Promise<number> {
  const data = await parseResponse(await fetch(`${API_BASE}/conversations/${id}/activate`, { method: "POST" }));
  return typeof data.active_id === "number" ? data.active_id : id;
}

export async function renameConversation(id: number, title: string): Promise<void> {
  await parseResponse(await fetch(`${API_BASE}/conversations/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  }));
}

export async function deleteConversation(id: number): Promise<number | null> {
  const data = await parseResponse(await fetch(`${API_BASE}/conversations/${id}`, { method: "DELETE" }));
  return typeof data.active_id === "number" ? data.active_id : null;
}


export type StatInfo = {
  key: string;
  label: string;
  value: number;
  min: number;
  max: number;
  description?: string;
  tier?: string;
};

export async function getStats(): Promise<StatInfo[]> {
  const data = await parseResponse(await fetch(`${API_BASE}/stats`, { cache: "no-store" }));
  return Array.isArray(data.stats) ? data.stats : [];
}
