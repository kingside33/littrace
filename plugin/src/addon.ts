import { config } from "../package.json";
import hooks from "./hooks";
import { serviceClient } from "./service-client";
import type { ServiceClient } from "./service-client";

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
}

export default Addon;
