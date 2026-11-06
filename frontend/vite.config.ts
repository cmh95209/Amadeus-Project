import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],

  // Lets the phone open the page through Tailscale (https://<pc>.<tailnet>.ts.net).
  // No live-reload socket: Android drops it whenever the browser goes to the
  // background (camera, gallery, screen off), and Vite then reloads the page
  // on return - losing the picture just picked. Refresh by hand after edits.
  server: {
    allowedHosts: [".ts.net"],
    ws: false,
  },

  resolve: {
    extensions: [".ts", ".tsx", ".js"],
    alias: {
      "@framework": path.resolve(
        __dirname,
        "./cubism/Framework/src"
      ),
    },
  },
});