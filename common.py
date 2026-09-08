import os, json, sqlite3, hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / '.env')
CFG = json.loads((ROOT / 'config.json').read_text(encoding='utf-8'))
DB = ROOT / 'market_ai.db'

TWELVE_KEY = os.getenv('TWELVEDATA_API_KEY', '').strip()
TG_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
TG_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID', '').strip()
ENABLE_FINBERT = os.getenv('ENABLE_FINBERT', '0').strip() == '1'

HTTP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; GlobalMarketAI/1.1)',
    'Accept': 'application/json,text/plain,*/*',
}

class MarketDataError(RuntimeError):
    pass


def db():
    c = sqlite3.connect(DB)
    c.execute('''CREATE TABLE IF NOT EXISTS alerts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, event_id TEXT, title TEXT, source TEXT, url TEXT,
        category TEXT, score INTEGER, gold TEXT, usd TEXT, stocks TEXT, oil TEXT, message TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS seen(event_id TEXT PRIMARY KEY, ts TEXT)''')
    c.commit()
    return c


def _safe_http_error(service, status):
    if status == 429:
        return MarketDataError(f'{service}: تم تجاوز حد الطلبات مؤقتًا. انتظر قليلًا ثم حدّث الصفحة.')
    if status in (401, 403):
        return MarketDataError(f'{service}: تعذر الوصول للبيانات. تحقق من المفتاح أو الخطة.')
    return MarketDataError(f'{service}: تعذر جلب البيانات حاليًا (HTTP {status}).')


def td(endpoint, params):
    if not TWELVE_KEY:
        raise MarketDataError('مفتاح Twelve Data غير موجود في Secrets.')
    p = dict(params)
    p['apikey'] = TWELVE_KEY
    try:
        r = requests.get('https://api.twelvedata.com/' + endpoint, params=p, timeout=20, headers=HTTP_HEADERS)
    except requests.RequestException:
        raise MarketDataError('Twelve Data: تعذر الاتصال بالخدمة حاليًا.')
    if r.status_code >= 400:
        raise _safe_http_error('Twelve Data', r.status_code)
    try:
        d = r.json()
    except ValueError:
        raise MarketDataError('Twelve Data: استجابة غير صالحة من الخدمة.')
    if isinstance(d, dict) and d.get('status') == 'error':
        code = d.get('code')
        if code == 429:
            raise MarketDataError('Twelve Data: تم تجاوز حد الطلبات مؤقتًا.')
        raise MarketDataError('Twelve Data: تعذر جلب هذا الرمز ضمن الخطة الحالية.')
    return d


def quote(symbol):
    return td('quote', {'symbol': symbol})


def pct(q):
    try:
        return float(q.get('percent_change'))
    except Exception:
        try:
            p = float(q['close'])
            prev = float(q['previous_close'])
            return (p - prev) / prev * 100
        except Exception:
            return None


def yahoo_chart(symbol, range_='1mo', interval='1d'):
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
    params = {'range': range_, 'interval': interval, 'includePrePost': 'false', 'events': 'div,splits'}
    try:
        r = requests.get(url, params=params, timeout=20, headers=HTTP_HEADERS)
    except requests.RequestException:
        raise MarketDataError('بيانات السوق السعودي: تعذر الاتصال بمصدر البيانات.')
    if r.status_code >= 400:
        raise _safe_http_error('بيانات السوق السعودي', r.status_code)
    try:
        payload = r.json()
        result = payload.get('chart', {}).get('result') or []
        if not result:
            raise MarketDataError('بيانات السوق السعودي: لا توجد بيانات لهذا الرمز.')
        return result[0]
    except MarketDataError:
        raise
    except Exception:
        raise MarketDataError('بيانات السوق السعودي: استجابة غير صالحة.')


def yahoo_quote(symbol):
    data = yahoo_chart(symbol, range_='5d', interval='1d')
    meta = data.get('meta', {})
    closes = ((data.get('indicators') or {}).get('quote') or [{}])[0].get('close') or []
    closes = [x for x in closes if x is not None]
    price = meta.get('regularMarketPrice')
    prev = meta.get('chartPreviousClose') or meta.get('previousClose')
    if price is None and closes:
        price = closes[-1]
    if prev is None and len(closes) >= 2:
        prev = closes[-2]
    change = None
    if price is not None and prev not in (None, 0):
        change = (float(price) - float(prev)) / float(prev) * 100
    return {'close': price, 'previous_close': prev, 'percent_change': change, 'currency': meta.get('currency', 'SAR')}


def yahoo_history(symbol, range_='6mo', interval='1d'):
    return yahoo_chart(symbol, range_=range_, interval=interval)


def gdelt_news():
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=int(CFG.get('news_hours', 4)))
    params = {
        'query': CFG['news_query'], 'mode': 'ArtList', 'format': 'json',
        'maxrecords': int(CFG.get('news_maxrecords', 20)), 'sort': 'datedesc',
        'startdatetime': start.strftime('%Y%m%d%H%M%S'),
        'enddatetime': now.strftime('%Y%m%d%H%M%S')
    }
    try:
        r = requests.get('https://api.gdeltproject.org/api/v2/doc/doc', params=params, timeout=25, headers=HTTP_HEADERS)
    except requests.RequestException:
        raise MarketDataError('GDELT: تعذر الاتصال بخدمة الأخبار حاليًا.')
    if r.status_code >= 400:
        raise _safe_http_error('GDELT', r.status_code)
    try:
        return r.json().get('articles', []) or []
    except ValueError:
        raise MarketDataError('GDELT: استجابة الأخبار غير صالحة.')


def telegram(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        return False
    try:
        r = requests.post(
            f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',
            json={'chat_id': TG_CHAT_ID, 'text': text, 'disable_web_page_preview': True},
            timeout=20,
            headers=HTTP_HEADERS,
        )
    except requests.RequestException:
        return False
    return r.ok

RULES = [
  (['fed','federal reserve','interest rate','rate hike','hawkish','inflation','cpi'], 'Fed/Inflation', 82, '↑','↑','↓','↔'),
  (['rate cut','dovish','lower rates'], 'Rates easing', 78, '↑','↓','↑','↔'),
  (['war','missile','attack','sanctions','geopolitical','conflict'], 'Geopolitical risk', 86, '↑','↑','↓','↑'),
  (['ceasefire','peace deal','de-escalation'], 'Risk easing', 72, '↓','↔','↑','↓'),
  (['opec','oil cut','production cut','supply disruption'], 'Oil supply', 80, '↑','↔','↓','↑'),
  (['jobs','payroll','unemployment','labor market'], 'US labour', 70, '↔','↑','↓','↔'),
  (['bank crisis','default','liquidity crisis','recession'], 'Financial stress', 88, '↑','↑','↓','↓'),
]

_finbert = None
def finbert_sentiment(text):
    global _finbert
    if not ENABLE_FINBERT:
        return None
    try:
        if _finbert is None:
            from transformers import pipeline
            _finbert = pipeline('text-classification', model='ProsusAI/finbert')
        out = _finbert(text[:500], truncation=True)[0]
        return {'label': out['label'].lower(), 'score': float(out['score'])}
    except Exception:
        return None


def analyze(title):
    t = (title or '').lower()
    best = ('General market', 45, '↔','↔','↔','↔')
    for keys, cat, score, gold, usd, stocks, oil in RULES:
        hits = sum(1 for k in keys if k in t)
        if hits:
            candidate = (cat, min(99, score + (hits - 1) * 4), gold, usd, stocks, oil)
            if candidate[1] > best[1]:
                best = candidate
    cat, score, gold, usd, stocks, oil = best
    sent = finbert_sentiment(title or '')
    if sent:
        score = min(99, max(score, int(50 + sent['score'] * 35)))
    return {'category': cat, 'score': int(score), 'gold': gold, 'usd': usd, 'stocks': stocks, 'oil': oil, 'sentiment': sent}


def eid(article):
    raw = (article.get('url') or '') + '|' + (article.get('title') or '')
    return hashlib.sha1(raw.encode('utf-8', errors='ignore')).hexdigest()
