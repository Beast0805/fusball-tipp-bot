import os
import sqlite3
import asyncio
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ConversationHandler,
    MessageHandler, filters, ContextTypes
)
from telegram.error import BadRequest, RetryAfter

# Conversation-States
CHOOSING_GAME, TYPING_SCORE = range(2)

# DB-Pfad Render-kompatibel
os.makedirs("/data", exist_ok=True)
DB_PATH = "/data/database.db"

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
    except BadRequest:
        return
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after)
        try: await msg.delete()
        except: pass

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "Willkommen beim Tipp-Bot!\n"
        "/neuenspiel       – neues Spiel anlegen (Admins)\n"
        "/spiele            – aktuelle Partien ansehen\n"
        "/spielealle        – *ALLE* Spiele in der DB (Debug)\n"
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
    try: await update.message.delete()
    except: pass

# Debug: listet alle Spiele in der Datenbank
async def spielealle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT spiel_id, beschreibung FROM spielen ORDER BY spiel_id")
    rows = c.fetchall()
    conn.close()
    if not rows:
        text = "Keine Spiele in der Datenbank."
    else:
        text = "Spiele in DB:\n" + "\n".join(f"ID {sid}: {besch}" for sid, besch in rows)
    msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    asyncio.create_task(auto_delete(msg, 15))

# Debug: zeigt DB_PATH und Existenz
async def dbinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    exists = os.path.exists(DB_PATH)
    text = f"DB_PATH = {DB_PATH}\nExistiert: {exists}"
    msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    asyncio.create_task(auto_delete(msg, 15))

# /neuenspiel (Admin + Dublettencheck)
async def neuenspiel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message
    try:
        member = await context.bot.get_chat_member(cmd.chat_id, cmd.from_user.id)
        if member.status not in ("administrator", "creator"):
            await cmd.delete()
            return
    except:
        await cmd.delete()
        return
    text = cmd.text.partition(" ")[2]
    if "|" not in text:
        err = await context.bot.send_message(cmd.chat_id, "📌 Nutze: /neuenspiel Beschreibung | YYYY-MM-DD HH:MM")
        asyncio.create_task(auto_delete(err,10))
        try: await cmd.delete()
        except: pass
        return
    besch, _, zeit = [p.strip() for p in text.partition("|")]
    try:
        dt = datetime.strptime(zeit, "%Y-%m-%d %H:%M")
    except ValueError:
        err = await context.bot.send_message(cmd.chat_id, "❌ Datum/Uhrzeit im Format YYYY-MM-DD HH:MM")
        asyncio.create_task(auto_delete(err,10))
        try: await cmd.delete()
        except: pass
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM spielen WHERE beschreibung=? AND startzeit=?", (besch, dt.isoformat()))
    if c.fetchone():
        dup = await context.bot.send_message(cmd.chat_id, "❌ Dieses Spiel wurde bereits angelegt.")
        asyncio.create_task(auto_delete(dup,10))
        conn.close()
        try: await cmd.delete()
        except: pass
        return
    c.execute("INSERT INTO spielen (beschreibung, startzeit) VALUES (?, ?)", (besch, dt.isoformat()))
    sid = c.lastrowid
    conn.commit()
    conn.close()
    reply = await context.bot.send_message(
        chat_id=cmd.chat_id,
        text=f"✅ Spiel {sid}: *{besch}* am {dt.strftime('%d.%m.%Y %H:%M')} angelegt.",
        parse_mode="Markdown"
    )
    asyncio.create_task(auto_delete(reply,10))
    try: await cmd.delete()
    except: pass
    due = dt - timedelta(minutes=30)
    context.job_queue.run_once(send_reminder, when=due, chat_id=cmd.chat_id,
                               name=str(sid), data={'id': sid, 'desc': besch, 'time': dt.strftime('%H:%M')})

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=f"⏰ Erinnerung: In 30 Min startet Spiel {d['id']}: {d['desc']} um {d['time']} – tippt jetzt!"
    )

# /spiele
async def spiele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT spiel_id, beschreibung, startzeit FROM spielen WHERE startzeit > ? ORDER BY startzeit", (now,))
    rows = c.fetchall()
    conn.close()
    if not rows:
        msg = await context.bot.send_message(cmd.chat_id, "📌 Keine aktiven Spiele.")
        asyncio.create_task(auto_delete(msg,15))
    else:
        lines = ["📅 *Aktuelle Spiele:*",""]
        for sid, besch, start in rows:
            dt = datetime.fromisoformat(start)
            lines.append(f"• *ID {sid}* — _{besch}_")
            lines.append(f"   🗓️ {dt.strftime('%d.%m.%Y')}   ⏰ {dt.strftime('%H:%M')}")
            lines.append("")
        text = "\n".join(lines)
        msg = await context.bot.send_message(cmd.chat_id, text=text, parse_mode="Markdown")
        asyncio.create_task(auto_delete(msg,15))
    try: await cmd.delete()
    except: pass

# Tipp-Dialog /tippen, choose_game, receive_score, cancel...
# (Hier folgen deine bestehenden Funktionen start_tippen, choose_game, receive_score, cancel)

# /ergebnis und /loeschenspiel wie vorher definiert...

async def rangliste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT username, SUM(punkte) as punkte FROM punkte GROUP BY username ORDER BY punkte DESC LIMIT 20")
    rows = c.fetchall(); conn.close()
    if not rows:
        msg = await context.bot.send_message(cmd.chat_id, "Noch keine ausgewerteten Tipps.")
        asyncio.create_task(auto_delete(msg,20))
    else:
        lines = ["🏆 *Rangliste* 🏆",""]
        for i,(u,p) in enumerate(rows):
            lines.append(f"{i+1}. {u}: {p} Punkte")
        text = "\n".join(lines)
        msg = await context.bot.send_message(cmd.chat_id, text=text, parse_mode="Markdown")
        asyncio.create_task(auto_delete(msg,30))
    try: await cmd.delete()
    except: pass

if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(os.environ["TOKEN"]).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("neuenspiel", neuenspiel))
    app.add_handler(CommandHandler("spiele", spiele))
    app.add_handler(CommandHandler("spielealle", spielealle))
    app.add_handler(CommandHandler("dbinfo", dbinfo))
    app.add_handler(CommandHandler("ergebnis", ergebnis))
    app.add_handler(CommandHandler("loeschenspiel", loeschenspiel))
    conv = ConversationHandler(
        entry_points=[CommandHandler("tippen", start_tippen)],
        states={
            CHOOSING_GAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_game)],
            TYPING_SCORE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_score)],
        },
        fallbacks=[CommandHandler("abbrechen", cancel)],
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("rangliste", rangliste))
    WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL")
    PORT = int(os.environ.get("PORT", "8443"))
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=os.environ["TOKEN"],
        webhook_url=f"{WEBHOOK_URL}/{os.environ['TOKEN']}"
    )
