// Minimal sandbox globals for the skeleton, hand-written on purpose: the
// full Zotero API typings (zotero-types) are introduced in one piece when
// the first UI feature needs them; see plugin/AGENTS.md.

declare const _globalThis: {
  [key: string]: any;
  addon: import("../src/addon").default;
};

declare const addon: import("../src/addon").default;

declare const Zotero: any;

declare const Services: any;

declare class Localization {
  constructor(resourceIds: Array<string>, sync?: boolean);
  formatMessagesSync(
    messages: Array<{ id: string; args?: Record<string, unknown> }>,
  ): Array<{
    value: string | null;
    attributes: Array<{ name: string; value: string }> | null;
  }>;
}
