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

# Datenbank-Pfad
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

# Helfer: löscht eine Nachricht nach delay Sekunden
async def auto_delete(msg, delay: int):
    await asyncio.sleep(delay)
    try: await msg.delete()
    except BadRequest: pass

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
    except: pass

# /neuenspiel (Admins)
async def neuenspiel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Admin-Check
    member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
    if member.status not in ("administrator", "creator"):
        await update.message.delete()
        return

    text = update.message.text.partition(" ")[2]
    if "|" not in text:
        msg = await update.message.reply_text("📌 Nutze: /neuenspiel Beschreibung | YYYY-MM-DD HH:MM")
        asyncio.create_task(auto_delete(msg, 10))
        await update.message.delete()
        return

    besch, _, zeit = [p.strip() for p in text.partition("|")]
    try:
        dt = datetime.strptime(zeit, "%Y-%m-%d %H:%M")
    except ValueError:
        msg = await update.message.reply_text("❌ Format: YYYY-MM-DD HH:MM")
        asyncio.create_task(auto_delete(msg, 10))
        await update.message.delete()
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO spielen (beschreibung, startzeit) VALUES (?, ?)", (besch, dt.isoformat()))
    sid = c.lastrowid
    conn.commit()
    conn.close()

    reply = await update.message.reply_text(
        f"✅ Spiel {sid}: {besch} am {dt.strftime('%d.%m.%Y %H:%M')} angelegt."
    )
    asyncio.create_task(auto_delete(reply, 10))
    await update.message.delete()

    # Reminder 30 Minuten vor Anpfiff
    due = dt - timedelta(minutes=30)
    context.job_queue.run_once(
        send_reminder, when=due,
        chat_id=update.effective_chat.id,
        name=str(sid),
        data={'id': sid, 'desc': besch, 'time': dt.strftime('%H:%M')}
    )

# Reminder-Callback
async def send_reminder(context: CallbackContext):
    d = context.job.data
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=(f"⏰ Erinnerung: In 30 Min startet Spiel {d['id']}: "
              f"{d['desc']} um {d['time']} – gebt eure Tipps ab!")
    )

# /spiele (schönes Layout & Ephemeral)
async def spiele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Slash-Kommando löschen
    try: await update.message.delete()
    except: pass

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
        msg = await context.bot.send_message(chat_id=update.effective_chat.id, text="📌 Keine aktiven Spiele.")
        asyncio.create_task(auto_delete(msg, 15))
        return

    lines = ["📅 *Aktuelle Spiele:*\n"]
    for sid, besch, start in rows:
        dt = datetime.fromisoformat(start)
        datum = dt.strftime("%d.%m.%Y")
        uhr   = dt.strftime("%H:%M")
        lines.append(f"• *ID {sid}* — _{besch}_")
        lines.append(f"   🗓️ {datum}   ⏰ {uhr}")
        lines.append("")  # Leerzeile

    text = "\n".join(lines)
    msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode="Markdown")
    asyncio.create_task(auto_delete(msg, 15))

# /tippen (Dialog)
async def start_tippen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Slash-Kommando löschen
    try: await update.message.delete()
    except: pass

    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT spiel_id, beschreibung FROM spielen WHERE startzeit > ? ORDER BY startzeit", (now,))
    games = c.fetchall()
    conn.close()

    if not games:
        msg = await context.bot.send_message(chat_id=update.effective_chat.id, text="📌 Keine Spiele zum Tippen.")
        asyncio.create_task(auto_delete(msg, 15))
        return ConversationHandler.END

    text = "Auf welches Spiel möchtest du tippen?\n" + "\n".join(f"• {sid}: {besch}" for sid, besch in games)
    prompt = await context.bot.send_message(chat_id=update.effective_chat.id, text=text)
    context.user_data['prompt_msg'] = prompt
    asyncio.create_task(auto_delete(prompt, 20))
    return CHOOSING_GAME

async def choose_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message
    if not user_msg.text.isdigit():
        err = await user_msg.reply_text("❌ Zahl als Spiel-ID eingeben.")
        asyncio.create_task(auto_delete(err, 20))
        return CHOOSING_GAME

    # altes Prompt löschen
    old = context.user_data.pop('prompt_msg', None)
    if old:
        try: await old.delete()
        except: pass

    sid = int(user_msg.text)
    context.user_data['spiel_id'] = sid
    try: await user_msg.delete()
    except: pass

    prompt = await context.bot.send_message(chat_id=update.effective_chat.id, text="Wie lautet dein Tipp? Format `2:1`")
    context.user_data['prompt_msg'] = prompt
    asyncio.create_task(auto_delete(prompt, 20))
    return TYPING_SCORE

async def receive_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message
    sid = context.user_data['spiel_id']
    uid = user_msg.from_user.id

    # 1) Doppel-Tipp verhindern
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT 1 FROM tipps WHERE spiel_id = ? AND user_id = ?", (sid, uid))
    if c.fetchone():
        notice = await user_msg.reply_text("⚠️ Du hast bereits getippt.")
        asyncio.create_task(auto_delete(notice, 5))
        try: await user_msg.delete()
        except: pass
        conn.close()
        return ConversationHandler.END

    # 2) Zeit-Check
    c.execute("SELECT startzeit FROM spielen WHERE spiel_id = ?", (sid,))
    row = c.fetchone()
    conn.close()
    if not row:
        err = await user_msg.reply_text("❌ Ungültige Spiel-ID.")
        asyncio.create_task(auto_delete(err, 5))
        try: await user_msg.delete()
        except: pass
        return ConversationHandler.END

    if datetime.now() >= datetime.fromisoformat(row[0]):
        notice = await user_msg.reply_text("⏰ Tippphase vorbei.")
        asyncio.create_task(auto_delete(notice, 5))
        try: await user_msg.delete()
        except: pass
        return ConversationHandler.END

    # 3) Format-Prüfung
    text = user_msg.text.strip()
    if ":" not in text or not all(p.isdigit() for p in text.split(":",1)):
        err = await user_msg.reply_text("❌ Format bitte `2:1`.")
        asyncio.create_task(auto_delete(err, 20))
        return TYPING_SCORE

    heim, gast = map(int, text.split(":",1))
    user = user_msg.from_user.username or user_msg.from_user.first_name

    # Tipp speichern
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
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
    asyncio.create_task(auto_delete(thanks, 5))
    try: await user_msg.delete()
    except: pass
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Tippen abgebrochen.")
    asyncio.create_task(auto_delete(msg, 5))
    try: await update.message.delete()
    except: pass
    return ConversationHandler.END

# /rangliste
async def rangliste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("""
        SELECT username, COUNT(*) as punkte
        FROM tipps GROUP BY username 
        ORDER BY punkte DESC LIMIT 20
    """)
    rows = c.fetchall()
    conn.close()
    try: await update.message.delete()
    except: pass

    if not rows:
        msg = await context.bot.send_message(chat_id=update.effective_chat.id, text="Noch keine Tipps.")
        asyncio.create_task(auto_delete(msg, 30))
        return

    text = "🏆 *Rangliste* 🏆\n" + "\n".join(f"{i+1}. {u}: {p}" for i,(u,p) in enumerate(rows))
    msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode="Markdown")
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
