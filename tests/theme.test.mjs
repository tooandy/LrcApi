import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../admin/static/theme.js", import.meta.url), "utf8");

function renderPreference(pref, denyStorage = false) {
  const values = new Map();
  if (pref !== undefined) values.set("home-lrcapi-theme-v1", pref);
  const events = {};
  const select = {
    value: "",
    addEventListener(name, handler) { events["select:" + name] = handler; },
  };
  const document = {
    documentElement: { dataset: {} },
    addEventListener(name, handler) { events[name] = handler; },
    getElementById(name) { return name === "theme-select" ? select : null; },
  };
  const localStorage = {
    getItem(key) { if (denyStorage) throw Error("Storage blocked"); return values.get(key) ?? null; },
    setItem(key, value) { if (denyStorage) throw Error("Storage blocked"); values.set(key, value); },
  };
  vm.runInNewContext(source, { document, localStorage, Set });
  events.DOMContentLoaded();
  return { document, events, select, values };
}

test("theme defaults to device preference and persists manual changes", () => {
  const view = renderPreference();
  assert.equal(view.document.documentElement.dataset.theme, "system");
  assert.equal(view.select.value, "system");
  view.select.value = "dark";
  view.events["select:change"]();
  assert.equal(view.document.documentElement.dataset.theme, "dark");
  assert.equal(view.values.get("home-lrcapi-theme-v1"), "dark");
  view.select.value = "light";
  view.events["select:change"]();
  assert.equal(view.document.documentElement.dataset.theme, "light");
  view.select.value = "system";
  view.events["select:change"]();
  assert.equal(view.document.documentElement.dataset.theme, "system");
});

test("saved dark preference applies before stylesheet loads", () => {
  assert.equal(renderPreference("dark").document.documentElement.dataset.theme, "dark");
  assert.equal(renderPreference("light").select.value, "light");
});

test("invalid saved preference and storage denial fail back to system", () => {
  assert.equal(renderPreference("invalid").document.documentElement.dataset.theme, "system");
  const noStorage = renderPreference("dark", true);
  assert.equal(noStorage.document.documentElement.dataset.theme, "system");
  noStorage.select.value = "dark";
  noStorage.events["select:change"]();
  assert.equal(noStorage.document.documentElement.dataset.theme, "dark");
  noStorage.select.value = "unknown";
  noStorage.events["select:change"]();
  assert.equal(noStorage.document.documentElement.dataset.theme, "dark");
});
