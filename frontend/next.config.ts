import type { NextConfig } from "next";
import path from "node:path";

const apiPort = process.env.AWB_API_PORT || "8030";
if (!/^\d{4,5}$/.test(apiPort) || Number(apiPort) < 1024 || Number(apiPort) > 65535) {
  throw new Error("invalid_local_api_port");
}
const config: NextConfig = {
  poweredByHeader: false,
  turbopack: { root: path.resolve(process.cwd(), "..") },
  async redirects() {
    return [
      { source: "/workspace/regression", destination: "/workspace/repairs?tab=regression", permanent: false },
      { source: "/workspace/memory", destination: "/workspace", permanent: false },
    ];
  },
  async rewrites() {
    return [
      { source: "/api/repair-runs/catalog", destination: `http://127.0.0.1:${apiPort}/api/repair-runs/catalog` },
      { source: "/api/repair-runs", destination: `http://127.0.0.1:${apiPort}/api/repair-runs` },
      ...["", "/cancel", "/events"].map(suffix => ({ source: `/api/repair-runs/:id([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})${suffix}`, destination: `http://127.0.0.1:${apiPort}/api/repair-runs/:id${suffix}` })),
      { source: "/api/repairs", destination: `http://127.0.0.1:${apiPort}/api/repairs` },
      { source: "/api/repairs/:id([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})", destination: `http://127.0.0.1:${apiPort}/api/repairs/:id` },
      { source: "/api/repairs/:id([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/candidates/:candidate([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/decision", destination: `http://127.0.0.1:${apiPort}/api/repairs/:id/candidates/:candidate/decision` },
      { source: "/api/repairs/:id([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/candidates/:candidate([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/download", destination: `http://127.0.0.1:${apiPort}/api/repairs/:id/candidates/:candidate/download` },
      { source: "/api/repairs/:id([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/candidates/:candidate([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/diff", destination: `http://127.0.0.1:${apiPort}/api/repairs/:id/candidates/:candidate/diff` },
      { source: "/api/repairs/:id([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/comparisons/:comparison([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})", destination: `http://127.0.0.1:${apiPort}/api/repairs/:id/comparisons/:comparison` },
      { source: "/api/analysis-runs/catalog", destination: `http://127.0.0.1:${apiPort}/api/analysis-runs/catalog` },
      { source: "/api/analysis-runs", destination: `http://127.0.0.1:${apiPort}/api/analysis-runs` },
      { source: "/api/analysis-runs/:id([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/cancel", destination: `http://127.0.0.1:${apiPort}/api/analysis-runs/:id/cancel` },
      { source: "/api/analyses/:id([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/evidence", destination: `http://127.0.0.1:${apiPort}/api/analyses/:id/evidence` },
      { source: "/api/analyses", destination: `http://127.0.0.1:${apiPort}/api/analyses` },
      { source: "/api/analyses/:id([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})", destination: `http://127.0.0.1:${apiPort}/api/analyses/:id` },
      { source: "/api/reproductions/catalog", destination: `http://127.0.0.1:${apiPort}/api/reproductions/catalog` },
      { source: "/api/reproductions", destination: `http://127.0.0.1:${apiPort}/api/reproductions` },
      { source: "/api/reproductions/:id([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})/cancel", destination: `http://127.0.0.1:${apiPort}/api/reproductions/:id/cancel` },
      { source: "/api/agent-contracts", destination: `http://127.0.0.1:${apiPort}/api/agent-contracts` },
      { source: "/api/runs", destination: `http://127.0.0.1:${apiPort}/api/runs` },
      { source: "/api/runs/:id([a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})", destination: `http://127.0.0.1:${apiPort}/api/runs/:id` },
      { source: "/api/runs/:id([a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})/report", destination: `http://127.0.0.1:${apiPort}/api/runs/:id/report` },
      { source: "/api/check", destination: `http://127.0.0.1:${apiPort}/api/check` },
      {
        source: "/api/health",
        destination: `http://127.0.0.1:${apiPort}/api/health`,
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};
export default config;
