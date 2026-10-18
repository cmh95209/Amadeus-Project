import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { runInThisContext } from "node:vm";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";

// Run the exact bundled Core, without a browser or a Flask process.
const root = fileURLToPath(new URL("../", import.meta.url));
const core = await readFile(new URL("../public/live2dcubismcore.min.js", import.meta.url), "utf8");
globalThis.Live2DCubismCore = runInThisContext(
  `(function(require, __dirname) { ${core}; return Live2DCubismCore; })`
)(createRequire(import.meta.url), root);
const server = await createServer({ root, server: { middlewareMode: true }, appType: "custom" });
try {
  // First: it does not depend on the Live2D core, and the pre-existing
  // motions.test.ts failure stops the run.
  await server.ssrLoadModule("/tests/greeting.test.ts");
  await server.ssrLoadModule("/tests/modelPicker.test.ts");
  await server.ssrLoadModule("/tests/motions.test.ts");
  await server.ssrLoadModule("/tests/speech.test.ts");
} finally {
  await server.close();
}
