
import sqlite3
from datetime import datetime
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from common import *

st.set_page_config(page_title="Global Market AI Pro", page_icon="🌍", layout="wide")
st.markdown("""<style>
.block-container{padding-top:1rem}
div[data-testid="stMetric"]{background:rgba(128,128,128,.08);border:1px solid rgba(128,128,128,.18);padding:12px;border-radius:14px}
.badge{padding:.2rem .55rem;border:1px solid #777;border-radius:99px;font-size:.8rem}
</style>""", unsafe_allow_html=True)

st.title("🌍 Global Market AI Pro")
st.caption("متابعة الأسواق + تحليل الأحداث + تأثير متوقع على الذهب والدولار والأسهم والنفط")

with st.sidebar:
    st.header("التحكم")
    refresh=st.select_slider("تحديث الواجهة", [30,60,120,300], value=int(CFG.get("refresh_seconds",60)))
    symbols=[x["symbol"] for x in CFG["watchlist"]]
    chosen=st.multiselect("الأصول", symbols, default=symbols[:5])
    st.metric("حد تنبيه الحدث", f"{CFG.get('impact_alert_score',65)}/100")
    st.caption("فعّل FinBERT من .env بوضع ENABLE_FINBERT=1 بعد تثبيت requirements-ai.txt")
    if st.button("تحديث الآن", use_container_width=True):
        st.cache_data.clear(); st.rerun()

if not TWELVE_KEY:
    st.error("ضع TWELVEDATA_API_KEY في ملف .env ثم أعد التشغيل.")
    st.stop()

quotes=[]
for item in CFG["watchlist"]:
    if item["symbol"] not in chosen: continue
    try: quotes.append((item,quote(item["symbol"])))
    except Exception as e: st.warning(f"{item['symbol']}: {e}")

if quotes:
    cols=st.columns(min(5,len(quotes)))
    for i,(item,q) in enumerate(quotes):
        p=pct(q)
        price=q.get("close","—")
        try:
            fv=float(price); price=f"{fv:,.2f}" if fv>=100 else f"{fv:,.5f}"
        except: pass
        cols[i%len(cols)].metric(item["label"],price,None if p is None else f"{p:+.2f}%")

st.divider()
a,b=st.columns([1.2,1])

with a:
    st.subheader("🧠 آخر التنبيهات والتحليل")
    conn=db()
    df=pd.read_sql_query("SELECT ts,title,source,category,score,gold,usd,stocks,oil,url FROM alerts ORDER BY id DESC LIMIT 30",conn)
    conn.close()
    if df.empty:
        st.info("لم تُسجل تنبيهات بعد. شغّل watcher.py أو ملف التشغيل الكامل.")
    else:
        for _,r in df.head(12).iterrows():
            st.markdown(f"**{r['score']}/100 · {r['category']}** — {r['title']}")
            st.caption(f"ذهب {r['gold']} | دولار {r['usd']} | أسهم {r['stocks']} | نفط {r['oil']} · {r['source']}")
            if r["url"]: st.markdown(f"[فتح المصدر]({r['url']})")

with b:
    st.subheader("📰 تحليل أخبار مباشر")
    try:
        news=gdelt_news()[:10]
        for art in news:
            x=analyze(art.get("title",""))
            st.markdown(f"**{x['score']}/100 · {x['category']}**")
            if art.get("url"): st.markdown(f"[{art.get('title','خبر')}]({art['url']})")
            else: st.write(art.get("title","خبر"))
            st.caption(f"Gold {x['gold']} · USD {x['usd']} · Stocks {x['stocks']} · Oil {x['oil']}")
    except Exception as e:
        st.error(f"تعذر تحميل الأخبار: {e}")

st.divider()
st.subheader("📈 رسم السوق")
chart_symbol=st.selectbox("الأصل", chosen if chosen else ["XAU/USD"])
interval=st.selectbox("الفاصل",["1min","5min","15min","1h"],index=1)

@st.cache_data(ttl=120,show_spinner=False)
def history(symbol,interval):
    d=td("time_series",{"symbol":symbol,"interval":interval,"outputsize":120,"order":"ASC"})
    df=pd.DataFrame(d.get("values",[]))
    if df.empty:return df
    for c in ["open","high","low","close"]: df[c]=pd.to_numeric(df[c],errors="coerce")
    df["datetime"]=pd.to_datetime(df["datetime"])
    return df

try:
    h=history(chart_symbol,interval)
    if not h.empty:
        fig=go.Figure(data=[go.Candlestick(x=h["datetime"],open=h["open"],high=h["high"],low=h["low"],close=h["close"])])
        fig.update_layout(height=480,xaxis_rangeslider_visible=False,margin=dict(l=10,r=10,t=20,b=10))
        st.plotly_chart(fig,use_container_width=True)
except Exception as e: st.error(str(e))

st.caption("الأسهم والاتجاهات المتوقعة هي تحليل آلي وليست ضمانًا للحركة المستقبلية أو توصية استثمارية.")
st.markdown(f"<script>setTimeout(function(){{window.parent.location.reload();}}, {int(refresh)*1000});</script>",unsafe_allow_html=True)
