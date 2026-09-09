"""Public RSS headlines with per-source backoff and a persistent last-good cache."""
import copy
import json
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import requests

SOURCES = [
    {'id': 'alarabiya', 'name': 'العربية', 'domain': 'alarabiya.net',
     'feed': 'https://www.alarabiya.net/tools/mrss/?cat=main',
     'query': 'site:www.alarabiya.net -site:www.alarabiya.net/aswaq when:1d'},
    {'id': 'business', 'name': 'العربية بزنس', 'domain': 'alarabiya.net',
     'feed': 'https://www.alarabiya.net/tools/mrss/?cat=aswaq',
     'query': 'site:alarabiya.net/aswaq when:1d'},
    {'id': 'asharq', 'name': 'الشرق للأخبار', 'domain': 'asharq.com',
     'feed': 'https://asharq.com/snapchat/rss.xml', 'query': 'site:asharq.com when:1d'},
    {'id': 'bloomberg', 'name': 'الشرق Bloomberg', 'domain': 'asharqbusiness.com',
     'feed': 'https://asharqbusiness.com/rss.xml', 'query': 'site:asharqbusiness.com when:1d'},
    {'id': 'cnn', 'name': 'CNN بالعربية', 'domain': 'arabic.cnn.com',
     'feed': 'https://arabic.cnn.com/rss', 'query': 'site:arabic.cnn.com when:1d'},
]


def domain_matches(url, domain):
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower()
    return parsed.scheme in ('http', 'https') and (host == domain or host.endswith('.' + domain))


def parse_feed(content, source, via, now):
    if b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
        raise ValueError('Unsupported XML declarations')
    root = ET.fromstring(content)
    if root.tag != 'rss':
        raise ValueError('Not an RSS feed')
    articles = {}
    for item in root.findall('./channel/item'):
        title = unescape(item.findtext('title') or '').strip()
        url = (item.findtext('link') or '').strip()
        try:
            date = parsedate_to_datetime(item.findtext('pubDate') or '')
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            published = date.timestamp()
        except (ValueError, TypeError, OverflowError):
            continue
        # Google searches can include old results even with a when:1d query.
        if not title or not now - 48 * 3600 <= published <= now + 600:
            continue
        if via == 'google':
            publisher = item.find('source')
            if publisher is None or not domain_matches(publisher.get('url', ''), source['domain']):
                continue
            if not domain_matches(url, 'news.google.com'):
                continue
        elif not domain_matches(url, source['domain']):
            continue
        articles[url] = {'title': title[:500], 'url': url, 'published': published,
                         'source': source['id'], 'name': source['name'], 'via': via}
    return sorted(articles.values(), key=lambda x: x['published'], reverse=True)[:100]


class FeedError(Exception):
    def __init__(self, status=0, retry=60):
        self.status, self.retry = status, retry


def download(url):
    try:
        with requests.get(url, timeout=(4, 8), stream=True,
                          headers={'User-Agent': 'GlobalMarketAI/1.0 (RSS reader)'}) as response:
            if response.status_code >= 400:
                try:
                    retry = float(response.headers.get('Retry-After', 300))
                except ValueError:
                    try:
                        retry = parsedate_to_datetime(response.headers['Retry-After']).timestamp() - time.time()
                    except (KeyError, ValueError, TypeError):
                        retry = 300
                raise FeedError(response.status_code, max(60, min(3600, retry)))
            chunks, size = [], 0
            for part in response.iter_content(65536):
                size += len(part)
                if size > 4 * 1024 * 1024:
                    raise FeedError()
                chunks.append(part)
            return b''.join(chunks)
    except requests.RequestException:
        raise FeedError() from None


class NewsStore:
    def __init__(self, path=None, fetch=download, clock=time.time):
        self.path = Path(path) if path else Path(__file__).with_name('news_cache.json')
        self.fetch, self.clock = fetch, clock
        self.lock = threading.Lock()
        self.states = {}
        self.persistence_error = False
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                self.states = {s['id']: data[s['id']] for s in SOURCES
                               if isinstance(data.get(s['id']), dict)}
        except (OSError, ValueError):
            pass

    def _source(self, source, state, now):
        if now < state.get('next_check', 0):
            return state
        state = copy.deepcopy(state)
        state['checked'] = now
        articles = None
        if now >= state.get('direct_retry', 0):
            try:
                articles = parse_feed(self.fetch(source['feed']), source, 'direct', now)
                if not articles:
                    raise FeedError()
                state['via'] = 'direct'
                state['direct_retry'] = 0
            except (FeedError, ValueError, ET.ParseError) as error:
                state['direct_retry'] = now + max(900, getattr(error, 'retry', 60))
        if articles is None:
            if now < state.get('google_retry', 0):
                state['next_check'] = min(state.get('direct_retry', now + 60), state['google_retry'])
                return state
            url = 'https://news.google.com/rss/search?' + urlencode(
                {'q': source['query'], 'hl': 'ar', 'gl': 'SA', 'ceid': 'SA:ar'})
            try:
                articles = parse_feed(self.fetch(url), source, 'google', now)
                if not articles:
                    raise FeedError()
                state['via'] = 'google'
            except (FeedError, ValueError, ET.ParseError) as error:
                failures = min(state.get('failures', 0) + 1, 5)
                wait = max(min(60 * 2 ** (failures - 1), 900), getattr(error, 'retry', 60))
                state.update(failures=failures, error=True, next_check=now + wait, google_retry=now + wait)
                return state
        merged = {x['url']: x for x in state.get('articles', [])}
        merged.update({x['url']: x for x in articles})
        recent = [x for x in merged.values() if now - 48 * 3600 <= x['published'] <= now + 600]
        state.update(articles=sorted(recent, key=lambda x: x['published'], reverse=True)[:100],
                     success=now, error=False, failures=0, next_check=now + 60, google_retry=0)
        return state

    def get(self):
        # One refresh per process; other sessions see the last completed snapshot.
        if not self.lock.acquire(blocking=False):
            return self.snapshot()
        try:
            now = self.clock()
            with ThreadPoolExecutor(max_workers=5) as pool:
                futures = {s['id']: pool.submit(self._source, s, self.states.get(s['id'], {}), now) for s in SOURCES}
                updated = {key: future.result() for key, future in futures.items()}
            self.states = updated
            try:
                temporary = self.path.with_suffix('.tmp')
                temporary.write_text(json.dumps(updated, ensure_ascii=False), encoding='utf-8')
                temporary.replace(self.path)
                self.persistence_error = False
            except OSError:
                self.persistence_error = True
            return self.snapshot()
        finally:
            self.lock.release()

    def snapshot(self):
        now = self.clock()
        states = copy.deepcopy(self.states)
        articles = []
        for source in SOURCES:
            state = states.setdefault(source['id'], {})
            for item in state.get('articles', []):
                if now - 48 * 3600 <= item['published'] <= now + 600:
                    articles.append(dict(item, stale=bool(state.get('error'))))
        return {'articles': sorted(articles, key=lambda x: x['published'], reverse=True),
                'states': states, 'persistence_error': self.persistence_error}
