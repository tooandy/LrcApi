"use strict";
// Runs before stylesheets to apply the previously chosen theme without a flash.
// Storage is per browser/device, not part of SQLite or the music library.
(() => {
  const key = "home-lrcapi-theme-v1";
  const allowed = new Set(["system", "light", "dark"]);
  let mode = "system";
  try {
    const stored = localStorage.getItem(key);
    if (allowed.has(stored)) mode = stored;
  } catch (_) {
    // Restricted browser storage: fall back to device preference.
  }
  document.documentElement.dataset.theme = mode;
  document.addEventListener("DOMContentLoaded", () => {
    const selector = document.getElementById("theme-select");
    if (!selector) return;
    selector.value = mode;
    selector.addEventListener("change", () => {
      if (!allowed.has(selector.value)) return;
      document.documentElement.dataset.theme = selector.value;
      try { localStorage.setItem(key, selector.value); } catch (_) {}
    });
  });
})();
