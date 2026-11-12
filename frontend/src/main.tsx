import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./theme"; // sets data-theme before the first render
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
