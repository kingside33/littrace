import { t } from "../utils/locale";
import type { TestResultView } from "./settingsCore.mjs";

/**
 * The settings page's server-side half: rendering a probe result as one line
 * of text. This is the only place a Fluent key is chosen at *render* time
 * rather than declared in markup, so the keys are written out literally and
 * tests/test_plugin_settings.cjs scans this file for them — a key that escapes
 * settingsCore's registry fails CI instead of rendering blank in a locale
 * nobody checked.
 *
 * The read/write plumbing (load, save, test, clear) lands with the pane in the
 * next issue; the rules about *what* to send already live in settingsCore.mjs
 * and are covered there.
 */

/**
 * One probe result as display text.
 *
 * Three parts, each optional and each from Fluent:
 *   - the verdict itself (`prefs-probe-*`, one per code);
 *   - 「配置的模型不在列表中」 when the service could compare and did not find
 *     the configured model — a real finding even though the transport worked;
 *   - 「本地代理未使用，已回退到系统网络」 when the request fell back, because
 *     the user's next action depends on knowing which route was taken.
 *
 * `detail` is the service's own one-line summary. It is passed as a Fluent
 * argument (`{ $detail }`) rather than concatenated, matching how the menus
 * render a server `detail` (`menu-feedback-compare-error`).
 */
export function probeStatusText(view: TestResultView): string {
  const parts = [t(view.labelKey)];
  if (view.modelListed === false) {
    parts.push(t("prefs-cloud-model-not-listed"));
  }
  if (view.networkRoute === "system_fallback") {
    parts.push(t("prefs-probe-proxy-fallback"));
  }
  const text = parts.join(" ");
  return view.detail ? `${text} ${t("prefs-probe-detail", { detail: view.detail })}` : text;
}

/** Lifecycle strings the pane's status row can show. */
export const STATUS_KEYS = Object.freeze({
  loading: "prefs-cloud-loading",
  loadFailed: "prefs-cloud-load-failed",
  saving: "prefs-cloud-saving",
  saved: "prefs-cloud-saved",
  saveFailed: "prefs-cloud-save-failed",
  testing: "prefs-cloud-testing",
  testFailed: "prefs-cloud-test-failed",
  clearDone: "prefs-cloud-clear-done",
  clearFailed: "prefs-cloud-clear-failed",
  platformUnsupported: "prefs-platform-cloud-unsupported",
});

/** One lifecycle string by role. */
export function statusText(role: keyof typeof STATUS_KEYS, args?: Record<string, unknown>): string {
  return t(STATUS_KEYS[role], args);
}
