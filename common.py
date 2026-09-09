
import os, json, sqlite3, hashlib, math, re
import time
from urllib.parse import quote as urlquote
from pathlib import Path
from datetime import datetime, timezone, timedelta
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")
CFG = json.loads((ROOT/"config.json").read_text(encoding="utf-8"))
DB = ROOT/"market_ai.db"

TWELVE_KEY = os.getenv("TWELVEDATA_API_KEY","").strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN","").strip()
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID","").strip()
ENABLE_FINBERT = os.getenv("ENABLE_FINBERT","0").strip() == "1"

def db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS alerts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT, event_id TEXT, title TEXT, source TEXT, url TEXT,
        category TEXT, score INTEGER, gold TEXT, usd TEXT, stocks TEXT, oil TEXT, message TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS seen(event_id TEXT PRIMARY KEY, ts TEXT)""")
    c.commit()
    return c

def td(endpoint, params):
    if not TWELVE_KEY:
        raise RuntimeError("TWELVEDATA_API_KEY غير موجود في ملف .env")
    p = dict(params); p["apikey"] = TWELVE_KEY
    d = request_json("https://api.twelvedata.com/" + endpoint, params=p, source="Twelve Data")
    if not isinstance(d, dict) or d.get("status") == "error":
        raise RuntimeError("Twelve Data: تعذر جلب البيانات؛ تحقق من الرمز والخطة وحد الطلبات.")
    return d

def quote(symbol):
    return td("quote", {"symbol":symbol})

def refresh_seconds(value):
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        return 300
    return value if value in (300, 600, 900, 1800) else 300


_retry_after = {}


def request_json(url, *, params=None, source="المصدر"):
    if time.monotonic() < _retry_after.get(source, 0):
        raise RuntimeError(f"{source}: الطلبات متوقفة مؤقتًا؛ حاول لاحقًا.")
    try:
        r = requests.get(url, params=params, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 429:
            _retry_after[source] = time.monotonic() + 300
            raise RuntimeError(f"{source}: تم تجاوز حد الطلبات؛ انتظر موعد التحديث التالي.")
        if r.status_code >= 400:
            raise RuntimeError(f"{source}: تعذر الاتصال (HTTP {r.status_code}).")
        return r.json()
    except (requests.RequestException, ValueError):
        _retry_after[source] = time.monotonic() + 60
        raise RuntimeError(f"{source}: تعذر الاتصال أو استلام بيانات صالحة.") from None


def yahoo_history(symbol, range_="6mo", interval="1d"):
    data = request_json("https://query1.finance.yahoo.com/v8/finance/chart/" + urlquote(symbol, safe=""),
                        params={"range": range_, "interval": interval}, source="Yahoo Finance")
    chart = data.get("chart") or {}
    if chart.get("error") or not chart.get("result"):
        raise RuntimeError("Yahoo Finance: لا تتوفر بيانات لهذا الرمز.")
    return chart["result"][0]


def yahoo_quote(symbol):
    data = yahoo_history(symbol, range_="5d")
    meta = data.get("meta") or {}
    price = meta.get("regularMarketPrice")
    previous = meta.get("previousClose", meta.get("chartPreviousClose"))
    # chartPreviousClose may precede the requested range; use the preceding daily bar.
    bars = ((data.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    closes = [x for x in bars if x is not None]
    if len(closes) >= 2:
        previous = closes[-2]
    if price is None:
        price = closes[-1] if closes else None
    if price is None:
        raise RuntimeError("Yahoo Finance: السعر غير متاح حاليًا.")
    return {"close": price, "previous_close": previous}


def pct(q):
    try:
        value = float(q.get("percent_change"))
        if math.isfinite(value):
            return value
    except (TypeError, ValueError):
        pass
    try:
        price, previous = float(q["close"]), float(q["previous_close"])
        value = (price - previous) / previous * 100
        return value if math.isfinite(value) else None
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None

def gdelt_news():
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=int(CFG.get("news_hours",4)))
    params = {
      "query":CFG["news_query"], "mode":"ArtList", "format":"json",
      "maxrecords":50, "sort":"datedesc",
      "startdatetime":start.strftime("%Y%m%d%H%M%S"),
      "enddatetime":now.strftime("%Y%m%d%H%M%S")
    }
    data = request_json("https://api.gdeltproject.org/api/v2/doc/doc", params=params, source="GDELT")
    return data.get("articles", []) or []

def telegram(text):
    if not TG_TOKEN or not TG_CHAT_ID: return False
    try:
        r=requests.post(
          f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
          json={"chat_id":TG_CHAT_ID,"text":text,"disable_web_page_preview":True},
          timeout=20
        )
        r.raise_for_status()
        return bool(r.json().get("ok"))
    except (requests.RequestException, ValueError):
        raise RuntimeError("Telegram: تعذر إرسال التنبيه.") from None

RULES = [
  (["fed","federal reserve","interest rate","rate hike","hawkish","inflation","cpi"], "Fed/Inflation", 82, "↑","↑","↓","↔"),
  (["rate cut","dovish","lower rates"], "Rates easing", 78, "↑","↓","↑","↔"),
  (["war","missile","attack","sanctions","geopolitical","conflict"], "Geopolitical risk", 86, "↑","↑","↓","↑"),
  (["ceasefire","peace deal","de-escalation"], "Risk easing", 72, "↓","↔","↑","↓"),
  (["opec","oil cut","production cut","supply disruption"], "Oil supply", 80, "↑","↔","↓","↑"),
  (["jobs","payroll","unemployment","labor market"], "US labour", 70, "↔","↑","↓","↔"),
  (["bank crisis","default","liquidity crisis","recession"], "Financial stress", 88, "↑","↑","↓","↓"),
]

_finbert = None
def finbert_sentiment(text):
    global _finbert
    if not ENABLE_FINBERT:
        return None
    try:
        if _finbert is None:
            from transformers import pipeline
            _finbert = pipeline("text-classification", model="ProsusAI/finbert")
        out = _finbert(text[:500], truncation=True)[0]
        return {"label":out["label"].lower(), "score":float(out["score"])}
    except Exception:
        return None

def analyze(title):
    t = (title or "").lower()
    best = ("General market", 45, "↔","↔","↔","↔")
    for keys, cat, score, gold, usd, stocks, oil in RULES:
        hits=sum(1 for k in keys if re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", t))
        if cat == "Fed/Inflation" and any(k in t for k in ["rate cut", "dovish", "lower rates"]):
            continue
        if hits:
            candidate=(cat, min(99, score + (hits-1)*4), gold, usd, stocks, oil)
            if candidate[1] > best[1]: best=candidate

    cat, score, gold, usd, stocks, oil = best
    sent = finbert_sentiment(title or "")
    if sent:
        # Sentiment refines confidence, but event-direction rules remain asset-specific.
        score = min(99, max(score, int(50 + sent["score"]*35)))
    return {
      "category":cat, "score":int(score),
      "gold":gold, "usd":usd, "stocks":stocks, "oil":oil,
      "sentiment":sent
    }

def eid(article):
    raw=(article.get("url") or "")+"|"+(article.get("title") or "")
    return hashlib.sha1(raw.encode("utf-8",errors="ignore")).hexdigest()
