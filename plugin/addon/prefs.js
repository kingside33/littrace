// Pref declarations for littrace. The scaffold prefixes every key with
// `extensions.zotero.littrace` at build time (zotero-plugin.config.ts ->
// build.prefs.prefix) and regenerates typings/prefs.d.ts accordingly.
//
// One pref per feature, never a shared "advanced" switch (U5). The table in
// plugin/src/prefs/prefsCore.mjs is the same list with types, defaults, groups
// and Fluent labels, and tests/test_plugin_prefs.cjs fails when the two drift.
//
// `connection.port` is a string on purpose: Zotero's declarative <preference>
// binding writes the input's string value, and prefsCore.normalizePort() is
// the validator (invalid text falls back to 8765).
//
// The two `pro.*` keys are M3 placeholders: declared so the pane can show them
// disabled with 「需 Pro」, and read by nothing in the free plugin.
// `cache.dir` is a string like `connection.port`: empty means "the service's
// default (<data>/cache)". Its value is not consumed inside Zotero at all — the
// pane forwards it to POST /api/settings and the service decides where its parse
// caches live (issue #61). prefsCore.normalizeCacheDir() is the validator.
pref("enable", true);
pref("connection.port", "8765");
pref("integration.column.lineage", true);
pref("integration.column.reading", true);
pref("cache.dir", "");
pref("pro.cross-check-report", false);
pref("pro.parallel-driver", false);
