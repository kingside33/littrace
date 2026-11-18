import {
  createColumnRegistration,
  readingColumnOption,
} from "./lineageCore.mjs";
import type { LineageColumnRegistration } from "./lineageCore.mjs";
import { readingBadgeForText } from "./columnDataCore.mjs";
import type { ReadingCellBadge } from "./columnDataCore.mjs";
import { paperData } from "./paperData";
import { config } from "../../package.json";

/**
 * 「精读」item-tree column (U1).
 *
 * Same registration model as 「谱系」: process-wide, registered from
 * src/registry.ts's registerWindowIndependent(), default-off until the user
 * enables it from the column picker (no `defaultIn`), persisted width /
 * hidden / sortDirection.
 *
 * The value is the paper's reading coverage, taken from the `understanding`
 * field of `GET /api/v2/paper` (kbapp/research/reading.py:148):
 *
 *   understanding === null                 → 未精读 (the service answered:
 *                                            no reading artifact yet)
 *   coverage.complete === true, total > 0  → 已精读 · 已读/总页数
 *   otherwise                              → 部分精读 · 已读/总页数
 *   coverage missing / not understood      → "" (no answer — blank cell,
 *                                            never a fabricated state)
 *
 * Both columns share one cache (src/columns/paperData.ts), so enabling both
 * doubles nothing on the wire.
 */

const pluginID: string = config.addonID;

function renderReadingCell(
  index: number,
  data: string,
  column: { className?: string },
  _isFirstColumn: boolean,
  doc: Document,
): HTMLElement {
  const cell = doc.createElement("span");
  cell.className = `cell ${column?.className ?? ""}`.trim();

  const badge = readingBadgeForText(data) as ReadingCellBadge | null;
  // Unknown / offline / malformed: a blank cell, never a broken row.
  if (!badge) {
    return cell;
  }

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

  const pages = doc.createElement("span");
  // The page progress is the number; 未精读 has none, so the cell shows only
  // the glyph. The full sentence stays in the title.
  pages.textContent =
    badge.reading.state === "none"
      ? ""
      : `${badge.reading.readPages}/${badge.reading.totalPages}`;
  pages.style.opacity = "0.85";
  cell.appendChild(pages);

  cell.title = badge.title;
  symbol.title = badge.title;

  return cell;
}

/**
 * Build the column registration. The caller (src/registry.ts) owns the
 * registry call; registration failures are logged, never thrown.
 */
export function createReadingColumn(): LineageColumnRegistration {
  const registration = createColumnRegistration(
    Zotero.ItemTreeManager,
    pluginID,
    {
      dataProvider: (item: unknown, dataKey: string) =>
        paperData().readingProvider(item, dataKey),
      renderCell: renderReadingCell,
    },
    readingColumnOption(pluginID),
  );

  if (!registration.dataKey) {
    Zotero.logError(
      new Error(
        "littrace: 「精读」自定义列注册失败(Zotero 拒绝了列选项),本机功能暂不可用。",
      ),
    );
  }

  return registration;
}
