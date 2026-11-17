"""kbapp.integrations.openalex：由职责边界组织的知识库能力。"""
from __future__ import annotations
import ipaddress
import json
import socket
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen, HTTPRedirectHandler
from kbapp.core.io import now

def abstract(inv):
    if not inv:
        return ''
    pairs = sorted(((pos, word) for word, positions in inv.items() for pos in positions))
    return ' '.join((word for _, word in pairs))

def map_work(raw):
    wid = raw['id'].rstrip('/').rsplit('/', 1)[-1]
    location = raw.get('best_oa_location') or {}
    return {'id': 'oa:' + wid, 'openalex_id': wid, 'kind': 'external', 'title': raw.get('title') or '', 'year': raw.get('publication_year'), 'doi': (raw.get('doi') or '').replace('https://doi.org/', ''), 'doi_url': raw.get('doi') or '', 'authors': [a.get('author', {}).get('display_name', '') for a in raw.get('authorships', [])], 'abstract': abstract(raw.get('abstract_inverted_index')), 'abstract_scope': 'publisher_metadata', 'refs': [x.rsplit('/', 1)[-1] for x in raw.get('referenced_works', [])], 'oa_url': location.get('pdf_url') or '', 'oa_landing_url': location.get('landing_page_url') or '', 'cited_by': raw.get('cited_by_count'), 'citation_count': raw.get('cited_by_count'), 'citation_source': 'OpenAlex', 'citation_updated': now(), 'metadata_updated': now()}

class OpenAlex:

    def __init__(self, store, allow_anonymous=False):
        self.store = store
        self.cfg = store.settings()
        self.calls = 0
        self.remaining = None
        self.allow_anonymous = allow_anonymous
        previous = store.state('openalex_usage', {})
        if str(previous.get('updated', ''))[:10] == now()[:10]:
            self.remaining = previous.get('remaining')
        self.used = 0.0

    def get(self, path, params=None):
        if not self.cfg['openalex_key'] and (not self.allow_anonymous):
            raise ValueError('请在网页配置 OpenAlex key 后继续分页补全')
        if self.calls >= self.cfg['external_requests_per_run']:
            raise ValueError('本轮外部请求上限已到，保留分页位置，下次续跑')
        if self.remaining is not None and self.remaining <= 0:
            raise ValueError('OpenAlex 返回的当日额度已用尽')
        url = 'https://api.openalex.org/' + path.lstrip('/')
        if params:
            url += '?' + urlencode(params)
        headers = {'User-Agent': 'LiteratureWorkbench/1.0'}
        if self.cfg['openalex_key']:
            headers['Authorization'] = 'Bearer ' + self.cfg['openalex_key']
        req = Request(url, headers=headers)
        self.calls += 1
        try:
            with urlopen(req, timeout=30) as resp:
                remaining = resp.headers.get('X-RateLimit-Remaining')
                if remaining is not None:
                    self.remaining = float(remaining)
                data = json.load(resp)
        except HTTPError as exc:
            if exc.code == 429:
                raise ValueError('OpenAlex 限流或当日额度不足，已保留采集位置') from None
            raise ValueError(f'OpenAlex HTTP {exc.code}') from None
        self.used += float(data.get('meta', {}).get('cost_usd') or 0)
        self.store.set_state('openalex_usage', {'requests': self.calls, 'reported_cost_usd': self.used, 'remaining': self.remaining, 'updated': now()})
        time.sleep(0.12)
        return data

def public_url(url):
    u = urlparse(url)
    if u.scheme not in {'https', 'http'} or not u.hostname or u.username:
        raise ValueError('全文地址无效')
    for family, _, _, _, addr in socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == 'https' else 80)):
        if not ipaddress.ip_address(addr[0]).is_global:
            raise ValueError('全文下载地址必须为公开来源')
    return url

class SafeRedirect(HTTPRedirectHandler):

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)
