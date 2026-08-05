/** @type {import('tailwindcss').Config} */
export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // semantic surfaces driven by CSS variables (light/dark)
        bg: "var(--bg)",
        panel: "var(--panel)",
        panel2: "var(--panel2)",
        bd: "var(--bd)",
        fg: "var(--fg)",
        muted: "var(--muted)",
        faint: "var(--faint)",
        // fixed accents (spec)
        pos: "#3fb950",
        neg: "#f85149",
        amber: "#d29922",
        blue: "#58a6ff",
        gold: "#ffd60a",
        purple: "#bc8cff",
      },
      fontFamily: {
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Roboto Mono",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
};
