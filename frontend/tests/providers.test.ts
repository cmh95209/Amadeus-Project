import assert from "node:assert/strict";
import { connectionUnsaved, providerForAddress, probeStatus, shouldAutoProbe } from "../src/providers";
import type { ProviderRow, ProviderTestResult, VertexInfo } from "../src/api";

// Settings -> Connection -> Custom Model: which provider a typed address
// belongs to, which rows may be checked on their own, and the status line.

const rows: ProviderRow[] = [
  { id: "auto", label: "Local (auto-detect)", kind: "local", url: "", has_key: false },
  { id: "ollama", label: "Ollama", kind: "local", url: "http://localhost:11434/v1", has_key: false },
  { id: "lmstudio", label: "LM Studio", kind: "local", url: "http://localhost:1234/v1", has_key: false },
  { id: "openrouter", label: "OpenRouter", kind: "cloud", url: "https://openrouter.ai/api/v1", has_key: true },
  { id: "openai", label: "OpenAI", kind: "cloud", url: "https://api.openai.com/v1", has_key: false },
  { id: "vertex", label: "Google Vertex AI (JSON key)", kind: "vertex", url: "", has_key: true },
  { id: "custom", label: "Custom address", kind: "custom", url: "", has_key: false },
];
const byId = (id: string): ProviderRow => rows.find((r) => r.id === id)!;
const vertexSaved: VertexInfo = { configured: true, project: "my-project", region: "global", address: "" };
const vertexUs: VertexInfo = { configured: true, project: "my-project", region: "us-central1", address: "" };
const vertexNone: VertexInfo = { configured: false, project: "", region: "global", address: "" };

// A Test-button answer (the backend's words, as the page would receive it).
const tested = (over: Partial<ProviderTestResult> = {}): ProviderTestResult => ({
  ok: false,
  outcome: "bad_key",
  message: "The provider rejected this key.",
  model: "gpt-test",
  seconds: 1.2,
  ...over,
});

// Blank = auto-detect. A table address = that row (any case, trailing slash
// ignored). Vertex's aiplatform host = vertex. Anything else = custom.
assert.equal(providerForAddress(rows, ""), "auto");
assert.equal(providerForAddress(rows, "   "), "auto");
assert.equal(providerForAddress(rows, "http://localhost:11434/v1"), "ollama");
assert.equal(providerForAddress(rows, "HTTP://LOCALHOST:11434/V1/"), "ollama");
assert.equal(providerForAddress(rows, "https://api.openai.com/v1/"), "openai");
assert.equal(providerForAddress(rows, "https://aiplatform.googleapis.com/v1/projects/p/locations/global/endpoints/openapi"),
  "vertex");
assert.equal(providerForAddress(rows, "https://europe-west1-aiplatform.googleapis.com/x"), "vertex");
assert.equal(providerForAddress(rows, "http://192.168.0.10:8000/v1"), "custom");
assert.equal(providerForAddress(rows, "https://some.other-host.example/v1"), "custom");

// Local servers answer without a key; Vertex only once a pass is saved;
// cloud / custom only once their key is saved.
assert.equal(shouldAutoProbe(byId("auto"), vertexNone), true);
assert.equal(shouldAutoProbe(byId("ollama"), vertexNone), true);
assert.equal(shouldAutoProbe(byId("vertex"), vertexSaved), true);
assert.equal(shouldAutoProbe(byId("vertex"), vertexNone), false);
assert.equal(shouldAutoProbe(byId("vertex"), null), false);
assert.equal(shouldAutoProbe(byId("openrouter"), vertexNone), true);
assert.equal(shouldAutoProbe(byId("openai"), vertexNone), false);
assert.equal(shouldAutoProbe(byId("custom"), vertexNone), false);

// A check running: whatever anything says, the line says so. A test
// message on its way: the model it is going to.
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a"], loading: false, error: null }, byId("openai"), null, true, null),
  { tone: "", text: "Checking..." });
assert.deepEqual(
  probeStatus(null, byId("openai"), null, false, null, true, "gpt-test"),
  { tone: "", text: "Sending a test message to gpt-test..." });

// The last Test stands over the last check: its words (ok / a failure /
// the neutral "no model yet" nudge).
assert.deepEqual(
  probeStatus({ reachable: false, models: [], loading: false, error: "connection refused" }, byId("openai"), null, false,
    tested({ ok: true, outcome: "ok", message: "Works with Amadeus. gpt-test replied in 0.8 s." })),
  { tone: "ok", text: "Works with Amadeus. gpt-test replied in 0.8 s." });
assert.deepEqual(
  probeStatus(null, byId("openai"), null, false,
    tested({ ok: false, outcome: "no_model", message: "Pick a model first." })),
  { tone: "", text: "Pick a model first." });
assert.deepEqual(
  probeStatus(null, byId("openai"), null, false,
    tested({ ok: false, outcome: "unreachable", message: "Could not reach the server." })),
  { tone: "fail", text: "Could not reach the server." });

// No check yet: the words that point at what to do. Only a cloud provider
// asks for a key first; local / auto / custom need none (a keyless server
// is checked with a placeholder).
assert.deepEqual(probeStatus(null, byId("vertex"), vertexNone, false, null),
  { tone: "", text: "Paste or choose your JSON key and press Save." });
assert.deepEqual(probeStatus(null, byId("ollama"), null, false, null),
  { tone: "", text: "Not checked yet." });
assert.deepEqual(probeStatus(null, byId("custom"), null, false, null),
  { tone: "", text: "Not checked yet." });
assert.deepEqual(probeStatus(null, byId("openai"), null, false, null),
  { tone: "", text: "Enter your API key and press Save." });
assert.deepEqual(probeStatus(null, byId("openrouter"), null, false, null),
  { tone: "", text: "Not checked yet." });
assert.deepEqual(probeStatus(null, null, null, false, null), { tone: "", text: "" });

// The server answers but is still loading its model.
assert.deepEqual(
  probeStatus({ reachable: true, models: [], loading: true, error: null }, byId("openai"), null, false, null),
  { tone: "warn", text: "The server is still loading its model." });

// Vertex: signed in (the saved endpoint is named). The listing lines end
// with "pick a model, then save and test it above." while no model is saved (that
// Save stores it) and with just "press Test." once a model is chosen (the
// connection is already saved). With the endpoint's own catalogue loaded
// the count is its (a listed model can still 404 - the Test button stays
// the proof); without it only the sign-in is known.
assert.deepEqual(
  probeStatus({ reachable: true, models: [], loading: false, error: null }, byId("vertex"), vertexSaved, false, null),
  { tone: "ok", text: "Signed in to Google Vertex (global). Not tested yet - pick a model, then save and test it above." });
assert.deepEqual(
  probeStatus({ reachable: true, models: [], loading: false, error: null }, byId("vertex"), vertexSaved, false, null, false, "", true),
  { tone: "ok", text: "Signed in to Google Vertex (global). Not tested yet - press Test." });
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a", "b"], loading: false, error: null, catalogue: true }, byId("vertex"), vertexUs, false, null),
  { tone: "ok", text: "Signed in to Google Vertex (us-central1), 2 models listed. Not tested yet - pick a model, then save and test it above." });
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a"], loading: false, error: null, catalogue: true }, byId("vertex"), vertexUs, false, null, false, "", true),
  { tone: "ok", text: "Signed in to Google Vertex (us-central1), 1 model listed. Not tested yet - press Test." });

// Local: connected - the model count, with the one-model and no-model
// wordings, and the next step (both wordings: model chosen / not yet).
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a", "b"], loading: false, error: null }, byId("ollama"), null, false, null),
  { tone: "ok", text: "Connected, 2 models. Pick a model, then save and test it above." });
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a", "b"], loading: false, error: null }, byId("ollama"), null, false, null, false, "", true),
  { tone: "ok", text: "Connected, 2 models. Press Test." });
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a"], loading: false, error: null }, byId("ollama"), null, false, null),
  { tone: "ok", text: "Connected, 1 model. Pick a model, then save and test it above." });
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a"], loading: false, error: null }, byId("ollama"), null, false, null, false, "", true),
  { tone: "ok", text: "Connected, 1 model. Press Test." });
assert.deepEqual(
  probeStatus({ reachable: true, models: [], loading: false, error: null }, byId("ollama"), null, false, null),
  { tone: "ok", text: "Connected. Pick a model, then save and test it above." });
assert.deepEqual(
  probeStatus({ reachable: true, models: [], loading: false, error: null }, byId("ollama"), null, false, null, false, "", true),
  { tone: "ok", text: "Connected. Press Test." });

// Cloud with no key: a listing proves nothing about a key (and no key is
// saved to check); no listing at all says so too.
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a"], loading: false, error: null, has_key: false }, byId("openrouter"), null, false, null),
  { tone: "", text: "Model list loaded. This provider shows it without a key - no key saved yet." });
assert.deepEqual(
  probeStatus({ reachable: true, models: [], loading: false, error: null, has_key: false }, byId("openrouter"), null, false, null),
  { tone: "", text: "No key saved yet." });

// Key saved and the list needed it (a bogus key is refused): accepted -
// again both "next step" wordings (model chosen / not yet).
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a", "b"], loading: false, error: null, has_key: true, key_checked: true }, byId("openai"), null, false, null),
  { tone: "ok", text: "Key accepted, 2 models listed. Not tested yet - pick a model, then save and test it above." });
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a", "b"], loading: false, error: null, has_key: true, key_checked: true }, byId("openai"), null, false, null, false, "", true),
  { tone: "ok", text: "Key accepted, 2 models listed. Not tested yet - press Test." });
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a"], loading: false, error: null, has_key: true, key_checked: true }, byId("openai"), null, false, null),
  { tone: "ok", text: "Key accepted, 1 model listed. Not tested yet - pick a model, then save and test it above." });
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a"], loading: false, error: null, has_key: true, key_checked: true }, byId("openai"), null, false, null, false, "", true),
  { tone: "ok", text: "Key accepted, 1 model listed. Not tested yet - press Test." });

// Key saved but the provider does not check it on listings (both wordings).
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a", "b"], loading: false, error: null, has_key: true }, byId("openrouter"), null, false, null),
  { tone: "", text: "Key saved, 2 models listed. This server does not check the key here - pick a model, then save and test it above." });
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a", "b"], loading: false, error: null, has_key: true }, byId("openrouter"), null, false, null, false, "", true),
  { tone: "", text: "Key saved, 2 models listed. This server does not check the key here - press Test." });

// Custom / LAN: connected without any key is fine (same words as local);
// with a key saved the key words stay.
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a", "b"], loading: false, error: null, has_key: false }, byId("custom"), null, false, null),
  { tone: "ok", text: "Connected, 2 models. Pick a model, then save and test it above." });
assert.deepEqual(
  probeStatus({ reachable: true, models: [], loading: false, error: null, has_key: false }, byId("custom"), null, false, null),
  { tone: "ok", text: "Connected. Pick a model, then save and test it above." });
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a", "b"], loading: false, error: null, has_key: true }, byId("custom"), null, false, null),
  { tone: "", text: "Key saved, 2 models listed. This server does not check the key here - pick a model, then save and test it above." });

// A custom address that is still empty: the fix is to type it in.
assert.deepEqual(
  probeStatus({ reachable: false, models: [], loading: false, error: "Enter the server address first." }, byId("custom"), null, false, null),
  { tone: "fail", text: "Enter the server address and press Save." });

// The server answered but refused (a wrong key): a failure, never
// "Connected". Cloud and Vertex rows say "provider"; local and custom
// rows (a custom address is usually someone's own server) say "server".
assert.deepEqual(
  probeStatus({ reachable: true, models: [], loading: false,
    error: "Server is running but rejected the API key (HTTP 401)" }, byId("openai"), null, false, null),
  { tone: "fail", text: "The provider rejected this key." });
assert.deepEqual(
  probeStatus({ reachable: true, models: [], loading: false,
    error: "Server is running but rejected the API key (HTTP 401)" }, byId("ollama"), null, false, null),
  { tone: "fail", text: "The server rejected this key." });
assert.deepEqual(
  probeStatus({ reachable: true, models: [], loading: false,
    error: "Server is running but rejected the API key (HTTP 401)" }, byId("custom"), null, false, null),
  { tone: "fail", text: "The server rejected this key." });
assert.deepEqual(
  probeStatus({ reachable: true, models: [], loading: false, error: "Server responded with HTTP 500" },
    byId("openai"), null, false, null),
  { tone: "fail", text: "The server answered, but: Server responded with HTTP 500" });

// Not reachable: the server's own words when it has any.
assert.deepEqual(
  probeStatus({ reachable: false, models: [], loading: false, error: "connection refused" }, byId("openai"), null, false, null),
  { tone: "fail", text: "Can't connect: connection refused" });
assert.deepEqual(
  probeStatus({ reachable: false, models: [], loading: false, error: null }, byId("openai"), null, false, null),
  { tone: "fail", text: "Can't connect: no answer from the server" });

// connectionUnsaved: whether Test must save first (MH 2026-11-12 - Test
// tests the NEW choice, so anything changed is saved, then tested).
const draft = {
  providerId: "vertex", activeId: "vertex", keyTyped: false, vertexJsonTyped: false,
  pendingEndpoint: false, model: "gemini-3.8-flash", savedModel: "google/gemini-3.8-flash",
  address: "", savedAddress: "",
};
// Nothing changed (the saved Vertex name carries "google/", the field not).
assert.equal(connectionUnsaved(draft), false);
assert.equal(connectionUnsaved({ ...draft, model: "google/gemini-3.8-flash" }), false);
// The reported case: Hydrogen saved, another model picked (and/or a new endpoint).
assert.equal(connectionUnsaved({ ...draft, savedModel: "google/gemini-4-hydrogen" }), true);
assert.equal(connectionUnsaved({ ...draft, pendingEndpoint: true }), true);
assert.equal(connectionUnsaved({ ...draft, vertexJsonTyped: true }), true);
// Another provider row shown than the one in use; a typed key.
assert.equal(connectionUnsaved({ ...draft, providerId: "openrouter", activeId: "vertex",
  model: "x", savedModel: "x" }), true);
const plain = { ...draft, providerId: "openrouter", activeId: "openrouter",
  model: "google/gemini-3.8-flash", savedModel: "google/gemini-3.8-flash" };
assert.equal(connectionUnsaved(plain), false);
assert.equal(connectionUnsaved({ ...plain, keyTyped: true }), true);
// "google/" is ignored only for Vertex (on OpenRouter it is part of the name).
assert.equal(connectionUnsaved({ ...plain, model: "gemini-3.8-flash" }), true);
// Surrounding spaces in the field do not count as a change.
assert.equal(connectionUnsaved({ ...plain, model: "  google/gemini-3.8-flash " }), false);
// A first pick when no model was saved is a change; clearing it too.
assert.equal(connectionUnsaved({ ...plain, savedModel: "" }), true);
assert.equal(connectionUnsaved({ ...plain, model: "" }), true);
// Custom server: a changed address counts (a trailing slash does not);
// for other rows the address box is not theirs and is ignored.
const custom = { ...plain, providerId: "custom", activeId: "custom",
  address: "http://192.168.0.10:8000/v1", savedAddress: "http://192.168.0.10:8000/v1" };
assert.equal(connectionUnsaved(custom), false);
assert.equal(connectionUnsaved({ ...custom, address: "http://192.168.0.10:8000/v1/" }), false);
assert.equal(connectionUnsaved({ ...custom, address: "http://192.168.0.11:8000/v1" }), true);
assert.equal(connectionUnsaved({ ...plain, address: "http://elsewhere/v1" }), false);
console.log("providers.test.ts: ok");
