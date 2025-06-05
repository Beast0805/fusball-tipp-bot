import os
import sqlite3
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- 1) Telegram-Token aus ENV lesen ---
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise RuntimeError("ENV VAR 'TOKEN' fehlt!")

# --- 2) Port aus ENV lesen (Render setzt das automatisch) ---
PORT = int(os.environ.get("PORT", "8443"))

# --- 3) Handler-Funktion(en) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Willkommen beim Fußball-Tipp-Bot!")

# Hier später weitere Handler: /tippen, /ergebnis, /rangliste hinzufügen.

# --- 4) Application erstellen und Webhook konfigurieren ---
if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))

    # Render setzt die Umgebungsvariable 'RENDER_EXTERNAL_URL' automatisch
    WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL")
    if not WEBHOOK_URL:
        raise RuntimeError("ENV VAR 'RENDER_EXTERNAL_URL' fehlt!")

    # Run Webhook:
    # - listen="0.0.0.0": bindet an alle Netzwerk-Interfaces
    # - port=PORT: Render leitet diesen Port nach außen weiter
    # - url_path=TOKEN: Telegram schickt Updates an /<TOKEN>
    # - webhook_url=f"{WEBHOOK_URL}/{TOKEN}": so meldet sich Telegram an
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
    )
