import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The built app ships inside the Python package, so `pip install` and the desktop
// executable serve it without Node. `npm run dev` proxies the API to `devicescout serve`.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../devicescout/web/dist",
    emptyOutDir: true,
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
});
