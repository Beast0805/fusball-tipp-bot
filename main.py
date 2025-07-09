```python
import os
import sqlite3
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import openai
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ConversationHandler,
    MessageHandler, filters, ContextTypes
)
from telegram.error import BadRequest, RetryAfter

# Logging konfigurieren
tlogging.basicConfig(level=logging.INFO)

# OpenAI API-Key aus Umgebungsvariablen
oopenai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    logging.error("OPENAI_API_KEY ist nicht gesetzt!")

# Telegram-Token
telegram_token = os.getenv("TELEGRAM_TOKEN")
if not telegram_token:
    logging.error("TELEGRAM_TOKEN ist nicht gesetzt!")

# Zeitzone und DB-Pfad
TZ = ZoneInfo("Europe/Berlin")
os.makedirs("data", exist_ok=True)
DB_PATH = "data/database.db"

# Conversation-States
CHOOSING_GAME, TYPING_SCORE = range(2)

# Datenbank initialisieren
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS spielen (
        spiel_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        beschreibung TEXT NOT NULL,
        startzeit    TEXT NOT NULL
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS tipps (
        spiel_id   INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        username   TEXT    NOT NULL,
        tore_heim  INTEGER NOT NULL,
        tore_gast  INTEGER NOT NULL,
        PRIMARY KEY (spiel_id, user_id)
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS ergebnisse (
        spiel_id   INTEGER PRIMARY KEY,
        tore_heim  INTEGER NOT NULL,
        tore_gast  INTEGER NOT NULL
    )""")
    c.execute("""
    CREATE TABLE IF NOT EXISTS punkte (
        spiel_id   INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        username   TEXT    NOT NULL,
        punkte     INTEGER NOT NULL,
        PRIMARY KEY (spiel_id, user_id)
    )""")
    conn.commit()
    conn.close()

# Helfer: löscht Nachrichten nach delay Sekunden
async def auto_delete(msg, delay: int):
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except BadRequest as e:
        logging.warning(f"Failed to delete message: {e}")
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after)
        try:
            await msg.delete()
        except Exception as ex:
            logging.warning(f"Retry delete failed: {ex}")

# ChatGPT-Handler: alle Nicht-Commands gehen hierhin
async def chatgpt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.effective_chat.id
    # Bot Activity anzeigen
    await context.bot.send_chat_action(chat_id, action="typing")
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Du bist ein hilfsbereiter Assistent."},
                {"role": "user",   "content": user_text}
            ]
        )
        reply = resp.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"OpenAI Error: {e}")
        reply = "⚠️ Entschuldigung, ich konnte gerade keine Antwort generieren."
    await context.bot.send_message(chat_id=chat_id, text=reply)

# Main
if __name__ == "__main__":
    # DB & Bot starten
    init_db()
    app = ApplicationBuilder().token(telegram_token).build()

    # Deine bestehenden CommandHandler hier registrieren, z.B.:
    # app.add_handler(CommandHandler("start", start))
    # app.add_handler(CommandHandler("neuenspiel", neuenspiel))
    # ...

    # Am Ende: ChatGPT-Handler
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, chatgpt_handler)
    )

    # Polling oder Webhook
    # Für lokalen Test:
    app.run_polling()
    # Für Deployment mit Webhook:
    # app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", 8443)), 
    #                 url_path=telegram_token, webhook_url=f"https://<dein-host>/{telegram_token}")
```

**Anleitung:**
1. Setze in Render oder deiner Shell die Environment-Variablen `TELEGRAM_TOKEN` und `OPENAI_API_KEY`.
2. Ersetze in den `app.add_handler(CommandHandler(...))`-Zeilen deine Tipp-Befehle.
3. Push & redeploy – jetzt chattet dein Bot via ChatGPT!
