import assert from "node:assert/strict";
import { filterModelOptions } from "../src/components/ModelPicker";
import type { ModelOption } from "../src/components/ModelPicker";

// Settings -> Connection -> Model: typing filters a long provider list.
const options: ModelOption[] = [
  { value: "google/gemini-3.8-flash" },
  { value: "google/gemini-3.8-flash:batch" },
  { value: "anthropic/claude-sonnet-5.5" },
  { value: "qwen/qwen3.8-27b" },
  { value: "gemma-4-12B-it-qat-UD-Q4_K_XL.gguf", hint: "6.3 GB" },
];
const values = (list: ModelOption[]) => list.map((o) => o.value);

// Empty query = everything, in the server's order.
assert.deepEqual(values(filterModelOptions(options, "")), values(options));
assert.deepEqual(values(filterModelOptions(options, "   ")), values(options));
// Any case, part of the name.
assert.deepEqual(values(filterModelOptions(options, "GEMINI")),
  ["google/gemini-3.8-flash", "google/gemini-3.8-flash:batch"]);
// Every word must match, in any order.
assert.deepEqual(values(filterModelOptions(options, "flash batch")), ["google/gemini-3.8-flash:batch"]);
assert.deepEqual(values(filterModelOptions(options, "sonnet anthropic")), ["anthropic/claude-sonnet-5.5"]);
// Labels count too (a file shown under another name).
assert.deepEqual(values(filterModelOptions([{ value: "x.gguf", label: "Her 12B model" }], "12b")), ["x.gguf"]);
// No match = empty (the picker then offers to use the typed name).
assert.deepEqual(filterModelOptions(options, "llama"), []);

console.log("modelPicker.test.ts: ok");
