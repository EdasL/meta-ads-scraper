import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async redirects() {
    return [
      // Routes renamed in the 2026 UI redesign.
      { source: "/board", destination: "/discover", permanent: true },
      { source: "/review", destination: "/saved", permanent: true },
    ];
  },
};

export default nextConfig;
