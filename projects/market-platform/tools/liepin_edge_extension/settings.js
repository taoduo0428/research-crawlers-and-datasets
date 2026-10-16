"use strict";

const TOKEN_STORAGE_KEY = "liepinBridgeRuntimeToken";
const form = document.querySelector("#token-form");
const tokenInput = document.querySelector("#token");
const statusElement = document.querySelector("#status");

async function restore() {
  const stored = await chrome.storage.local.get(TOKEN_STORAGE_KEY);
  const token = stored[TOKEN_STORAGE_KEY];
  if (typeof token === "string" && token) {
    tokenInput.value = token;
    statusElement.textContent = "A runtime token is configured.";
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const token = tokenInput.value.trim();
  if (!token) {
    statusElement.textContent = "Runtime token is required.";
    return;
  }
  await chrome.storage.local.set({ [TOKEN_STORAGE_KEY]: token });
  const response = await chrome.runtime.sendMessage({
    type: "liepin_bridge_token_updated"
  });
  statusElement.textContent = response?.accepted
    ? "Saved. Workers are ready."
    : "Saved, but the background worker did not accept the token.";
});

void restore();
