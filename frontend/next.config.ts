import path from "node:path";
import { fileURLToPath } from "node:url";
import type { NextConfig } from "next";

const projectRoot = path.dirname(fileURLToPath(import.meta.url));

// NEXT_EXPORT=1 produces a fully static build (frontend/out) that the backend
// serves from the same origin; dev keeps the /api proxy to the backend port.
// BACKEND_ORIGIN overrides the dev proxy target (e.g. Docker/LAN), defaults to local backend.
const isExport = process.env.NEXT_EXPORT === "1";
const backendOrigin = process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1"],
  turbopack: {
    root: projectRoot,
  },
  ...(isExport
    ? { output: "export" as const, trailingSlash: true }
    : {
        async rewrites() {
          return [
            { source: "/api/:path*", destination: `${backendOrigin}/api/:path*` },
          ];
        },
      }),
};

export default nextConfig;
