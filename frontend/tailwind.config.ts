import type { Config } from "tailwindcss";

// ORBIT AI colour tokens (same palette as the full platform).
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        orbit: {
          bg: "#090D18",
          sidebar: "#101727",
          panel: "#0D1322",
          card: "#151F32",
          cardHover: "#1B2740",
          border: "#1E293B",
          borderStrong: "#334155",
          purple: "#795CFF",
          purpleHover: "#6849F5",
          purpleLight: "#A78BFA",
          cyan: "#00D4FF",
          green: "#20C997",
          amber: "#F59E0B",
          red: "#EF4444",
          redLight: "#F87171",
          text: "#F8FAFC",
          muted: "#8B9BB4",
          subtle: "#64748B",
          nav: "#94A3B8",
        },
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Fira Code", "monospace"],
      },
      boxShadow: {
        glow: "0 0 16px rgba(121,92,255,0.35)",
        glowStrong: "0 0 15px rgba(121,92,255,0.4)",
      },
    },
  },
  plugins: [],
};
export default config;
