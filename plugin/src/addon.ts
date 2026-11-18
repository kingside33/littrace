import { config } from "../package.json";
import hooks from "./hooks";
import { serviceClient } from "./service-client";
import type { ServiceClient } from "./service-client";
import { prefsPane } from "./prefs/prefs";
import { comparisonSet } from "./menus/menus";

/**
 * The single plugin instance. Holds shared state and the lifecycle hooks;
 * everything that registers with Zotero goes through src/registry.ts.
 */
class Addon {
  data: {
    alive: boolean;
    config: typeof config;
    initialized: boolean;
    locale?: { current: Localization };
  };
  hooks: typeof hooks;

  constructor() {
    this.data = {
      alive: true,
      config,
      initialized: false,
    };
    this.hooks = hooks;
  }

  /**
   * The one client for the local service. Features use this instead of
   * building requests themselves, so the token cache, the `errorDelayMax: 0`
   * discipline and the failure semantics cannot drift per feature (S4).
   */
  get service(): ServiceClient {
    return serviceClient();
  }

  /**
   * The two live rows of the settings page (platform card, service health).
   *
   * The pane fragment's inline `onload`/`oncommand` handlers run in the
   * preferences window, not in this sandbox; they reach this object through
   * the plugin instance Zotero exposes as `Zotero.Littrace` (src/index.ts).
   * That is the same instance Zotero's own panes use for their init calls
   * (`preferences_general.xhtml`'s `onload`).
   */
  get prefsPane(): typeof prefsPane {
    return prefsPane;
  }

  /**
   * The in-memory comparison set (U6), as service paper ids.
   *
   * 「加入对比集」 fills it from the item context menu. U3's reader panel does
   * not expose a comparison entry yet, so this accessor is the reserved
   * consumer interface: when that entry lands it reads
   * `Zotero.Littrace.comparisonSet` instead of growing a second set. The set
   * is process memory on purpose and is emptied on shutdown.
   */
  get comparisonSet(): ReadonlyArray<string> {
    return comparisonSet();
  }
}

export default Addon;
