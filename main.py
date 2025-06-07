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

    # Spiele-Tabelle
    c.execute("""
    CREATE TABLE IF NOT EXISTS spielen (
        spiel_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        beschreibung TEXT NOT NULL,
        startzeit    TEXT NOT NULL
    )""")

    # Tipps-Tabelle
    c.execute("""
    CREATE TABLE IF NOT EXISTS tipps (
        spiel_id   INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        username   TEXT    NOT NULL,
        tore_heim  INTEGER NOT NULL,
        tore_gast  INTEGER NOT NULL,
        PRIMARY KEY (spiel_id, user_id)
    )""")

    # Ergebnisse-Tabelle
    c.execute("""
    CREATE TABLE IF NOT EXISTS ergebnisse (
        spiel_id   INTEGER PRIMARY KEY,
        tore_heim  INTEGER NOT NULL,
        tore_gast  INTEGER NOT NULL
    )""")

    # Punkte-Tabelle
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


# Helfer: löscht Msg nach delay (mit RetryAfter-Handling)
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
        "/tippen            – Tipp abgeben (Dialog)\n"
        "/ergebnis         – Ergebnis eintragen (Admins)\n"
        "/loeschenspiel    – Spiel löschen (Admins)\n"
        "/rangliste        – Top-Tipper"
    )
    msg = await context.bot.send_message(chat_id=update.effective_chat.id, text=help_text)
    asyncio.create_task(auto_delete(msg, 8))
    try: await update.message.delete()
    except: pass


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
        err = await context.bot.send_message(
            chat_id=cmd.chat_id,
            text="📌 Nutze: /neuenspiel Beschreibung | YYYY-MM-DD HH:MM"
        )
        asyncio.create_task(auto_delete(err, 10))
        try: await cmd.delete()
        except: pass
        return

    besch, _, zeit = [p.strip() for p in text.partition("|")]
    try:
        dt = datetime.strptime(zeit, "%Y-%m-%d %H:%M")
    except ValueError:
        err = await context.bot.send_message(
            chat_id=cmd.chat_id,
            text="❌ Datum/Uhrzeit im Format YYYY-MM-DD HH:MM"
        )
        asyncio.create_task(auto_delete(err, 10))
        try: await cmd.delete()
        except: pass
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT 1 FROM spielen WHERE beschreibung=? AND startzeit=?",
        (besch, dt.isoformat())
    )
    if c.fetchone():
        dup = await context.bot.send_message(
            chat_id=cmd.chat_id,
            text="❌ Dieses Spiel wurde bereits angelegt."
        )
        asyncio.create_task(auto_delete(dup, 10))
        conn.close()
        try: await cmd.delete()
        except: pass
        return

    c.execute(
        "INSERT INTO spielen (beschreibung, startzeit) VALUES (?, ?)",
        (besch, dt.isoformat())
    )
    sid = c.lastrowid
    conn.commit()
    conn.close()

    reply = await context.bot.send_message(
        chat_id=cmd.chat_id,
        text=f"✅ Spiel {sid}: *{besch}* am {dt.strftime('%d.%m.%Y %H:%M')} angelegt.",
        parse_mode="Markdown"
    )
    asyncio.create_task(auto_delete(reply, 10))
    try: await cmd.delete()
    except: pass

    due = dt - timedelta(minutes=30)
    context.job_queue.run_once(
        send_reminder,
        when=due,
        chat_id=cmd.chat_id,
        name=str(sid),
        data={'id': sid, 'desc': besch, 'time': dt.strftime('%H:%M')}
    )


# Reminder-Callback
async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    d = context.job.data
    await context.bot.send_message(
        chat_id=context.job.chat_id,
        text=(
            f"⏰ Erinnerung: In 30 Min startet Spiel {d['id']}: "
            f"{d['desc']} um {d['time']} – tippt jetzt!"
        )
    )


# /spiele
async def spiele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT spiel_id, beschreibung, startzeit FROM spielen "
        "WHERE startzeit > ? ORDER BY startzeit",
        (now,)
    )
    rows = c.fetchall()
    conn.close()

    if not rows:
        msg = await context.bot.send_message(chat_id=cmd.chat_id, text="📌 Keine aktiven Spiele.")
        asyncio.create_task(auto_delete(msg,15))
    else:
        lines = ["📅 *Aktuelle Spiele:*",""]
        for sid, besch, start in rows:
            dt = datetime.fromisoformat(start)
            lines.append(f"• *ID {sid}* — _{besch}_")
            lines.append(f"   🗓️ {dt.strftime('%d.%m.%Y')}   ⏰ {dt.strftime('%H:%M')}")
            lines.append("")
        text = "\n".join(lines)
        msg = await context.bot.send_message(
            chat_id=cmd.chat_id, text=text, parse_mode="Markdown"
        )
        asyncio.create_task(auto_delete(msg,15))

    try: await cmd.delete()
    except: pass


# /tippen (Dialog)
async def start_tippen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message
    try: await cmd.delete()
    except: pass

    user = cmd.from_user.first_name
    uid  = cmd.from_user.id
    now  = datetime.now().isoformat()

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT spiel_id, beschreibung, startzeit FROM spielen "
        "WHERE startzeit > ? ORDER BY startzeit", (now,)
    )
    active = c.fetchall()
    c.execute("SELECT spiel_id FROM tipps WHERE user_id = ?", (uid,))
    tipped = {r[0] for r in c.fetchall()}
    conn.close()

    remaining = [(sid, desc, st) for sid, desc, st in active if sid not in tipped]
    if not remaining:
        notice = await context.bot.send_message(
            chat_id=cmd.chat_id,
            text=f"{user}, du hast schon auf alle aktiven Spiele getippt."
        )
        asyncio.create_task(auto_delete(notice,5))
        return ConversationHandler.END

    lines = [f"{user}, auf welches Spiel möchtest du tippen?",""]
    for sid, besch, start in remaining:
        dt = datetime.fromisoformat(start)
        lines.append(f"• *ID {sid}* — _{besch}_")
        lines.append(f"   🗓️ {dt.strftime('%d.%m.%Y')}   ⏰ {dt.strftime('%H:%M')}")
        lines.append("")
    prompt = "\n".join(lines)

    msg = await context.bot.send_message(
        chat_id=cmd.chat_id, text=prompt, parse_mode="Markdown"
    )
    context.user_data['prompt_msg'] = msg
    asyncio.create_task(auto_delete(msg,10))
    return CHOOSING_GAME


async def choose_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message
    user = user_msg.from_user.first_name

    if not user_msg.text.isdigit():
        err = await context.bot.send_message(
            chat_id=user_msg.chat_id,
            text=f"{user}, bitte gib eine *Zahl* als Spiel-ID ein.",
            parse_mode="Markdown"
        )
        asyncio.create_task(auto_delete(err,5))
        return CHOOSING_GAME

    old = context.user_data.pop('prompt_msg', None)
    if old:
        try: await old.delete()
        except: pass

    sid = int(user_msg.text)
    context.user_data['spiel_id'] = sid
    try: await user_msg.delete()
    except: pass

    prompt = await context.bot.send_message(
        chat_id=user_msg.chat_id,
        text=f"{user}, wie lautet dein Tipp? Format `2:1`"
    )
    context.user_data['prompt_msg'] = prompt
    asyncio.create_task(auto_delete(prompt,10))
    return TYPING_SCORE


async def receive_score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message
    user     = user_msg.from_user.first_name
    sid      = context.user_data['spiel_id']
    uid      = user_msg.from_user.id

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM tipps WHERE spiel_id = ? AND user_id = ?", (sid, uid))
    if c.fetchone():
        notice = await context.bot.send_message(
            chat_id=user_msg.chat_id, text=f"{user}, du hast bereits getippt."
        )
        asyncio.create_task(auto_delete(notice,5))
        try: await user_msg.delete()
        except: pass
        conn.close()
        return ConversationHandler.END

    c.execute("SELECT startzeit FROM spielen WHERE spiel_id = ?", (sid,))
    row = c.fetchone()
    conn.close()
    if not row or datetime.now() >= datetime.fromisoformat(row[0]):
        notice = await context.bot.send_message(
            chat_id=user_msg.chat_id, text=f"{user}, die Tippphase ist vorbei."
        )
        asyncio.create_task(auto_delete(notice,5))
        try: await user_msg.delete()
        except: pass
        return ConversationHandler.END

    old = context.user_data.pop('prompt_msg', None)
    if old:
        try: await old.delete()
        except: pass

    txt = user_msg.text.strip()
    if ":" not in txt or not all(p.isdigit() for p in txt.split(":",1)):
        err = await context.bot.send_message(
            chat_id=user_msg.chat_id, text=f"{user}, bitte im Format `2:1` tippen."
        )
        asyncio.create_task(auto_delete(err,5))
        return TYPING_SCORE

    heim, gast = map(int, txt.split(":",1))
    username  = user_msg.from_user.username or user

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO tipps (spiel_id,user_id,username,tore_heim,tore_gast) VALUES (?,?,?,?,?)",
        (sid, uid, username, heim, gast)
    )
    conn.commit()
    conn.close()

    thanks = await context.bot.send_message(
        chat_id=user_msg.chat_id,
        text=f"{user}, danke für deinen Tipp {heim}:{gast}! Viel Glück!"
    )
    asyncio.create_task(auto_delete(thanks,5))
    try: await user_msg.delete()
    except: pass
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message
    msg = await context.bot.send_message(chat_id=cmd.chat_id, text="Tippen abgebrochen.")
    asyncio.create_task(auto_delete(msg,5))
    try: await cmd.delete()
    except: pass
    return ConversationHandler.END


# /ergebnis <ID> <H:G> – Admin trägt Ergebnis ein und verteilt Punkte
async def ergebnis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message
    try:
        m = await context.bot.get_chat_member(cmd.chat_id, cmd.from_user.id)
        if m.status not in ("administrator", "creator"):
            await cmd.delete(); return
    except:
        await cmd.delete(); return

    parts = cmd.text.split(maxsplit=2)
    if len(parts) != 3 or ":" not in parts[2]:
        err = await context.bot.send_message(
            chat_id=cmd.chat_id,
            text="📌 Nutze: /ergebnis <Spiel-ID> <ToreHeim>:<ToreGast>"
        )
        asyncio.create_task(auto_delete(err,10))
        try: await cmd.delete()
        except: pass
        return

    sid_str, score = parts[1], parts[2]
    if not sid_str.isdigit():
        err = await context.bot.send_message(chat_id=cmd.chat_id, text="❌ Ungültige Spiel-ID.")
        asyncio.create_task(auto_delete(err,10))
        try: await cmd.delete()
        except: pass
        return

    sid = int(sid_str)
    home_str, away_str = score.split(":",1)
    if not (home_str.isdigit() and away_str.isdigit()):
        err = await context.bot.send_message(chat_id=cmd.chat_id, text="❌ Score im Format 2:1.")
        asyncio.create_task(auto_delete(err,10))
        try: await cmd.delete()
        except: pass
        return

    home, away = int(home_str), int(away_str)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM spielen WHERE spiel_id = ?", (sid,))
    if not c.fetchone():
        err = await context.bot.send_message(chat_id=cmd.chat_id, text="❌ Spiel nicht gefunden.")
        asyncio.create_task(auto_delete(err,10))
        conn.close()
        try: await cmd.delete()
        except: pass
        return

    c.execute(
        "INSERT OR REPLACE INTO ergebnisse (spiel_id,tore_heim,tore_gast) VALUES (?,?,?)",
        (sid, home, away)
    )

    c.execute(
        "SELECT user_id,username,tore_heim,tore_gast FROM tipps WHERE spiel_id = ?", (sid,)
    )
    tips = c.fetchall()
    for uid, username, heim_tip, gast_tip in tips:
        if heim_tip == home and gast_tip == away:
            pts = 3
        else:
            actual_diff = home - away
            tip_diff    = heim_tip - gast_tip
            if (actual_diff > 0 and tip_diff > 0) or \
               (actual_diff == 0 and tip_diff == 0) or \
               (actual_diff < 0 and tip_diff < 0):
                pts = 1
            else:
                pts = 0
        c.execute(
            "INSERT OR REPLACE INTO punkte (spiel_id,user_id,username,punkte) VALUES (?,?,?,?)",
            (sid, uid, username, pts)
        )

    conn.commit()
    conn.close()

    reply = await context.bot.send_message(
        chat_id=cmd.chat_id,
        text=f"✅ Ergebnis für Spiel {sid} gesetzt: {home}:{away}. Punkte verteilt!"
    )
    asyncio.create_task(auto_delete(reply,10))
    try: await cmd.delete()
    except: pass


# /loeschenspiel <ID> – Admin löscht Spiel + alle Einträge (robust)
async def loeschenspiel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message
    try:
        m = await context.bot.get_chat_member(cmd.chat_id, cmd.from_user.id)
        if m.status not in ("administrator", "creator"):
            await cmd.delete()
            return
    except:
        await cmd.delete()
        return

    parts = cmd.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        err = await context.bot.send_message(cmd.chat_id, "📌 Nutze: /loeschenspiel <Spiel-ID>")
        asyncio.create_task(auto_delete(err,10))
        await cmd.delete()
        return

    sid = int(parts[1])
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM spielen WHERE spiel_id = ?", (sid,))
    (exists,) = c.fetchone()
    if exists == 0:
        await context.bot.send_message(cmd.chat_id, "❌ Spiel nicht gefunden.")
        conn.close()
        await cmd.delete()
        return

    c.execute("DELETE FROM tipps     WHERE spiel_id = ?", (sid,))
    c.execute("DELETE FROM punkte    WHERE spiel_id = ?", (sid,))
    c.execute("DELETE FROM ergebnisse WHERE spiel_id = ?", (sid,))
    c.execute("DELETE FROM spielen   WHERE spiel_id = ?", (sid,))
    conn.commit()
    conn.close()

    reply = await context.bot.send_message(
        cmd.chat_id,
        f"🗑️ {exists} Spiel(e) mit ID {sid} und alle zugehörigen Einträge gelöscht."
    )
    asyncio.create_task(auto_delete(reply,10))
    await cmd.delete()


# /rangliste
async def rangliste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.message
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT username, SUM(punkte) as punkte
        FROM punkte
        GROUP BY username
        ORDER BY punkte DESC
        LIMIT 20
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        msg = await context.bot.send_message(chat_id=cmd.chat_id, text="Noch keine ausgewerteten Tipps.")
        asyncio.create_task(auto_delete(msg,20))
    else:
        lines = ["🏆 *Rangliste* 🏆",""]
        for i, (u, p) in enumerate(rows):
            lines.append(f"{i+1}. {u}: {p} Punkte")
        text = "\n".join(lines)
        msg = await context.bot.send_message(chat_id=cmd.chat_id, text=text, parse_mode="Markdown")
        asyncio.create_task(auto_delete(msg,30))

    try: await cmd.delete()
    except: pass


# Main
if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(os.environ["TOKEN"]).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("neuenspiel", neuenspiel))
    app.add_handler(CommandHandler("spiele", spiele))
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
    PORT        = int(os.environ.get("PORT", "8443"))
    app.run_webhook(
        listen     = "0.0.0.0",
        port       = PORT,
        url_path   = os.environ["TOKEN"],
        webhook_url= f"{WEBHOOK_URL}/{os.environ['TOKEN']}"
    )
