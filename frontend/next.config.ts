import path from "node:path";
import { fileURLToPath } from "node:url";
import type { NextConfig } from "next";

const projectRoot = path.dirname(fileURLToPath(import.meta.url));

// NEXT_EXPORT=1 produces a fully static build (frontend/out) that the backend
// serves from the same origin; dev keeps the /api proxy to the backend port.
const isExport = process.env.NEXT_EXPORT === "1";

const nextConfig: NextConfig = {
  turbopack: {
    root: projectRoot,
  },
  allowedDevOrigins: ["115.25.46.*"],
  ...(isExport
    ? { output: "export" as const, trailingSlash: true }
    : {
        async rewrites() {
          return [
            { source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" },
          ];
        },
      }),
};

export default nextConfig;
