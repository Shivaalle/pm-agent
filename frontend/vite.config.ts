import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const API_TARGET = process.env.VITE_API_PROXY || "http://127.0.0.1:8000";

const apiPaths = [
  "/auth",
  "/capabilities",
  "/projects",
  "/users",
  "/meetings",
  "/teams",
  "/sample-transcript",
  "/drafts",
  "/history",
  "/action-items",
  "/notifications",
  "/forecast",
  "/alerts",
  "/metrics",
  "/overview",
  "/code-activity",
  "/integrations",
  "/series",
  "/privacy",
];

export default defineConfig({
  base: "/app/",
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: Object.fromEntries(
      apiPaths.map((path) => [
        path,
        { target: API_TARGET, changeOrigin: true },
      ]),
    ),
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
