import type {
  ProviderProbe,
  ProviderRow,
  ProviderTestResult,
  VertexInfo,
} from "./api";

// Settings -> Connection -> Custom Model: pure helpers around the provider
// table (no React, no fetch) - which row a typed address belongs to, which
// rows Settings may check on its own, and the status line's plain words.

/** Which provider a server address belongs to: blank = "auto"; a table
 * address (case and trailing slash ignored) = that row's id; Google
 * Vertex's aiplatform address = "vertex"; anything else = "custom". */
export function providerForAddress(rows: ProviderRow[], address: string): string {
  const clean = (address ?? "").trim().toLowerCase().replace(/\/+$/, "");
  if (!clean) return "auto";
  for (const row of rows) {
    const url = (row.url ?? "").toLowerCase().replace(/\/+$/, "");
    if (url && url === clean) return row.id;
  }
  if (clean.includes("aiplatform.googleapis.com")) return "vertex";
  return "custom";
}

/** Whether Settings may check this row on its own: local servers answer
 * without a key; a Vertex pass is checked by signing in (only when one is
 * saved); a cloud or custom provider is checked once its key is saved. */
export function shouldAutoProbe(row: ProviderRow, vertex: VertexInfo | null): boolean {
  if (row.kind === "local") return true;
  if (row.kind === "vertex") return vertex?.configured === true;
  return row.has_key === true;
}

export type ProbeStatus = {
  tone: "" | "ok" | "warn" | "fail";
  text: string;
};

/** The status line under the key area: what the last check or Test says in
 * plain words. `test` = the last Test-button answer still valid for the
 * chosen provider + model (the page forgets it when the provider, key,
 * address or model moves on); `busy` = a check is running right now;
 * `testing` = a test message is on its way to `testModel`; `hasModel` =
 * a model is saved or chosen in the Model field (the listing lines end
 * with "press Test." once a model is chosen - the connection is already
 * saved, so only a test is left; with none saved they end with "pick a
 * model and press Save below.", which stores it). */
export function probeStatus(
  probe: ProviderProbe | null,
  row: ProviderRow | null,
  vertex: VertexInfo | null,
  busy: boolean,
  test: ProviderTestResult | null,
  testing = false,
  testModel = "",
  hasModel = false,
): ProbeStatus {
  // The "next step" tail of the status lines: with no model saved yet the
  // next Save stores one, with a model chosen only a Test is left.
  const nudgeLower = hasModel ? "press Test." : "pick a model and press Save below.";
  const nudge = hasModel ? "Press Test." : "Pick a model and press Save below.";
  if (busy) return { tone: "", text: "Checking..." };
  if (testing) {
    return { tone: "", text: `Sending a test message to ${testModel || "the model"}...` };
  }
  if (test) {
    // The last thing that happened was a Test: its words stand (the
    // "no model chosen" nudge stays neutral - nothing is wrong yet).
    return {
      tone: test.ok ? "ok" : test.outcome === "no_model" ? "" : "fail",
      text: test.message,
    };
  }
  if (!probe) {
    if (!row) return { tone: "", text: "" };
    if (row.kind === "vertex") {
      return { tone: "", text: "Paste your JSON key and press Save JSON key." };
    }
    if (row.kind === "cloud") {
      // Only a cloud provider needs a key before it can be checked.
      return row.has_key
        ? { tone: "", text: "Not checked yet." }
        : { tone: "", text: "Enter your API key and press Save." };
    }
    // Local, auto and custom servers work without a key (a keyless one is
    // checked with a placeholder): the line is just "not checked yet".
    return { tone: "", text: "Not checked yet." };
  }
  if (probe.loading) {
    return { tone: "warn", text: "The server is still loading its model." };
  }
  if (probe.reachable && probe.error) {
    // The server answered but said no (a wrong key, most of the time).
    // Local and custom rows say "server" (a custom address is usually
    // someone's own server); cloud and Vertex keep "provider".
    return {
      tone: "fail",
      text: /rejected the API key/i.test(probe.error)
        ? (row && (row.kind === "local" || row.kind === "custom")
          ? "The server rejected this key."
          : "The provider rejected this key.")
        : `The server answered, but: ${probe.error}`,
    };
  }
  if (probe.reachable) {
    if (row?.kind === "vertex") {
      return {
        tone: "ok",
        text: `Signed in to Google Vertex. Not tested yet - ${nudgeLower}`,
      };
    }
    const count = probe.models.length;
    if (row?.kind === "local" || (row?.kind === "custom" && !probe.has_key)) {
      // Local, auto and keyless custom / LAN servers: connected is good
      // enough - they take no key at all.
      return {
        tone: "ok",
        text: count === 0
          ? `Connected. ${nudge}`
          : `Connected, ${count} model${count === 1 ? "" : "s"}. ${nudge}`,
      };
    }
    if (!probe.has_key) {
      // A cloud provider with no key saved; a server that lists models
      // without a key says nothing about one either way.
      return {
        tone: "",
        text: count > 0
          ? "Model list loaded. This provider shows it without a key - no key saved yet."
          : "No key saved yet.",
      };
    }
    if (probe.key_checked) {
      return {
        tone: "ok",
        text: `Key accepted, ${count} model${count === 1 ? "" : "s"} listed. Not tested yet - ${nudgeLower}`,
      };
    }
    return {
      tone: "",
      text: `Key saved, ${count} model${count === 1 ? "" : "s"} listed. This server does not check the key here - ${nudgeLower}`,
    };
  }
  if (row?.kind === "custom" && /address/i.test(probe.error ?? "")) {
    // A custom address is still empty: the fix is to type it in.
    return { tone: "fail", text: "Enter the server address and press Save." };
  }
  return {
    tone: "fail",
    text: `Can't connect: ${probe.error || "no answer from the server"}`,
  };
}
