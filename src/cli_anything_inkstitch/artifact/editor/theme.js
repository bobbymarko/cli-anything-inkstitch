/* Colour themes. "default" = the tokens in design-system.css. Catppuccin
   flavours are generated from @catppuccin/palette@1.7.1 (MIT, (c) 2021
   Catppuccin; THIRD_PARTY_NOTICES.md), mapped onto our roles per the
   Catppuccin style guide (docs/style-guide.md, General Usage + Terminals). */
const THEMES = {
 "catppuccin-latte": {
  "label": "Catppuccin Latte",
  "light": true,
  "vars": {
   "--bg": "#eff1f5",
   "--panel": "#e6e9ef",
   "--canvas": "#dce0e8",
   "--field": "#eff1f5",
   "--line": "#ccd0da",
   "--text": "#4c4f69",
   "--muted": "#6c6f85",
   "--accent": "#1e66f5",
   "--start": "#40a02b",
   "--end": "#d20f39",
   "--warn": "#df8e1d",
   "--rail": "#fe640b",
   "--rung": "#8839ef",
   "--zig": "#dc8a78",
   "--guide": "#179299",
   "--on-accent": "#eff1f5",
   "--handle": "#eff1f5",
   "--scrim": "#dce0e8",
   "--raised": "#eff1f5"
  }
 },
 "catppuccin-frappe": {
  "label": "Catppuccin Frappé",
  "light": false,
  "vars": {
   "--bg": "#303446",
   "--panel": "#292c3c",
   "--canvas": "#232634",
   "--field": "#303446",
   "--line": "#414559",
   "--text": "#c6d0f5",
   "--muted": "#a5adce",
   "--accent": "#8caaee",
   "--start": "#a6d189",
   "--end": "#e78284",
   "--warn": "#e5c890",
   "--rail": "#ef9f76",
   "--rung": "#ca9ee6",
   "--zig": "#f2d5cf",
   "--guide": "#81c8be",
   "--on-accent": "#303446",
   "--handle": "#c6d0f5",
   "--scrim": "#232634",
   "--raised": "#414559"
  }
 },
 "catppuccin-macchiato": {
  "label": "Catppuccin Macchiato",
  "light": false,
  "vars": {
   "--bg": "#24273a",
   "--panel": "#1e2030",
   "--canvas": "#181926",
   "--field": "#24273a",
   "--line": "#363a4f",
   "--text": "#cad3f5",
   "--muted": "#a5adcb",
   "--accent": "#8aadf4",
   "--start": "#a6da95",
   "--end": "#ed8796",
   "--warn": "#eed49f",
   "--rail": "#f5a97f",
   "--rung": "#c6a0f6",
   "--zig": "#f4dbd6",
   "--guide": "#8bd5ca",
   "--on-accent": "#24273a",
   "--handle": "#cad3f5",
   "--scrim": "#181926",
   "--raised": "#363a4f"
  }
 },
 "catppuccin-mocha": {
  "label": "Catppuccin Mocha",
  "light": false,
  "vars": {
   "--bg": "#1e1e2e",
   "--panel": "#181825",
   "--canvas": "#11111b",
   "--field": "#1e1e2e",
   "--line": "#313244",
   "--text": "#cdd6f4",
   "--muted": "#a6adc8",
   "--accent": "#89b4fa",
   "--start": "#a6e3a1",
   "--end": "#f38ba8",
   "--warn": "#f9e2af",
   "--rail": "#fab387",
   "--rung": "#cba6f7",
   "--zig": "#f5e0dc",
   "--guide": "#94e2d5",
   "--on-accent": "#1e1e2e",
   "--handle": "#cdd6f4",
   "--scrim": "#11111b",
   "--raised": "#313244"
  }
 }
};
function applyTheme(id) {
  const root = document.documentElement;
  for (const t of Object.values(THEMES))
    for (const k of Object.keys(t.vars)) root.style.removeProperty(k);
  const t = THEMES[id];
  if (t) for (const [k, v] of Object.entries(t.vars)) root.style.setProperty(k, v);
  root.style.colorScheme = t && t.light ? "light" : "dark";
  root.dataset.theme = t ? id : "default";
}
try { applyTheme(localStorage.getItem("artifact-theme") || "default"); } catch (e) { /* defaults */ }
