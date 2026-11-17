"""保留独立运维能力；全部通过统一命令调用包内函数。"""
import importlib
TOOLS = {'chunk', 'extract_figures', 'extract_formulas', 'extract_refs', 'ocr_pages', 'ocr_formulas', 'build_cards', 'build_home', 'build_index', 'apply_reading_cards', 'export_zotero', 'map_pdfs', 'gen_ris', 'audit', 'verify', 'dump_text', 'fetch_openalex', 'fetch_missing', 'clean_formula_latex', 'build_citation_context', 'build_citation_graph', 'build_evolution_graph', 'kb_graph', 'kb_lineage', 'embed', 'embed_extra'}

def run_tool(options, paths):
    if not options or options[0] not in TOOLS:
        raise ValueError('可用工具：' + ', '.join(sorted(TOOLS)))
    name, *args = options
    module = importlib.import_module('kbapp.workflows.tools.' + name)
    return module.main(args) or 0
