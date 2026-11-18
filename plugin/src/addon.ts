import { config } from "../package.json";
import hooks from "./hooks";
import { serviceClient } from "./service-client";
import type { ServiceClient } from "./service-client";
import { prefsPane } from "./prefs/prefs";

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
}

export default Addon;
