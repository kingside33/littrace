import {
  badgeForText,
  createColumnRegistration,
  createLocatePayload,
  dispatchLineageLocate,
  itemKeyOf,
  itemRefOf,
  relationCount,
} from "./lineageCore.mjs";
import type {
  LineageCellBadge,
  LineageColumnRegistration,
} from "./lineageCore.mjs";
import { paperData } from "./paperData";
import { config } from "../../package.json";

/**
 * 「谱系」item-tree column (U1: real data).
 *
 * Registration goes through src/registry.ts like every other feature, but as
 * a *global* registration: columns are not window-scoped. Zotero keeps one
 * column registry per process, and the ItemTree reads it on refresh, so
 * closing a single main window must not unregister the column for the others
 * (ADR 0009). Disable/uninstall reclaims it in unregisterAll.
 *
 * The data now comes from the local service via src/columns/paperData.ts. A
 * dataProvider is synchronous, so the first time a row is drawn the cache has
 * nothing and the cell is blank; the fetch that miss starts lands a moment
 * later and the tree is repainted. Offline and error cases stay blank — this
 * column never invents a badge (issue #27 需求 3).
 *
 * The data and its contract live in lineageCore.mjs / columnDataCore.mjs
 * (plain ESM) so tests/test_plugin_column.cjs runs the real logic without
 * Zotero. `renderCell` cannot be tested that way: it needs a Document. The
 * DOM-independent half (what to draw, the click payload) is in the core.
 */

/** Stabilise the XPI's `config` typing; only the addon ID is used here. */
const pluginID: string = config.addonID;

function renderLineageCell(
  index: number,
  data: string,
  column: { className?: string },
  _isFirstColumn: boolean,
  doc: Document,
): HTMLElement {
  const cell = doc.createElement("span");
  cell.className = `cell ${column?.className ?? ""}`.trim();

  const badge = badgeForText(data) as LineageCellBadge | null;
  // Empty / malformed / offline text: a blank cell, never a broken row.
  if (!badge) {
    return cell;
  }

  // `this` is undefined here: Zotero wraps option functions in an arrow
  // function (pluginAPIBase.mjs), so the item tree is reached through the
  // document instead. See itemRefOf().
  const item = itemRefOf(undefined, index, doc);
  const itemKey = itemKeyOf(item);

  cell.style.display = "flex";
  cell.style.alignItems = "center";
  cell.style.gap = "4px";

  const symbol = doc.createElement("span");
  symbol.textContent = badge.symbol;
  symbol.style.color = badge.color;
  symbol.style.fontSize = "12px";
  symbol.style.lineHeight = "1";
  symbol.className = badge.className;
  cell.appendChild(symbol);

  const count = doc.createElement("span");
  // Always the confirmed relation count (issue #22 需求 3, ADR 0012): 「三态
  // 徽标 + 已确认关系数」. The state is the glyph, the breakdown is the title.
  count.textContent = String(relationCount(badge.counts));
  count.style.opacity = "0.85";
  cell.appendChild(count);

  const title = badge.title;
  cell.title = title;
  symbol.title = title;

  if (itemKey) {
    // Open the 谱系 tab positioned on this paper (U1 需求 5). The payload is
    // dispatched, not handled here: the tab belongs to another feature.
    cell.style.cursor = "pointer";
    cell.addEventListener("click", (event) => {
      const libraryID = (item as { libraryID?: unknown })?.libraryID;
      const payload = createLocatePayload({
        itemKey,
        libraryID,
        state: badge.state,
        // The identity `GET /api/v2/paper?paper=` accepts, so the listener
        // can open the tab with `?paper=` without a second lookup.
        paper: paperData().identityOf(item)?.key ?? null,
      });
      if (payload) {
        // Dispatch on the item tree's document: a document-level dispatch
        // bubbles to the window, so either scope can listen without the
        // column deciding where the 谱系 tab lives.
        dispatchLineageLocate(doc, payload);
      }
      event.stopPropagation();
    });
  }

  return cell;
}

/**
 * Build the column registration. The caller (src/registry.ts) owns the
 * plugin registry call, so this module never imports the registry and the
 * two do not form an import cycle.
 *
 * Registration failures are reported, not thrown: one bad feature must not
 * abort startup. `dataKey` is null when Zotero rejected the option.
 */
export function createLineageColumn(): LineageColumnRegistration {
  const registration = createColumnRegistration(
    Zotero.ItemTreeManager,
    pluginID,
    {
      dataProvider: (item: unknown, dataKey: string) =>
        paperData().lineageProvider(item, dataKey),
      renderCell: renderLineageCell,
    },
  );

  if (!registration.dataKey) {
    Zotero.logError(
      new Error(
        "littrace: 「谱系」自定义列注册失败(Zotero 拒绝了列选项),本机功能暂不可用。",
      ),
    );
  }

  return registration;
}
