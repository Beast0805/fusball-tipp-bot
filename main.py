import os
import sqlite3
import asyncio
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ConversationHandler,
    MessageHandler, filters, ContextTypes, CallbackContext
)

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

# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Willkommen beim Fußball-Tipp-Bot!\n\n"
        "/neuenspiel – neues Spiel anlegen (Admins)\n"
        "/spiele      – aktuelle Partien ansehen\n"
        "/tippen      – Tipp abgeben (Dialog)\n"
        "/rangliste   – Top-Tipper"
    )

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
    await update.message.reply_text(f"✅ Spiel {sid}: {besch} am {dt.strftime('%d.%m.%Y %H:%M')} angelegt.")
    # Reminder 30 Min vorher
    due = dt - timedelta(minutes=30)
    context.job_queue.run_once(
        send_reminder, when=due, chat_id=update.effective_chat.id,
        name=str(sid),
        data={'id': sid, 'desc': besch, 'time': dt.strftime('%H:%M')}
    )

# Reminder callback
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
        return await update.message.reply_text("📌 Keine aktiven Spiele.")
    text = "📅 *Aktuelle Spiele:*\n"
    for sid, besch, start in rows:
        dt = datetime.fromisoformat(start)
        text += f"• ID {sid}: {besch} ({dt.strftime('%d.%m.%Y %H:%M')})\n"
    await update.message.reply_text(text, parse_mode="Markdown")

# /tippen (Dialog)
async def start_tippen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT spiel_id, beschreibung FROM spielen WHERE startzeit > ? ORDER BY startzeit", (now,))
    games = c.fetchall()
    conn.close()
    if not games:
        return await update.message.reply_text("📌 Keine Spiele zum Tippen.")
    text = "Auf welches Spiel möchtest du tippen?\n"
    for sid, besch in games:
        text += f"• {sid}: {besch}\n"
    await update.message.reply_text(text)
    return CHOOSING_GAME

async def choose_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.isdigit():
        await update.message.reply_text("❌ Bitte eine Zahl als Spiel-ID eingeben.")
        return CHOOSING_GAME
    context.user_data['spiel_id'] = int(update.message.text)
    await update.message.reply_text("Wie lautet dein Tipp? Format `2:1`")
    return TYPING_SCORE

async def receive_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if ":" not in text:
        await update.message.reply_text("❌ Format bitte `H:G`, z.B. `2:1`")
        return TYPING_SCORE
    home, away = text.split(":", 1)
    if not (home.isdigit() and away.isdigit()):
        await update.message.reply_text("❌ Beide Teile müssen Zahlen sein.")
        return TYPING_SCORE
    sid = context.user_data['spiel_id']
    uid = update.effective_user.id
    user = update.effective_user.username or update.effective_user.first_name
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO tipps (spiel_id, user_id, username, tore_heim, tore_gast) VALUES (?,?,?,?,?)",
        (sid, uid, user, int(home), int(away))
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(f"{user}, vielen Dank für deinen Tipp {home}:{away}! Viel Glück!")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Tippen abgebrochen. Mit /tippen neu starten.")
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
    text = "🏆 Rangliste 🏆\n"
    for i, (user, pts) in enumerate(rows, start=1):
        text += f"{i}. {user}: {pts}\n"
    await update.message.reply_text(text)

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

    app.run_polling()
