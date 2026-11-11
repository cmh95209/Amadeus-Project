import assert from "node:assert/strict";
import { providerForAddress, probeStatus, shouldAutoProbe } from "../src/providers";
import type { ProviderRow, VertexInfo } from "../src/api";

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
const vertexNone: VertexInfo = { configured: false, project: "", region: "global", address: "" };

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

// Busy: whatever the check says, the line says so.
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a"], loading: false, error: null }, byId("openai"), null, true),
  { tone: "", text: "Checking..." });

// No check yet: the words that point at what to do.
assert.deepEqual(probeStatus(null, byId("vertex"), vertexNone, false),
  { tone: "", text: "Paste your JSON key and press Save JSON key." });
assert.deepEqual(probeStatus(null, byId("ollama"), null, false),
  { tone: "", text: "Not checked yet." });
assert.deepEqual(probeStatus(null, byId("openai"), null, false),
  { tone: "", text: "Enter your API key and press Save key." });
assert.deepEqual(probeStatus(null, null, null, false), { tone: "", text: "" });

// The server answers but is still loading its model.
assert.deepEqual(
  probeStatus({ reachable: true, models: [], loading: true, error: null }, byId("openai"), null, false),
  { tone: "warn", text: "The server is still loading its model." });

// Vertex: signed in (the project comes from the sign-in state).
assert.deepEqual(
  probeStatus({ reachable: true, models: [], loading: false, error: null }, byId("vertex"), vertexSaved, false),
  { tone: "ok", text: "Signed in to Google Vertex (project my-project)." });

// Connected: the model count, with the one-model and no-model wordings.
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a", "b", "c"], loading: false, error: null }, byId("openai"), null, false),
  { tone: "ok", text: "Connected - 3 models." });
assert.deepEqual(
  probeStatus({ reachable: true, models: ["a"], loading: false, error: null }, byId("openai"), null, false),
  { tone: "ok", text: "Connected - 1 model." });
assert.deepEqual(
  probeStatus({ reachable: true, models: [], loading: false, error: null }, byId("openai"), null, false),
  { tone: "ok", text: "Connected." });

// The server answered but refused (a wrong key): a failure, never "Connected".
assert.deepEqual(
  probeStatus({ reachable: true, models: [], loading: false,
    error: "Server is running but rejected the API key (HTTP 401)" }, byId("openai"), null, false),
  { tone: "fail", text: "The provider did not accept this key. Check it and save it again." });
assert.deepEqual(
  probeStatus({ reachable: true, models: [], loading: false, error: "Server responded with HTTP 500" },
    byId("openai"), null, false),
  { tone: "fail", text: "The server answered, but: Server responded with HTTP 500" });

// Not reachable: the server's own words when it has any.
assert.deepEqual(
  probeStatus({ reachable: false, models: [], loading: false, error: "connection refused" }, byId("openai"), null, false),
  { tone: "fail", text: "Can't connect: connection refused" });
assert.deepEqual(
  probeStatus({ reachable: false, models: [], loading: false, error: null }, byId("openai"), null, false),
  { tone: "fail", text: "Can't connect: no answer from the server" });

console.log("providers.test.ts: ok");
