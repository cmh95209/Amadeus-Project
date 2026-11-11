import type { ProviderProbe, ProviderRow, VertexInfo } from "./api";

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

/** The status line under the key area: what the last check (or its absence)
 * says in plain words. `busy` = a check is running right now. */
export function probeStatus(
  probe: ProviderProbe | null,
  row: ProviderRow | null,
  vertex: VertexInfo | null,
  busy: boolean,
): ProbeStatus {
  if (busy) return { tone: "", text: "Checking..." };
  if (!probe) {
    if (!row) return { tone: "", text: "" };
    if (row.kind === "vertex") {
      return { tone: "", text: "Paste your JSON key and press Save JSON key." };
    }
    if (row.kind === "local") {
      return { tone: "", text: "Not checked yet." };
    }
    return { tone: "", text: "Enter your API key and press Save key." };
  }
  if (probe.loading) {
    return { tone: "warn", text: "The server is still loading its model." };
  }
  if (probe.reachable && probe.error) {
    // The server answered but said no (a wrong key, most of the time).
    return {
      tone: "fail",
      text: /rejected the API key/i.test(probe.error)
        ? "The provider did not accept this key. Check it and save it again."
        : `The server answered, but: ${probe.error}`,
    };
  }
  if (probe.reachable) {
    if (row?.kind === "vertex") {
      const project = vertex?.project || "";
      return {
        tone: "ok",
        text: project
          ? `Signed in to Google Vertex (project ${project}).`
          : "Signed in to Google Vertex.",
      };
    }
    const count = probe.models.length;
    return {
      tone: "ok",
      text: count === 0 ? "Connected." : `Connected - ${count} model${count === 1 ? "" : "s"}.`,
    };
  }
  return {
    tone: "fail",
    text: `Can't connect: ${probe.error || "no answer from the server"}`,
  };
}
