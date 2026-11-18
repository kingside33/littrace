import {
  createPaperDataProviders,
  DEFAULT_MAX_CONCURRENT_REQUESTS,
  paperPath,
  repaintStrategy,
} from "./columnDataCore.mjs";
import type { PaperDataProviders } from "./columnDataCore.mjs";
import { serviceClient } from "../service-client";

/**
 * The Zotero adapter behind the 「谱系」 and 「精读」 columns (U1).
 *
 * Everything testable lives in columnDataCore.mjs: the response mapping, the
 * cache, the text formats. This file contributes exactly two things the
 * tests cannot see — the S4 client's `GET /api/v2/paper` call and a
 * throttled item-tree repaint — and neither makes a decision.
 *
 * The request goes through src/service-client.ts rather than Zotero.HTTP
 * directly, so both columns inherit the one request discipline S4 established
 * (`errorDelayMax: 0`, 10 s timeout, no automatic retry). A failed fetch
 * rejects; columnDataCore swallows it into the blank cell, so an offline
 * service shows empty columns and never an error dialog per row.
 *
 * Why a background fetch at all: a column's dataProvider is synchronous and
 * must answer within the list render. On a cache miss it returns "" and a
 * request is started; when the answer lands, the item tree is repainted once
 * (throttled) and the cells fill in.
 */

/** Repaint at most once per this window; a 200-row page is 200 updates. */
const REPAINT_DELAY_MS = 250;

let providers: PaperDataProviders | null = null;
let repaintTimer: ReturnType<typeof setTimeout> | null = null;

/**
 * Repaint every open main window's item tree, once per burst.
 *
 * The strategy comes from `repaintStrategy()` in the tested core: re-sort
 * when the tree is sorted by one of our columns (a plain refresh cannot
 * re-order existing rows — see that function's comment), refresh otherwise.
 * Every call is guarded — a repaint is an optimisation, and a Zotero build
 * without one of these entry points must degrade to "the column updates on
 * the next natural repaint", never to an exception thrown out of a
 * dataProvider. The exact effect of these calls is part of the 真机
 * acceptance list (issue #27); unit tests pin the decision, not the DOM.
 */
function scheduleRepaint(): void {
  if (repaintTimer !== null) {
    return;
  }
  repaintTimer = setTimeout(() => {
    repaintTimer = null;
    try {
      const windows =
        typeof Zotero.getMainWindows === "function" ? Zotero.getMainWindows() : [];
      for (const win of windows) {
        const view = win?.ZoteroPane?.itemsView ?? win?.ZoteroPane_Local?.itemsView;
        const strategy = repaintStrategy(view);
        if (strategy === "sort") {
          // `sort()` is async in Zotero 7+; a rejection must not escape a timer.
          const sorted = view.sort();
          if (sorted && typeof sorted.catch === "function") {
            sorted.catch((error: unknown) => {
              Zotero.logError(
                error instanceof Error
                  ? error
                  : new Error(`littrace: 条目列表重排失败: ${String(error)}`),
              );
            });
          }
        } else if (strategy === "refresh") {
          view.refreshAndMaintainSelection();
        }
      }
    } catch (error) {
      Zotero.logError(
        error instanceof Error
          ? error
          : new Error(`littrace: 条目列表重绘失败: ${String(error)}`),
      );
    }
  }, REPAINT_DELAY_MS);
}

/**
 * The plugin-wide providers. One instance means one cache: both columns read
 * the same `GET /api/v2/paper` answer, so turning both on costs one request
 * per paper, not two.
 */
export function paperData(): PaperDataProviders {
  if (!providers) {
    providers = createPaperDataProviders({
      fetchPaper: (paper: string, context: { libraryID: number; itemKey: string }) => {
        // The path is built by the tested core, so the query encoding cannot
        // drift from what the tests assert.
        const path = paperPath(context.libraryID, context.itemKey);
        if (path.length === 0) {
          return Promise.reject(new Error("littrace: 无文献标识，不发请求"));
        }
        return serviceClient().get(path);
      },
      // Explicit, not the default: one sort click re-reads every row, and the
      // local service is a single process. See DEFAULT_MAX_CONCURRENT_REQUESTS.
      maxConcurrentRequests: DEFAULT_MAX_CONCURRENT_REQUESTS,
      onUpdate: () => scheduleRepaint(),
    });
  }
  return providers;
}

/** Drop every cached answer and repaint (plugin shutdown). */
export function resetPaperData(): void {
  if (repaintTimer !== null) {
    clearTimeout(repaintTimer);
    repaintTimer = null;
  }
  if (providers) {
    providers.invalidateAll();
    providers = null;
  }
}
