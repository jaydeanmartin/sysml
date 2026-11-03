import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the API services run on the host (or in compose with ports published).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api/models": { target: "http://localhost:8001", rewrite: (p) => p.replace(/^\/api\/models/, "") },
      "/api/sysml-v2": { target: "http://localhost:8001", rewrite: (p) => p.replace(/^\/api/, "") },
      "/api/interpreter": { target: "http://localhost:8002", rewrite: (p) => p.replace(/^\/api\/interpreter/, "") },
      "/api/analysis": { target: "http://localhost:8003", rewrite: (p) => p.replace(/^\/api\/analysis/, "") },
      "/api/geometry": { target: "http://localhost:8004", rewrite: (p) => p.replace(/^\/api\/geometry/, "") },
    },
  },
});
