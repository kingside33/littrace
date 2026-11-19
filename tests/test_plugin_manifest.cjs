"use strict";

// Anchors the plugin manifest contract (A1 插件骨架与构建链):
// - pluginID is the A0-decided id (name@domain, no "Zotero", ADR on naming);
// - supported Zotero range is strict_min_version 8.0 with an x.x.* max;
// - the manifest version exists and matches plugin/package.json;
// - bootstrap.js keeps the full lifecycle so window-level registrations can
//   be reclaimed;
// - both locale packs exist.
// Node built-ins only: this file runs in CI without any npm install.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const PLUGIN_DIR = path.join(__dirname, "..", "plugin");
const ADDON_DIR = path.join(PLUGIN_DIR, "addon");

function readJson(relativePath) {
  return JSON.parse(fs.readFileSync(path.join(PLUGIN_DIR, relativePath), "utf8"));
}

test("manifest carries the A0-decided plugin id", () => {
  const manifest = readJson("addon/manifest.json");
  const id = manifest.applications.zotero.id;
  // A0 (2026-11-19): name@domain, settled before any public beta. The
  // placeholder form that A1 shipped with must be gone — a Zotero install
  // that saw the placeholder would treat the renamed plugin as a second one.
  assert.equal(id, "littrace@kingside33.github.io");
  assert.match(id, /^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/);
});

test("manifest pins the supported Zotero range", () => {
  const manifest = readJson("addon/manifest.json");
  const app = manifest.applications.zotero;
  assert.equal(app.strict_min_version, "8.0");
  assert.match(app.strict_max_version, /^\d+\.\d+\.\*$/);
});

test("manifest declares a version equal to package.json", () => {
  const manifest = readJson("addon/manifest.json");
  const pkg = readJson("package.json");
  assert.match(manifest.version, /^\d+\.\d+\.\d+$/);
  assert.equal(manifest.version, pkg.version);
});

test("manifest and package.json agree on the A0 addon id", () => {
  const manifest = readJson("addon/manifest.json");
  const pkg = readJson("package.json");
  assert.equal(manifest.applications.zotero.id, pkg.config.addonID);
  // A0 (maintainer decision): the id is the settled form, not a placeholder.
  assert.equal(pkg.config.addonID, "littrace@kingside33.github.io");
  assert.doesNotMatch(pkg.config.addonID, /example\.invalid/);
  // Trademark rule: neither the display name nor the id may carry the word.
  assert.doesNotMatch(pkg.config.addonID, /zotero/i);
  assert.doesNotMatch(pkg.config.addonName, /zotero/i);
});

test("bootstrap.js keeps the full minimal lifecycle", () => {
  const bootstrap = fs.readFileSync(path.join(ADDON_DIR, "bootstrap.js"), "utf8");
  for (const hook of [
    "function install",
    "async function startup",
    "async function onMainWindowLoad",
    "async function onMainWindowUnload",
    "async function shutdown",
    "async function uninstall",
  ]) {
    assert.ok(bootstrap.includes(hook), `bootstrap.js must define ${hook}`);
  }
});

test("locale packs exist for en-US and zh-CN", () => {
  for (const locale of ["en-US", "zh-CN"]) {
    const ftl = path.join(ADDON_DIR, "locale", locale, "addon.ftl");
    assert.ok(fs.existsSync(ftl), `missing ${locale} addon.ftl`);
    const content = fs.readFileSync(ftl, "utf8");
    assert.match(content, /^startup-begin =/m);
    assert.match(content, /^startup-finish =/m);
  }
});

test("plugin/.gitignore keeps build output and node_modules out of git", () => {
  const gitignore = fs.readFileSync(path.join(PLUGIN_DIR, ".gitignore"), "utf8");
  assert.match(gitignore, /^\.scaffold$/m);
  assert.match(gitignore, /^node_modules$/m);
  assert.match(gitignore, /^\.env$/m);
});
