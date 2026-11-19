/**
 * Types for settingsCore.mjs; the implementation is plain ESM on purpose so
 * tests/test_plugin_settings.cjs can run it without Zotero or a build step.
 * `tsc` does not type-check the .mjs file itself (see ADR 0010), so this
 * declaration is the single typed view of the module.
 */

export type SettingsFieldType = "text" | "password" | "number" | "bool" | "select";

export interface SettingsField {
  /** The exact `kbapp/core/config.py` DEFAULTS key. */
  key: string;
  type: SettingsFieldType;
  /** Which pane group draws the row. */
  section: "cloud" | "connection";
  /** The service answers with a boolean for this key, never the value. */
  sensitive: boolean;
  labelKey: string;
  descriptionKey: string;
}

export interface SettingsGroup {
  id: string;
  titleKey: string;
  descriptionKey: string;
}

/** Form values: strings for text/number/select, booleans for bool/sensitive. */
export type SettingsFormValues = Record<string, string | boolean>;

export interface SettingsView {
  fields: SettingsFormValues;
  budgetMode: string;
}

export interface TestResultView {
  tone: "ok" | "warn" | "bad";
  labelKey: string;
  detail: string | null;
  networkRoute: string | null;
  modelListed: boolean | null;
}

export const SETTINGS_GROUPS: ReadonlyArray<Readonly<SettingsGroup>>;
export const SETTINGS_FIELDS: ReadonlyArray<Readonly<SettingsField>>;
export const BUDGET_MODES: ReadonlyArray<string>;
export const TEST_CODES: Readonly<Record<string, string>>;
export const TEST_CODE_KEYS: Readonly<Record<string, string>>;
export const TEST_TONES: Readonly<{ OK: "ok"; WARN: "warn"; BAD: "bad" }>;
export const PANE_FLUENT_KEYS: ReadonlyArray<string>;

export interface VersionView {
  serviceVersion: string | null;
  minServiceVersion: string | null;
  relationKey: string | null;
  tone: string;
}

export function settingsField(key: string): Readonly<SettingsField> | null;
export function fieldsForSection(section: string): Array<Readonly<SettingsField>>;
export function versionViewFrom(rows: unknown): VersionView;
export function settingsViewFrom(publicSettings: unknown): SettingsView;
export function keyStored(view: SettingsView | null | undefined): boolean;
export function savePayloadFrom(
  form: Record<string, unknown>,
  section?: string | null,
): Record<string, unknown>;
export function clearKeyPayload(): { api_key: ""; cloud_enabled: false };
export function testResultView(response: unknown): TestResultView;
export function proxyNoteKey(view: TestResultView | null | undefined): string | null;
export function settingsFluentKeys(): string[];
