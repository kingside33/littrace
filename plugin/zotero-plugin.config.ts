import { defineConfig } from "zotero-plugin-scaffold";
import pkg from "./package.json";

// Build/run configuration.
//
// The XPI build never needs a Zotero binary: `npm run build` only bundles
// and zips, so CI can build without Zotero. Running against a real Zotero
// (`npm start`, i.e. `zotero-plugin serve`) needs binPath/profilePath, and
// they are deliberately NOT written into this file - no machine-absolute
// paths in the repository. Resolve them per environment, in order of
// precedence: CLI flags (`--zotero.bin-path` / `--zotero.profile-path`),
// then environment variables
// (ZOTERO_PLUGIN_ZOTERO_BIN_PATH / ZOTERO_PLUGIN_PROFILE_PATH), then a local
// plugin/.env (copy plugin/.env.example; the file is gitignored).
export default defineConfig({
  source: ["src", "addon"],
  dist: ".scaffold/build",
  name: pkg.config.addonName,
  id: pkg.config.addonID,
  namespace: pkg.config.addonRef,

  build: {
    assets: ["addon/**/*.*"],
    prefs: {
      prefix: pkg.config.prefsPrefix,
    },
    esbuildOptions: [
      {
        entryPoints: ["src/index.ts"],
        bundle: true,
        target: "firefox115",
        outfile: `.scaffold/build/addon/content/scripts/${pkg.config.addonRef}.js`,
      },
    ],
  },
});
