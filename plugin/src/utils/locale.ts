import { config } from "../../package.json";
import { messageId } from "../readerPanelCore.mjs";

/**
 * Minimal Fluent bootstrap: load the plugin's FTL bundle once. The build
 * renames `addon.ftl` to `{addonRef}-addon.ftl` and prefixes the keys, so
 * the resource id and lookups use the prefixed name.
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

/**
 * Look up one Fluent message. Keys are written without the build prefix
 * everywhere in `src/`, so the prefix is added here and nowhere else.
 *
 * A missing message must be visible, not a blank button: fall back to the
 * raw key rather than an empty string, and log the miss so a forgotten FTL
 * entry shows up in the Zotero console during real-machine acceptance.
 */
export function t(key: string): string {
  const id = messageId(key, config.addonRef);
  if (!l10n) {
    Zotero.logError(
      new Error(`littrace: locale used before initLocale() for "${id}"`),
    );
    return id;
  }
  const [message] = l10n.formatMessagesSync([{ id }]);
  if (!message || message.value === null) {
    Zotero.logError(new Error(`littrace: missing Fluent message "${id}"`));
    return id;
  }
  return message.value;
}
