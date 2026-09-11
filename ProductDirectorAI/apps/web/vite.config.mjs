import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const proxy = { "/api": { target: process.env.PRODUCTDIRECTOR_API_PROXY || "http://127.0.0.1:8000", changeOrigin: false } };

export default defineConfig({
  build: {
    outDir: "dist/client",
  },
  optimizeDeps: {
    include: ["react", "react-dom/client"],
  },
  server: {
    host: "127.0.0.1",
    proxy,
    allowedHosts: ["terminal.local"],
    warmup: {
      clientFiles: ["./src/main.jsx"],
    },
  },
  preview: { host: "127.0.0.1", proxy },
  plugins: [react()],
});
