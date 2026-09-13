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
  audio_url?: string;
  has_japanese?: boolean;
  active?: boolean;
  version?: number;
  total_versions?: number;
  version_ids?: number[];
};

export type MessageReply = {
  response: string;
  speechUrl?: string;
  userId?: number;
  assistantId?: number;
  audioRelUrl?: string;
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

export async function sendMessage(userInput: string): Promise<MessageReply> {
  const response = await fetch(`${API_BASE}/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      user_input: userInput,
    }),
  });

  const data = await parseResponse(response);
  if (typeof data.response !== "string") {
    throw new Error("Backend returned an invalid response");
  }

  return {
    response: data.response,
    speechUrl:
      typeof data.speech_id === "string"
        ? `${API_BASE}/speech/${encodeURIComponent(data.speech_id)}`
        : undefined,
    userId: typeof data.user_id === "number" ? data.user_id : undefined,
    assistantId: typeof data.assistant_id === "number" ? data.assistant_id : undefined,
  };
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

export type ConnectionStatus = {
  address: string;
  configured: boolean;
  reachable: boolean;
  models: string[];
  configured_model: string;
  model_configured: boolean;
  model_found: boolean;
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
