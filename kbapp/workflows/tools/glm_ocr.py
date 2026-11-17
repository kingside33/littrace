"""kbapp.workflows.tools.glm_ocr：由职责边界组织的知识库能力。"""
import argparse
import json
import sys
from kbapp.integrations.ocr import GLMOCR, GLMOCRError

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', action='store_true')
    ap.add_argument('--image', default='')
    ap.add_argument('--mode', default='formula', choices=['formula', 'page', 'table'])
    ap.add_argument('--url', default=None)
    ap.add_argument('--model', default=None)
    ap.add_argument('--api', default=None, choices=['openai', 'ollama'])
    args = ap.parse_args(argv)
    client = GLMOCR(args.url, args.model, args.api)
    if args.probe or not args.image:
        print(json.dumps(client.probe(), ensure_ascii=False, indent=2))
        sys.exit(0 if client.available() else 1)
    with open(args.image, 'rb') as fh:
        png = fh.read()
    try:
        text = client.image_to_text(png, args.mode)
    except GLMOCRError as ex:
        sys.exit(f'[FAIL] {ex}')
    print(text)
