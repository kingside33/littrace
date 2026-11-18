import Addon from "./addon";
import { config } from "../package.json";

/**
 * Sandbox entry point. bootstrap.js loads this bundle into a sandbox whose
 * `_globalThis` is the plugin root scope. Exactly one Addon instance is
 * installed on the Zotero global so bootstrap.js and later features can
 * reach it as `Zotero.Littrace`.
 */
if (!Zotero[config.addonInstance]) {
  const addon = new Addon();
  _globalThis.addon = addon;
  Zotero[config.addonInstance] = addon;
}
