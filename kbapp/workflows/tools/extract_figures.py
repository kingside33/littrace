import argparse
import sys
import kbapp.core.config as C
from kbapp.library.figures import process_collection

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=C.BASE)
    ap.add_argument('--index', default=None)
    ap.add_argument('--only-core', action='store_true')
    ap.add_argument('--citekey', default='')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--max-figures', type=int, default=C.FIGURE_MAX)
    ap.add_argument('--full', action='store_true')
    args = ap.parse_args(argv)
    return process_collection(vars(args))
