import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./design-system/tokens.css";
// Bundled OFL display faces (no CDN): Latin serif for the wordmark and menu, CJK serif for headings and her name.
// Noto Serif CSS splits glyphs by unicode-range, so only the characters on screen are fetched.
import "@fontsource/eb-garamond/latin-400.css";
import "@fontsource/eb-garamond/latin-500.css";
import "@fontsource/eb-garamond/latin-400-italic.css";
import "@fontsource/noto-serif-sc/400.css";
import "@fontsource/noto-serif-jp/400.css";
import "@fontsource/noto-serif-jp/900.css";
import "./App.css";
import "./workstation/workstation.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
