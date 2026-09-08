# Global Market AI Pro

## ماذا يفعل؟
- يتابع الذهب، العملات، SPY، QQQ والنفط من Twelve Data.
- يجلب الأخبار الحديثة من GDELT.
- يصنف نوع الحدث ويعطيه قوة من 0 إلى 100.
- يقدّر الاتجاه المحتمل على الذهب والدولار والأسهم والنفط.
- يرسل Telegram تلقائياً من watcher.py.
- يحفظ آخر التنبيهات في SQLite ويعرضها في Dashboard.
- يدعم FinBERT اختيارياً لتحليل نبرة النص المالي.

## التشغيل السريع
1) ثبّت Python 3.10+.
2) انسخ `.env.example` إلى `.env`.
3) ضع:
   - TWELVEDATA_API_KEY
   - TELEGRAM_BOT_TOKEN
   - TELEGRAM_CHAT_ID
4) Windows: شغّل `run_windows.bat`
5) Mac/Linux: شغّل `run_mac_linux.sh`

## تفعيل FinBERT (اختياري)
Windows: شغّل `install_ai_windows.bat`
ثم غيّر في `.env`:
ENABLE_FINBERT=1

ملاحظة: تنزيل FinBERT كبير نسبياً (مئات الميغابايت) في أول مرة.

## روابط رسمية
Twelve Data: https://twelvedata.com/
Telegram Bot API: https://core.telegram.org/bots/api
GDELT: https://www.gdeltproject.org/
FinBERT: https://huggingface.co/ProsusAI/finbert

هذه أداة معلومات وليست توصية استثمارية.
