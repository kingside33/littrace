import { config } from "../package.json";
import hooks from "./hooks";

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
}

export default Addon;
