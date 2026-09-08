import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from common import *

st.set_page_config(page_title='Global Market AI Pro', page_icon='🌍', layout='wide')
st.markdown('''<style>
.block-container{padding-top:1rem}
div[data-testid="stMetric"]{background:rgba(128,128,128,.08);border:1px solid rgba(128,128,128,.18);padding:12px;border-radius:14px}
.smallnote{opacity:.75;font-size:.86rem}
</style>''', unsafe_allow_html=True)

st.title('🌍 Global Market AI Pro')
st.caption('متابعة الأسواق العالمية والسوق السعودي + تحليل الأحداث وتأثيرها المتوقع')

@st.cache_data(ttl=300, show_spinner=False)
def cached_global_quote(symbol):
    return quote(symbol)

@st.cache_data(ttl=900, show_spinner=False)
def cached_saudi_quote(symbol):
    return yahoo_quote(symbol)

@st.cache_data(ttl=900, show_spinner=False)
def cached_news():
    return gdelt_news()

@st.cache_data(ttl=600, show_spinner=False)
def global_history(symbol, interval):
    d = td('time_series', {'symbol': symbol, 'interval': interval, 'outputsize': 120, 'order': 'ASC'})
    df = pd.DataFrame(d.get('values', []))
    if df.empty:
        return df
    for c in ['open','high','low','close']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['datetime'] = pd.to_datetime(df['datetime'])
    return df

@st.cache_data(ttl=1800, show_spinner=False)
def saudi_history(symbol):
    d = yahoo_history(symbol, range_='6mo', interval='1d')
    ts = d.get('timestamp') or []
    q = ((d.get('indicators') or {}).get('quote') or [{}])[0]
    if not ts:
        return pd.DataFrame()
    df = pd.DataFrame({
        'datetime': pd.to_datetime(ts, unit='s', utc=True).tz_convert('Asia/Riyadh').tz_localize(None),
        'open': q.get('open', []), 'high': q.get('high', []), 'low': q.get('low', []), 'close': q.get('close', [])
    })
    for c in ['open','high','low','close']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    return df.dropna(subset=['close'])

with st.sidebar:
    st.header('التحكم')
refresh = st.select_slider('تحديث الواجهة', [300, 600, 900, 1800], value=300, format_func=lambda x: f'{x//60} دقائق') 
global_symbols = [x['symbol'] for x in CFG['watchlist']]
    chosen_global = st.multiselect('الأسواق العالمية', global_symbols, default=global_symbols[:4])
    saudi_symbols = [x['symbol'] for x in CFG.get('saudi_watchlist', [])]
    chosen_saudi = st.multiselect('🇸🇦 السوق السعودي', saudi_symbols, default=saudi_symbols[:4])
    st.metric('حد تنبيه الحدث', f"{CFG.get('impact_alert_score',65)}/100")
    if st.button('تحديث الآن', use_container_width=True):
        st.cache_data.clear()
        st.rerun()

if not TWELVE_KEY:
    st.error('أضف TWELVEDATA_API_KEY في Secrets داخل Streamlit.')
    st.stop()

st.subheader('🌐 الأسواق العالمية')
global_quotes = []
for item in CFG['watchlist']:
    if item['symbol'] not in chosen_global:
        continue
    try:
        global_quotes.append((item, cached_global_quote(item['symbol'])))
    except Exception as e:
        st.warning(f"{item['label']}: {str(e)}")

if global_quotes:
    cols = st.columns(min(4, len(global_quotes)))
    for i, (item, q) in enumerate(global_quotes):
        p = pct(q)
        price = q.get('close', '—')
        try:
            fv = float(price)
            price = f'{fv:,.2f}' if fv >= 100 else f'{fv:,.5f}'
        except Exception:
            pass
        cols[i % len(cols)].metric(item['label'], price, None if p is None else f'{p:+.2f}%')

st.divider()
st.subheader('🇸🇦 السوق السعودي')
st.caption('الأسعار بالريال السعودي وقد تكون متأخرة بحسب مصدر البيانات.')
saudi_quotes = []
for item in CFG.get('saudi_watchlist', []):
    if item['symbol'] not in chosen_saudi:
        continue
    try:
        saudi_quotes.append((item, cached_saudi_quote(item['symbol'])))
    except Exception as e:
        st.warning(f"{item['label']}: {str(e)}")

if saudi_quotes:
    cols = st.columns(min(4, len(saudi_quotes)))
    for i, (item, q) in enumerate(saudi_quotes):
        p = pct(q)
        price = q.get('close', '—')
        try:
            price = f'{float(price):,.2f}'
        except Exception:
            pass
        suffix = '' if item['symbol'].startswith('^') else ' ر.س'
        cols[i % len(cols)].metric(item['label'], f'{price}{suffix}', None if p is None else f'{p:+.2f}%')

st.divider()
a, b = st.columns([1.2, 1])

with a:
    st.subheader('🧠 آخر التنبيهات والتحليل')
    conn = db()
    df = pd.read_sql_query('SELECT ts,title,source,category,score,gold,usd,stocks,oil,url FROM alerts ORDER BY id DESC LIMIT 30', conn)
    conn.close()
    if df.empty:
        st.info('لم تُسجل تنبيهات بعد. التنبيهات التلقائية تحتاج تشغيل watcher مستقل.')
    else:
        for _, r in df.head(12).iterrows():
            st.markdown(f"**{r['score']}/100 · {r['category']}** — {r['title']}")
            st.caption(f"ذهب {r['gold']} | دولار {r['usd']} | أسهم {r['stocks']} | نفط {r['oil']} · {r['source']}")
            if r['url']:
                st.markdown(f"[فتح المصدر]({r['url']})")

with b:
    st.subheader('📰 تحليل أخبار مباشر')
    try:
        news = cached_news()[:10]
        if not news:
            st.info('لا توجد أخبار جديدة ضمن النافذة الحالية.')
        for art in news:
            x = analyze(art.get('title', ''))
            st.markdown(f"**{x['score']}/100 · {x['category']}**")
            if art.get('url'):
                st.markdown(f"[{art.get('title','خبر')}]({art['url']})")
            else:
                st.write(art.get('title', 'خبر'))
            st.caption(f"Gold {x['gold']} · USD {x['usd']} · Stocks {x['stocks']} · Oil {x['oil']}")
    except Exception as e:
        st.warning(str(e))

st.divider()
st.subheader('📈 رسم السوق')
market_type = st.radio('السوق', ['عالمي', 'سعودي'], horizontal=True)

try:
    if market_type == 'عالمي':
        options = chosen_global if chosen_global else ['XAU/USD']
        chart_symbol = st.selectbox('الأصل', options, key='global_chart')
        interval = st.selectbox('الفاصل', ['5min','15min','1h','1day'], index=1)
        h = global_history(chart_symbol, interval)
    else:
        options = chosen_saudi if chosen_saudi else ['^TASI.SR']
        chart_symbol = st.selectbox('الأصل السعودي', options, key='saudi_chart')
        st.caption('الرسم السعودي يومي لتقليل الطلبات وتحسين الاستقرار.')
        h = saudi_history(chart_symbol)

    if not h.empty:
        fig = go.Figure(data=[go.Candlestick(x=h['datetime'], open=h['open'], high=h['high'], low=h['low'], close=h['close'])])
        fig.update_layout(height=480, xaxis_rangeslider_visible=False, margin=dict(l=10,r=10,t=20,b=10))
        st.plotly_chart(fig, use_container_width=True)
except Exception as e:
    st.warning(str(e))

st.caption('التحليل آلي ومعلوماتي، وليس توصية استثمارية أو ضمانًا للحركة المستقبلية.')
st.markdown(f'<script>setTimeout(function(){{window.parent.location.reload();}}, {int(refresh)*1000});</script>', unsafe_allow_html=True)
