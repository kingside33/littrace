import { config } from "../../package.json";

/**
 * Minimal Fluent bootstrap: load the plugin's FTL bundle once. The build
 * renames `addon.ftl` to `{addonRef}-addon.ftl` and prefixes the keys, so
 * the resource id and lookups use the prefixed name. Message-lookup helpers
 * arrive with the first UI feature that needs them.
 */
let l10n: Localization | undefined;

function getLocalizationCtor(): typeof Localization {
  // The global exists on the sandbox in current Zotero builds; fall back to
  // the main window for environments where it is only exposed there.
  const g = globalThis as { Localization?: typeof Localization };
  if (typeof g.Localization !== "undefined") {
    return g.Localization;
  }
  const win = Zotero.getMainWindows()[0] as unknown as {
    Localization: typeof Localization;
  };
  return win.Localization;
}

export function initLocale(): void {
  if (l10n) {
    return;
  }
  l10n = new (getLocalizationCtor())(
    [`${config.addonRef}-addon.ftl`],
    true,
  );
}
