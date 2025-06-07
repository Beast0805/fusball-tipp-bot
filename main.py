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

# Hilfsfunktion zum automatischen Löschen
async def auto_delete(message, delay: int):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except BadRequest:
        pass

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "Willkommen beim Fußball-Tipp-Bot!\n\n"
        "/neuenspiel – neues Spiel anlegen (Admins)\n"
        "/spiele      – aktuelle Partien ansehen\n"
        "/tippen      – Tipp abgeben (Dialog)\n"
        "/rangliste   – Top-Tipper"
    )
    # Optional: entferne den /start-Befehl selbst nach kurzer Zeit
    await asyncio.sleep(5)
    try: await msg.delete()
    except: pass
    try: await update.message.delete()
    except: pass

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
    c.execute("INSERT INTO spielen (beschreibung, startzeit) VALUES (?, ?)", (besch, dt.isoformat()))
    sid = c.lastrowid
    conn.commit()
    conn.close()
    reply = await update.message.reply_text(
        f"✅ Spiel {sid}: {besch} am {dt.strftime('%d.%m.%Y %H:%M')} angelegt."
    )
    # Reminder 30 Minuten vor Anpfiff
    due = dt - timedelta(minutes=30)
    context.job_queue.run_once(
        send_reminder, when=due, chat_id=update.effective_chat.id,
        name=str(sid),
        data={'id': sid, 'desc': besch, 'time': dt.strftime('%H:%M')}
    )
    # Lösche den Befehl
    try: await update.message.delete()
    except: pass

# Reminder-Callback
async def send_reminder(context: CallbackContext):
    data = context.job.data
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=f"⏰ Erinnerung: In 30 Min startet Spiel {data['id']}: {data['desc']} um {data['time']} – gebt eure Tipps ab!"
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
        asyncio.create_task(auto_delete(msg, 10))
        try: await update.message.delete()
        except: pass
        return
    text = "📅 *Aktuelle Spiele:*\n"
    for sid, besch, start in rows:
        dt = datetime.fromisoformat(start)
        text += f"• ID {sid}: {besch} ({dt.strftime('%d.%m.%Y %H:%M')})\n"
    msg = await update.message.reply_text(text, parse_mode="Markdown")
    asyncio.create_task(auto_delete(msg, 10))
    try: await update.message.delete()
    except: pass

# /tippen (Dialog)
async def start_tippen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT spiel_id, beschreibung FROM spielen WHERE startzeit > ? ORDER BY startzeit", (now,))
    games = c.fetchall()
    conn.close()
    if not games:
        msg = await update.message.reply_text("📌 Keine Spiele zum Tippen.")
        asyncio.create_task(auto_delete(msg, 10))
        try: await update.message.delete()
        except: pass
        return ConversationHandler.END
    text = "Auf welches Spiel möchtest du tippen?\n" + "\n".join(f"• {sid}: {besch}" for sid, besch in games)
    prompt = await update.message.reply_text(text)
    try: await update.message.delete()
    except: pass
    asyncio.create_task(auto_delete(prompt, 30))
    return CHOOSING_GAME

async def choose_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message
    if not user_msg.text.isdigit():
        err = await user_msg.reply_text("❌ Bitte Zahl als Spiel-ID eingeben.")
        asyncio.create_task(auto_delete(err, 30))
        return CHOOSING_GAME
    context.user_data['spiel_id'] = int(user_msg.text)
    prompt = await user_msg.reply_text("Wie lautet dein Tipp? Format `2:1`")
    try: await user_msg.delete()
    except: pass
    asyncio.create_task(auto_delete(prompt, 30))
    return TYPING_SCORE

async def receive_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message
    text = user_msg.text.strip()
    if ":" not in text or not all(p.isdigit() for p in text.split(":",1)):
        err = await user_msg.reply_text("❌ Format bitte `2:1`.")
        asyncio.create_task(auto_delete(err, 30))
        return TYPING_SCORE
    heim, gast = map(int, text.split(":",1))
    sid = context.user_data['spiel_id']
    uid = user_msg.from_user.id
    user = user_msg.from_user.username or user_msg.from_user.first_name
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO tipps (spiel_id,user_id,username,tore_heim,tore_gast) VALUES (?,?,?,?,?)",
        (sid, uid, user, heim, gast)
    )
    conn.commit()
    conn.close()
    thanks = await user_msg.reply_text(f"{user}, danke für deinen Tipp {heim}:{gast}! Viel Glück!")
    try: await user_msg.delete()
    except: pass
    asyncio.create_task(auto_delete(thanks, 15))
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("Tippen abgebrochen. Mit /tippen neu starten.")
    asyncio.create_task(auto_delete(msg, 15))
    try: await update.message.delete()
    except: pass
    return ConversationHandler.END

# /rangliste
async def rangliste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT username, SUM(punkte) as punkte 
        FROM tipps GROUP BY username 
        ORDER BY punkte DESC LIMIT 20
    """)
    rows = c.fetchall()
    conn.close()
    if not rows:
        msg = await update.message.reply_text("Noch keine Tipps.")
        asyncio.create_task(auto_delete(msg, 30))
        try: await update.message.delete()
        except: pass
        return
    text = "🏆 *Rangliste* 🏆\n" + "\n".join(f"{i+1}. {u}: {p}" for i,(u,p) in enumerate(rows))
    msg = await update.message.reply_text(text, parse_mode="Markdown")
    asyncio.create_task(auto_delete(msg, 30))
    try: await update.message.delete()
    except: pass

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
