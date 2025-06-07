import os
import sqlite3
import asyncio
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ConversationHandler,
    MessageHandler, filters, ContextTypes, CallbackContext
)
from telegram.error import BadRequest

# Conversation states
CHOOSING_GAME, TYPING_SCORE = range(2)

# Pfad zur Datenbank
os.makedirs("/data", exist_ok=True)
DB_PATH = "/data/database.db"

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
        username   TEXT NOT NULL,
        tore_heim  INTEGER NOT NULL,
        tore_gast  INTEGER NOT NULL,
        PRIMARY KEY (spiel_id, user_id)
    )""")
    conn.commit()
    conn.close()

# Helfer zum verzögerten Löschen
async def auto_delete(message, delay: int):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except BadRequest:
        pass

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "Willkommen beim Tipp-Bot!\n"
        "/neuenspiel – neues Spiel anlegen (Admins)\n"
        "/spiele      – aktuelle Partien ansehen\n"
        "/tippen      – Tipp abgeben (Dialog)\n"
        "/rangliste   – Top-Tipper"
    )
    await asyncio.sleep(5)
    try:
        await msg.delete()
        await update.message.delete()
    except:
        pass

# /neuenspiel (Admins)
async def neuenspiel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    if member.status not in ("administrator", "creator"):
        return await update.message.reply_text("❌ Nur Admins dürfen neue Spiele anlegen.")
    text = update.message.text.partition(" ")[2]
    if "|" not in text:
        return await update.message.reply_text("📌 Nutze: /neuenspiel Beschreibung | YYYY-MM-DD HH:MM")
    besch, _, zeit = [p.strip() for p in text.partition("|")]
    try:
        dt = datetime.strptime(zeit, "%Y-%m-%d %H:%M")
    except ValueError:
        return await update.message.reply_text("❌ Format: YYYY-MM-DD HH:MM")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO spielen (beschreibung, startzeit) VALUES (?, ?)",
              (besch, dt.isoformat()))
    sid = c.lastrowid
    conn.commit()
    conn.close()

    reply = await update.message.reply_text(
        f"✅ Spiel {sid}: {besch} am {dt.strftime('%d.%m.%Y %H:%M')} angelegt."
    )
    try: await update.message.delete()
    except: pass
    asyncio.create_task(auto_delete(reply, 10))

    # Reminder 30 Minuten vor Spiel
    due = dt - timedelta(minutes=30)
    context.job_queue.run_once(
        send_reminder, when=due, chat_id=update.effective_chat.id,
        name=str(sid),
        data={'id': sid, 'desc': besch, 'time': dt.strftime('%H:%M')}
    )

# Reminder-Callback
async def send_reminder(context: CallbackContext):
    data = context.job.data
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=(f"⏰ Erinnerung: In 30 Min startet Spiel {data['id']}: "
              f"{data['desc']} um {data['time']} – gebt eure Tipps ab!")
    )

# /spiele
async def spiele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT spiel_id, beschreibung, startzeit FROM spielen "
        "WHERE startzeit > ? ORDER BY startzeit", (now,)
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        msg = await update.message.reply_text("📌 Keine aktiven Spiele.")
        try: await update.message.delete()
        except: pass
        asyncio.create_task(auto_delete(msg, 15))
        return
    text = "📅 *Aktuelle Spiele:*\n" + "\n".join(
        f"• ID {sid}: {besch} ({datetime.fromisoformat(start).strftime('%d.%m.%Y %H:%M')})"
        for sid, besch, start in rows
    )
    msg = await update.message.reply_text(text, parse_mode="Markdown")
    try: await update.message.delete()
    except: pass
    asyncio.create_task(auto_delete(msg, 15))

# /tippen (Dialog)
async def start_tippen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT spiel_id, beschreibung FROM spielen WHERE startzeit > ? ORDER BY startzeit",
        (now,)
    )
    games = c.fetchall()
    conn.close()
    if not games:
        msg = await update.message.reply_text("📌 Keine Spiele zum Tippen.")
        try: await update.message.delete()
        except: pass
        asyncio.create_task(auto_delete(msg, 15))
        return ConversationHandler.END

    text = "Auf welches Spiel möchtest du tippen?\n" + "\n".join(f"• {sid}: {besch}"
                                                                 for sid, besch in games)
    prompt = await update.message.reply_text(text)
    try: await update.message.delete()
    except: pass
    context.user_data['prompt_msg'] = prompt
    asyncio.create_task(auto_delete(prompt, 20))
    return CHOOSING_GAME

async def choose_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message
    if not user_msg.text.isdigit():
        err = await user_msg.reply_text("❌ Bitte Zahl als Spiel-ID eingeben.")
        asyncio.create_task(auto_delete(err, 20))
        return CHOOSING_GAME

    # altes Prompt löschen
    old = context.user_data.pop('prompt_msg', None)
    if old:
        try: await old.delete()
        except: pass

    sid = int(user_msg.text)
    context.user_data['spiel_id'] = sid

    prompt = await user_msg.reply_text("Wie lautet dein Tipp? Format `2:1`")
    try: await user_msg.delete()
    except: pass
    context.user_data['prompt_msg'] = prompt
    asyncio.create_task(auto_delete(prompt, 20))
    return TYPING_SCORE

async def receive_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message
    sid = context.user_data['spiel_id']
    uid = user_msg.from_user.id

    # 1) Doppel-Tipp prüfen
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM tipps WHERE spiel_id = ? AND user_id = ?", (sid, uid))
    if c.fetchone():
        conn.close()
        notice = await user_msg.reply_text("⚠️ Du hast bereits getippt.")
        asyncio.create_task(auto_delete(notice, 10))
        try: await user_msg.delete()
        except: pass
        return ConversationHandler.END

    # 2) Zeit-Check
    c.execute("SELECT startzeit FROM spielen WHERE spiel_id = ?", (sid,))
    row = c.fetchone()
    conn.close()
    if not row:
        err = await user_msg.reply_text("❌ Ungültige Spiel-ID.")
        asyncio.create_task(auto_delete(err, 10))
        try: await user_msg.delete()
        except: pass
        return ConversationHandler.END

    start_dt = datetime.fromisoformat(row[0])
    if datetime.now() >= start_dt:
        notice = await user_msg.reply_text("⏰ Tippphase vorbei.")
        asyncio.create_task(auto_delete(notice, 10))
        try: await user_msg.delete()
        except: pass
        return ConversationHandler.END

    # 3) Format prüfen
    text = user_msg.text.strip()
    if ":" not in text or not all(p.isdigit() for p in text.split(":",1)):
        err = await user_msg.reply_text("❌ Format bitte `2:1`.")
        asyncio.create_task(auto_delete(err, 20))
        return TYPING_SCORE

    heim, gast = map(int, text.split(":",1))
    user = user_msg.from_user.username or user_msg.from_user.first_name

    # Tipp speichern
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO tipps (spiel_id,user_id,username,tore_heim,tore_gast) VALUES (?,?,?,?,?)",
        (sid, uid, user, heim, gast)
    )
    conn.commit()
    conn.close()

    # altes Prompt löschen
    old = context.user_data.pop('prompt_msg', None)
    if old:
        try: await old.delete()
        except: pass

    thanks = await user_msg.reply_text(f"{user}, danke für deinen Tipp {heim}:{gast}! Viel Glück!")
    try: await user_msg.delete()
    except: pass
    asyncio.create_task(auto_delete(thanks, 5))
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Tippen abgebrochen.")
    try: await update.message.delete()
    except: pass
    asyncio.create_task(auto_delete(msg, 5))
    return ConversationHandler.END

# /rangliste
async def rangliste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT username, COUNT(*) as punkte
        FROM tipps GROUP BY username
        ORDER BY punkte DESC LIMIT 20
    """)
    rows = c.fetchall()
    conn.close()
    if not rows:
        msg = await update.message.reply_text("Noch keine Tipps.")
        try: await update.message.delete()
        except: pass
        asyncio.create_task(auto_delete(msg, 30))
        return
    text = "🏆 *Rangliste* 🏆\n" + "\n".join(f"{i+1}. {u}: {p}" for i,(u,p) in enumerate(rows))
    msg = await update.message.reply_text(text, parse_mode="Markdown")
    try: await update.message.delete()
    except: pass
    asyncio.create_task(auto_delete(msg, 30))

# Main
if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(os.environ["TOKEN"]).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("neuenspiel", neuenspiel))
    app.add_handler(CommandHandler("spiele", spiele))
    app.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("tippen", start_tippen)],
            states={
                CHOOSING_GAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_game)],
                TYPING_SCORE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_score)],
            },
            fallbacks=[CommandHandler("abbrechen", cancel)],
            per_user=True,
            per_chat=True
        )
    )
    app.add_handler(CommandHandler("rangliste", rangliste))

    # Webhook-Setup für Render
    WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL")
    PORT        = int(os.environ.get("PORT", "8443"))
    app.run_webhook(
        listen     = "0.0.0.0",
        port       = PORT,
        url_path   = os.environ["TOKEN"],
        webhook_url= f"{WEBHOOK_URL}/{os.environ['TOKEN']}"
    )
