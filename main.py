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

# Conversation-States
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

# Helfer: löscht Nachrichten nach delay Sekunden
async def auto_delete(msg, delay: int):
    await asyncio.sleep(delay)
    try:
        await msg.delete()
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
        msg = await cmd.reply_text("📌 Nutze: /neuenspiel Beschreibung | YYYY-MM-DD HH:MM")
        asyncio.create_task(auto_delete(msg, 10))
        await cmd.delete()
        return

    besch, _, zeit = [p.strip() for p in text.partition("|")]
    try:
        dt = datetime.strptime(zeit, "%Y-%m-%d %H:%M")
    except ValueError:
        msg = await cmd.reply_text("❌ Format: YYYY-MM-DD HH:MM")
        asyncio.create_task(auto_delete(msg, 10))
        await cmd.delete()
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO spielen (beschreibung, startzeit) VALUES (?, ?)", (besch, dt.isoformat()))
    sid = c.lastrowid
    conn.commit()
    conn.close()

    reply = await cmd.reply_text(
        f"✅ Spiel {sid}: {besch} am {dt.strftime('%d.%m.%Y %H:%M')} angelegt."
    )
    asyncio.create_task(auto_delete(reply, 10))
    await cmd.delete()

    # Reminder 30 Minuten vor Anpfiff
    due = dt - timedelta(minutes=30)
    context.job_queue.run_once(
        send_reminder,
        when=due,
        chat_id=cmd.chat_id,
        name=str(sid),
        data={'id': sid, 'desc': besch, 'time': dt.strftime('%H:%M')}
    )

# Reminder-Callback
async def send_reminder(context: CallbackContext):
    d = context.job.data
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=(
            f"⏰ Erinnerung: In 30 Min startet Spiel {d['id']}: "
            f"{d['desc']} um {d['time']} – tippt jetzt!"
        )
    )

# /spiele – schön formatiert
async def spiele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute(
        "SELECT spiel_id, beschreibung, startzeit FROM spielen "
        "WHERE startzeit > ? ORDER BY startzeit", (now,)
    )
    rows = c.fetchall()
    conn.close()

    await cmd.delete()
    if not rows:
        msg = await cmd.reply_text("📌 Keine aktiven Spiele.")
        asyncio.create_task(auto_delete(msg, 15))
        return

    lines = ["📅 *Aktuelle Spiele:*",""]
    for sid, besch, start in rows:
        dt = datetime.fromisoformat(start)
        lines.append(f"• *ID {sid}* — _{besch}_")
        lines.append(f"   🗓️ {dt.strftime('%d.%m.%Y')}   ⏰ {dt.strftime('%H:%M')}")
        lines.append("")
    text = "\n".join(lines)

    msg = await cmd.reply_text(text, parse_mode="Markdown")
    asyncio.create_task(auto_delete(msg, 15))

# /tippen (Dialog) – nur verbleibende, formatiert
async def start_tippen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message
    try: await cmd.delete()
    except: pass

    user = cmd.from_user.first_name
    uid = cmd.from_user.id
    now = datetime.now().isoformat()

    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT spiel_id, beschreibung, startzeit FROM spielen WHERE startzeit > ? ORDER BY startzeit", (now,))
    active = c.fetchall()
    c.execute("SELECT spiel_id FROM tipps WHERE user_id = ?", (uid,))
    tipped = {r[0] for r in c.fetchall()}
    conn.close()

    remaining = [(sid, desc, start) for sid, desc, start in active if sid not in tipped]
    if not remaining:
        notice = await context.bot.send_message(
            chat_id=cmd.chat_id,
            text=f"{user}, du hast bereits auf alle aktiven Spiele getippt. Viel Glück!"
        )
        asyncio.create_task(auto_delete(notice, 5))
        return ConversationHandler.END

    lines = [f"{user}, auf welches Spiel möchtest du tippen?",""]
    for sid, besch, start in remaining:
        dt = datetime.fromisoformat(start)
        lines.append(f"• *ID {sid}* — _{besch}_")
        lines.append(f"   🗓️ {dt.strftime('%d.%m.%Y')}   ⏰ {dt.strftime('%H:%M')}")
        lines.append("")
    text = "\n".join(lines)

    prompt = await context.bot.send_message(chat_id=cmd.chat_id, text=text, parse_mode="Markdown")
    context.user_data['prompt_msg'] = prompt
    asyncio.create_task(auto_delete(prompt, 10))
    return CHOOSING_GAME

async def choose_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message
    user = user_msg.from_user.first_name

    if not user_msg.text.isdigit():
        err = await user_msg.reply_text(f"{user}, bitte gib eine Zahl als Spiel-ID ein.")
        asyncio.create_task(auto_delete(err, 5))
        return CHOOSING_GAME

    old = context.user_data.pop('prompt_msg', None)
    if old:
        try: await old.delete()
        except: pass

    sid = int(user_msg.text)
    context.user_data['spiel_id'] = sid
    try: await user_msg.delete()
    except: pass

    prompt = await user_msg.reply_text(f"{user}, wie lautet dein Tipp? Format `2:1`")
    context.user_data['prompt_msg'] = prompt
    asyncio.create_task(auto_delete(prompt, 10))
    return TYPING_SCORE

async def receive_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message
    user = user_msg.from_user.first_name
    sid = context.user_data['spiel_id']
    uid = user_msg.from_user.id

    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    # Doppel-Tipp prüfen
    c.execute("SELECT 1 FROM tipps WHERE spiel_id = ? AND user_id = ?", (sid, uid))
    if c.fetchone():
        notice = await user_msg.reply_text(f"{user}, du hast bereits getippt.")
        asyncio.create_task(auto_delete(notice, 5))
        await user_msg.delete()
        conn.close()
        return ConversationHandler.END

    # Zeit-Check
    c.execute("SELECT startzeit FROM spielen WHERE spiel_id = ?", (sid,))
    row = c.fetchone()
    conn.close()
    if not row or datetime.now() >= datetime.fromisoformat(row[0]):
        notice = await user_msg.reply_text(f"{user}, die Tippphase ist vorbei.")
        asyncio.create_task(auto_delete(notice, 5))
        await user_msg.delete()
        return ConversationHandler.END

    # altes Prompt löschen
    old = context.user_data.pop('prompt_msg', None)
    if old:
        try: await old.delete()
        except: pass

    # Format prüfen
    txt = user_msg.text.strip()
    if ":" not in txt or not all(p.isdigit() for p in txt.split(":",1)):
        err = await user_msg.reply_text(f"{user}, bitte im Format `2:1` tippen.")
        asyncio.create_task(auto_delete(err, 5))
        return TYPING_SCORE

    heim, gast = map(int, txt.split(":",1))
    username = user_msg.from_user.username or user

    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute(
        "INSERT INTO tipps (spiel_id,user_id,username,tore_heim,tore_gast) VALUES (?,?,?,?,?)",
        (sid, uid, username, heim, gast)
    )
    conn.commit()
    conn.close()

    thanks = await user_msg.reply_text(f"{user}, danke für deinen Tipp {heim}:{gast}! Viel Glück!")
    asyncio.create_task(auto_delete(thanks, 5))
    await user_msg.delete()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message
    msg = await cmd.reply_text("Tippen abgebrochen.")
    asyncio.create_task(auto_delete(msg, 5))
    try: await cmd.delete()
    except: pass
    return ConversationHandler.END

# /rangliste
async def rangliste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("""
        SELECT username, COUNT(*) as punkte
        FROM tipps GROUP BY username
        ORDER BY punkte DESC LIMIT 20
    """)
    rows = c.fetchall()
    conn.close()

    await cmd.delete()
    if not rows:
        msg = await cmd.reply_text("Noch keine Tipps.")
        asyncio.create_task(auto_delete(msg, 30))
        return

    text = "🏆 *Rangliste* 🏆\n" + "\n".join(
        f"{i+1}. {u}: {p}" for i,(u,p) in enumerate(rows)
    )
    msg = await cmd.reply_text(text, parse_mode="Markdown")
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
                TYPING_SCORE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_score)],
            },
            fallbacks=[CommandHandler("abbrechen", cancel)],
            per_user=True,
            per_chat=True
        )
    )
    app.add_handler(CommandHandler("rangliste", rangliste))

    WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL")
    PORT        = int(os.environ.get("PORT", "8443"))
    app.run_webhook(
        listen     = "0.0.0.0",
        port       = PORT,
        url_path   = os.environ["TOKEN"],
        webhook_url= f"{WEBHOOK_URL}/{os.environ['TOKEN']}"
    )
