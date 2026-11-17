"""在线与离线使用同一份原生 JS 模块清单，无额外构建步骤。"""
import base64
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1] / 'web/assets'
_cache = {}

def _file_key(name):
    path = ROOT / name
    stat = path.stat()
    return (name, stat.st_mtime_ns, stat.st_size)

def manifest():
    key = _file_key('manifest.json')
    if _cache.get('manifest_key') != key:
        _cache['manifest_key'] = key
        _cache['manifest'] = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    return _cache['manifest']

def javascript_version():
    return tuple(_file_key(name) for name in manifest()['scripts'])

def javascript():
    names = manifest()['scripts']
    key = tuple(_file_key(name) for name in names)
    if _cache.get('js_key') != key:
        _cache['js_key'] = key
        _cache['js'] = '\n'.join(((ROOT / name).read_text(encoding='utf-8') for name in names))
    return _cache['js']

def render_html(static_graph=None):
    html = (ROOT / 'index.html').read_text(encoding='utf-8')
    if static_graph is None:
        return html
    assets = manifest()
    for name in assets['styles']:
        html = html.replace(f'<link rel="stylesheet" href="/{name}">', '<style>' + (ROOT / name).read_text(encoding='utf-8') + '</style>')
    icons = {p.stem: 'data:image/svg+xml;base64,' + base64.b64encode(p.read_bytes()).decode('ascii') for p in (ROOT / 'icons').glob('*.svg')}
    data = json.dumps(static_graph, ensure_ascii=False).replace('<', '\\u003c')
    script = 'window.STATIC_GRAPH=' + data + ';window.KB_ICONS=' + json.dumps(icons) + ';\n' + javascript()
    script = script.replace('</script', '<\\/script')
    return html.replace('<script src="/app.js" defer></script>', '<script>' + script + '</script>')
