import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // A self-contained server in .next/standalone with only the node_modules files it uses, so the
  // Docker image does not need the whole installation.
  output: "standalone",
  turbopack: {
    resolveAlias: {
      // PDF.js's legacy build carries polyfills for browsers a few versions behind the newest,
      // such as Safari. react-pdf imports "pdfjs-dist"; this points it to that build.
      "pdfjs-dist": "pdfjs-dist/legacy/build/pdf.mjs",
    },
  },
};

export default nextConfig;
