import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  async redirects() {
    return [
      // Routes renamed in the 2026 UI redesign.
      { source: "/board", destination: "/discover", permanent: true },
    ];
  },
};

export default nextConfig;
