/** Entry point. Identical under Vite and under the in-browser loader. */
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

const container = document.getElementById("root");
if (!container) {
  throw new Error("#root is missing from index.html");
}

if (!window.location.hash) {
  window.location.hash = "/dashboard";
}

ReactDOM.createRoot(container).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
