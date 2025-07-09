import os
import sqlite3
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ConversationHandler,
    MessageHandler, filters, ContextTypes
)
from telegram.error import BadRequest, RetryAfter

# Logging konfigurieren
logging.basicConfig(level=logging.INFO)

# Conversation-States
CHOOSING_GAME, TYPING_SCORE = range(2)

# DB-Pfad Render-kompatibel
os.makedirs("data", exist_ok=True)
DB_PATH = "data/database.db"

# Standard-Zeitzone
TZ = ZoneInfo("Europe/Berlin")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Tabellen anlegen
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

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "Willkommen beim Tipp-Bot!\n"
        "/neuenspiel [--persistent] – neues Spiel anlegen (Admins)\n"
        "/spiele            – aktuelle Partien ansehen\n"
        "/dbinfo            – Zeigt DB-Pfad und Existenz (Debug)\n"
        "/tippen            – Tipp abgeben (Dialog)\n"
        "/ergebnis         – Ergebnis eintragen (Admins)\n"
        "/loeschenspiel    – Spiel löschen (Admins)\n"
        "/rangliste        – Top-Tipper"
    )
    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=help_text,
        parse_mode="Markdown"
    )
    asyncio.create_task(auto_delete(msg, 8))
    try:
        await update.message.delete()
    except Exception as e:
        logging.warning(f"Could not delete /start command: {e}")

# Debug: zeigt DB_PATH und Existenz
async def dbinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    exists = os.path.exists(DB_PATH)
    text = f"DB_PATH = {DB_PATH}\nExistiert: {exists}"
    msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    asyncio.create_task(auto_delete(msg, 15))
    try:
        await update.message.delete()
    except Exception as e:
        logging.warning(f"Could not delete /dbinfo command: {e}")

# /neuenspiel (Admin + Validierung)
async def neuenspiel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message
    # Nur Admins
    try:
        member = await context.bot.get_chat_member(cmd.chat_id, cmd.from_user.id)
        if member.status not in ("administrator", "creator"):
            await cmd.delete()
            return
    except Exception as e:
        logging.warning(f"Admin check failed: {e}")
        await cmd.delete()
        return

    text = cmd.text.partition(" ")[2]
    persistent = False
    if text.startswith("--persistent "):
        persistent = True
        text = text.replace("--persistent ", "", 1)

    if "|" not in text:
        err = await context.bot.send_message(cmd.chat_id,
            "📌 Nutze: /neuenspiel [--persistent] Beschreibung | YYYY-MM-DD HH:MM")
        asyncio.create_task(auto_delete(err, 10))
        await cmd.delete()
        return

    besch, zeit = [p.strip() for p in text.split("|", 1)]
    if not besch or not zeit:
        err = await context.bot.send_message(cmd.chat_id, "❌ Beschreibung und Zeit dürfen nicht leer sein.")
        asyncio.create_task(auto_delete(err, 10))
        await cmd.delete()
        return

    try:
        dt_naive = datetime.strptime(zeit, "%Y-%m-%d %H:%M")
        dt = dt_naive.replace(tzinfo=TZ)
    except ValueError:
        err = await context.bot.send_message(cmd.chat_id,
            "❌ Datum/Uhrzeit im Format YYYY-MM-DD HH:MM")
        asyncio.create_task(auto_delete(err, 10))
        await cmd.delete()
        return

    # DB-Insert
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM spielen WHERE beschreibung=? AND startzeit=?",
              (besch, dt.isoformat()))
    if c.fetchone():
        dup = await context.bot.send_message(cmd.chat_id,
            "❌ Dieses Spiel wurde bereits angelegt.")
        asyncio.create_task(auto_delete(dup, 10))
        conn.close()
        await cmd.delete()
        return

    c.execute("INSERT INTO spielen (beschreibung, startzeit) VALUES (?, ?)",
              (besch, dt.isoformat()))
    sid = c.lastrowid
    conn.commit()
    conn.close()

    reply = await context.bot.send_message(
        chat_id=cmd.chat_id,
        text=f"✅ Spiel {sid}: *{besch}* am {dt.strftime('%d.%m.%Y %H:%M')} angelegt.",
        parse_mode="Markdown"
    )
    if not persistent:
        asyncio.create_task(auto_delete(reply, 10))

    await cmd.delete()

    # Reminder 30 Min vorher, nur wenn in Zukunft
    now = datetime.now(TZ)
    due = dt - timedelta(minutes=30)
    if due > now:
        seconds_until = (due - now).total_seconds()
        context.job_queue.run_once(
            send_reminder,
            when=seconds_until,
            chat_id=cmd.chat_id,
            name=str(sid),
            data={'id': sid, 'desc': besch, 'time': dt.strftime('%H:%M')}
        )
    else:
        logging.info(f"Reminder time for Spiel {sid} is in the past; skipping reminder")

# Reminder-Callback
async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=f"⏰ Erinnerung: In 30 Min startet Spiel {d['id']}: {d['desc']} um {d['time']} – tippt jetzt!"
    )

# ... (Rest des Codes bleibt inhaltlich gleich, nur mit Fehler-Logging in try/except-Blöcken) ...

if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(os.environ["TOKEN"]).build()
    # Handler registrieren
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dbinfo", dbinfo))
    app.add_handler(CommandHandler("neuenspiel", neuenspiel))
    # ... ConversationHandler und weitere Handler hier ...
    app.run_webhook(...)
