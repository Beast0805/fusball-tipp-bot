import os
import sys
import sqlite3
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import openai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.error import BadRequest, RetryAfter

# Logging konfigurieren
logging.basicConfig(level=logging.INFO)

# OpenAI API-Key aus Umgebungsvariablen
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    logging.error("OPENAI_API_KEY ist nicht gesetzt!")
    sys.exit(1)

# Telegram-Token aus Umgebungsvariablen
telegram_token = os.getenv("TELEGRAM_TOKEN")
if not telegram_token:
    logging.error("TELEGRAM_TOKEN ist nicht gesetzt!")
    sys.exit(1)

# Zeitzone und DB-Pfad
TZ = ZoneInfo("Europe/Berlin")
DB_PATH = os.path.join(os.getcwd(), "data", "database.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# Datenbank initialisieren
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS spielen (
            spiel_id     INTEGER PRIMARY KEY AUTOINCREMENT,
            beschreibung TEXT NOT NULL,
            startzeit    TEXT NOT NULL
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS tipps (
            spiel_id   INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            username   TEXT    NOT NULL,
            tore_heim  INTEGER NOT NULL,
            tore_gast  INTEGER NOT NULL,
            PRIMARY KEY (spiel_id, user_id)
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS ergebnisse (
            spiel_id   INTEGER PRIMARY KEY,
            tore_heim  INTEGER NOT NULL,
            tore_gast  INTEGER NOT NULL
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS punkte (
            spiel_id   INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            username   TEXT    NOT NULL,
            punkte     INTEGER NOT NULL,
            PRIMARY KEY (spiel_id, user_id)
        )
        """
    )
    conn.commit()
    conn.close()

# Helfer: löscht Nachrichten nach delay Sekunden
async def auto_delete(msg, delay: int):
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except (BadRequest, RetryAfter) as e:
        logging.warning(f"Failed to delete message: {e}")

# ChatGPT-Handler: alle Nicht-Commands landen hier
async def chatgpt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = update.effective_chat.id
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
        reply = "⚠️ Entschuldigung, gerade nicht verfügbar."
    await context.bot.send_message(chat_id=chat_id, text=reply)

# Start-Befehl als Beispiel
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hallo! Ich werde deine Nachrichten an ChatGPT weiterleiten.")

# Main
if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(telegram_token).build()
    # Start-Command
    app.add_handler(CommandHandler("start", start))
    # ChatGPT-Handler für beliebige Texte
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chatgpt_handler))
    # Bot starten (Long Polling)
    app.run_polling()
