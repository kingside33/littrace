"""kbapp.library.page_ocr：由职责边界组织的知识库能力。"""
from __future__ import annotations
from kbapp.integrations.ocr import OCRLengthLimitError

def layout_regions(page):
    """用PDF文字坐标划分宽幅段落与两栏正文；识别内容仍来自页面像素。"""
    import pymupdf
    bounds = page.rect
    middle = bounds.width / 2
    blocks = [(pymupdf.Rect(b[:4]), b[4]) for b in page.get_text('blocks') if b[6] == 0]
    long = [box for box, text in blocks if len(text.strip()) >= 12 and box.width >= bounds.width * 0.25]
    left = [b for b in long if b.x1 <= middle + 8 and b.x0 < middle]
    right = [b for b in long if b.x0 >= middle - 8]
    if len(left) < 2 or len(right) < 2:
        return [bounds]
    wide = sorted([box for box, text in blocks if len(text.strip()) >= 40 and box.width > bounds.width * 0.6 and (box.x0 < middle < box.x1)], key=lambda b: b.y0)

    def padded(box):
        return pymupdf.Rect(box.x0 - 4, box.y0 - 4, box.x1 + 4, box.y1 + 4) & bounds
    result = []
    lower = 0
    for band in wide + [pymupdf.Rect(0, bounds.height, bounds.width, bounds.height)]:
        for column in (left, right):
            items = [b for b in column if lower <= (b.y0 + b.y1) / 2 < band.y0]
            if items:
                start = max(lower, min((b.y0 for b in items)) - 24)
                is_left = column is left
                included = [b for b, text in blocks if start <= (b.y0 + b.y1) / 2 < band.y0 and (b.x0 < middle and b.x1 <= middle + 8 if is_left else b.x0 >= middle - 8)]
                box = pymupdf.Rect(items[0])
                for item in included:
                    box |= item
                result.append(padded(box))
        if band.height:
            result.append(padded(band))
        lower = max(lower, band.y1)
    extra = [padded(box) for box, text in blocks if text.strip() and (not any((region.contains(box) for region in result)))]
    merged = []
    for box in extra:
        overlaps = [r for r in merged if (r & box).get_area() > 0]
        for region in overlaps:
            box |= region
            merged.remove(region)
        merged.append(box)
    regions = result + merged
    separators = sorted([r for r in regions if r.width > bounds.width * 0.6 and r.x0 < middle < r.x1], key=lambda r: r.y0)
    ordered = []
    lower = 0
    for band in separators + [pymupdf.Rect(0, bounds.height, bounds.width, bounds.height)]:
        items = [r for r in regions if r not in separators and lower <= (r.y0 + r.y1) / 2 < band.y0]
        for right_side in (False, True):
            column = [r for r in items if ((r.x0 + r.x1) / 2 >= middle) == right_side]
            if column:
                box = pymupdf.Rect(column[0])
                for part in column[1:]:
                    box |= part
                ordered.append(pymupdf.Rect(min(middle, box.x0) if right_side else bounds.x0, lower, bounds.x1 if right_side else max(middle, box.x1), band.y0) & bounds)
        if band.height:
            ordered.append(band)
        lower = max(lower, band.y1)
    return ordered or [bounds]

def split_ocr_region(page, region):
    """尽量沿文字行间空白分割；扫描页无文字坐标时采用小幅重叠。"""
    import pymupdf
    vertical = region.width > region.height * 1.2
    start, end = (region.x0, region.x1) if vertical else (region.y0, region.y1)
    extent = end - start
    if extent < 72:
        return []
    middle = (start + end) / 2
    intervals = []
    for word in page.get_text('words'):
        box = pymupdf.Rect(word[:4])
        if (box & region).get_area() <= 0:
            continue
        a, b = (box.x0, box.x1) if vertical else (box.y0, box.y1)
        intervals.append((max(start, a), min(end, b)))
    merged = []
    for a, b in sorted(intervals):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    gaps = [(a[1] + b[0]) / 2 for a, b in zip(merged, merged[1:]) if b[0] - a[1] >= 2 and start + extent * 0.2 < (a[1] + b[0]) / 2 < end - extent * 0.2]
    cut = min(gaps, key=lambda x: abs(x - middle)) if gaps else middle
    overlap = 0 if gaps else min(6, extent * 0.02)
    if vertical:
        return [pymupdf.Rect(region.x0, region.y0, cut + overlap, region.y1), pymupdf.Rect(cut - overlap, region.y0, region.x1, region.y1)]
    return [pymupdf.Rect(region.x0, region.y0, region.x1, cut + overlap), pymupdf.Rect(region.x0, cut - overlap, region.x1, region.y1)]

def recognize_page(client, page, progress):
    import pymupdf
    regions = layout_regions(page)
    parts = []
    completed_regions = []

    def recognize(region, depth=0):
        try:
            text = client.page_to_text(page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=region).tobytes('png'))
        except OCRLengthLimitError:
            children = split_ocr_region(page, region) if depth < 6 else []
            if not children:
                raise
            progress(f'GLM-OCR 输出不完整，细分页面区域后重试：p.{page.number + 1}')
            for child in children:
                recognize(child, depth + 1)
            return
        completed_regions.append(list(region))
        if text.strip():
            parts.append(text)
    for n, region in enumerate(regions, 1):
        if len(regions) > 1:
            progress(f'GLM-OCR 版面分区识别 {n}/{len(regions)}：p.{page.number + 1}')
        recognize(region)
    return ('\n\n'.join(parts), completed_regions)

def blank_page(page):
    import pymupdf
    import numpy as np
    pix = page.get_pixmap(matrix=pymupdf.Matrix(0.5, 0.5), colorspace=pymupdf.csGRAY)
    gray = np.frombuffer(pix.samples, dtype=np.uint8)
    return bool(len(gray) and np.count_nonzero(gray < 220) / len(gray) < 5e-05)
