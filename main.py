import os
import sqlite3
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.error import BadRequest

# --- 1) Persistenten Ordner anlegen und DB-Pfad setzen ---
os.makedirs("/data", exist_ok=True)
DB_PATH = "/data/database.db"

# --- 2) Datenbank initialisieren ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS spielen (
        spiel_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        beschreibung TEXT NOT NULL,
        startzeit    TEXT NOT NULL,
        tore_heim    INTEGER,
        tore_gast    INTEGER
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS tipps (
        spiel_id   INTEGER NOT NULL,
        user_id    INTEGER NOT NULL,
        username   TEXT NOT NULL,
        tore_heim  INTEGER NOT NULL,
        tore_gast  INTEGER NOT NULL,
        punkte     INTEGER DEFAULT 0,
        PRIMARY KEY (spiel_id, user_id)
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS streaks (
        user_id      INTEGER PRIMARY KEY,
        streak_count INTEGER NOT NULL
    )
    """)
    conn.commit()
    conn.close()

# --- 3) Punkteberechnung mit Streak-Logik ---
def berechne_punkte(spiel_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT tore_heim, tore_gast FROM spielen WHERE spiel_id = ?", (spiel_id,))
    ergebnis = c.fetchone()
    if not ergebnis or ergebnis[0] is None:
        conn.close()
        return
    eh, eg = ergebnis

    c.execute("SELECT user_id, username, tore_heim, tore_gast FROM tipps WHERE spiel_id = ?", (spiel_id,))
    alle_tipps = c.fetchall()

    for user_id, username, th, tg in alle_tipps:
        if th == eh and tg == eg:
            base = 3
        elif (th - tg) * (eh - eg) > 0:
            base = 1
        else:
            base = 0

        c.execute("SELECT streak_count FROM streaks WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        old = row[0] if row else 0

        if base > 0:
            new = old + 1
            mult = 2 if new >= 3 else 1
            pts = base * mult
            if row:
                c.execute("UPDATE streaks SET streak_count = ? WHERE user_id = ?", (new, user_id))
            else:
                c.execute("INSERT INTO streaks (user_id, streak_count) VALUES (?, ?)", (user_id, new))
        else:
            new = 0
            pts = 0
            if row:
                c.execute("UPDATE streaks SET streak_count = 0 WHERE user_id = ?", (user_id,))
            else:
                c.execute("INSERT INTO streaks (user_id, streak_count) VALUES (?, ?)", (user_id, 0))

        c.execute(
            "UPDATE tipps SET punkte = ? WHERE spiel_id = ? AND user_id = ?",
            (pts, spiel_id, user_id)
        )

    conn.commit()
    conn.close()

# --- 4) Bot-Handler-Funktionen ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "Willkommen beim Fußball-Tipp-Bot!\n\n"
        "Befehle:\n"
        "/neuenspiel <Beschreibung> | <YYYY-MM-DD HH:MM> – als Admin ein neues Spiel anlegen\n"
        "/tippen <Spiel-ID> <ToreHeim>:<ToreGast> – Tipp abgeben (vor Spielbeginn)\n"
        "/ergebnis <Spiel-ID> <ToreHeim>:<ToreGast> – als Admin echtes Ergebnis eintragen\n"
        "/spiele – zeigt alle aktiven Spiele (30 Sekunden sichtbar)\n"
        "/rangliste – zeigt die Top 20 Tipper (30 Sekunden sichtbar)"
    )
    msg = await update.message.reply_text(txt)
    await asyncio.sleep(5)
    try: await msg.delete()
    except BadRequest: pass
    try: await update.message.delete()
    except BadRequest: pass

async def neuenspiel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: neues Spiel anlegen"""
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if member.status not in ("administrator", "creator"):
            msg = await update.message.reply_text("❌ Nur Admins dürfen neue Spiele anlegen.")
            await asyncio.sleep(5)
            try: await msg.delete()
            except BadRequest: pass
            try: await update.message.delete()
            except BadRequest: pass
            return
    except Exception as e:
        msg = await update.message.reply_text(f"⚠️ Admin-Check fehlgeschlagen: {e}")
        await asyncio.sleep(5)
        try: await msg.delete()
        except BadRequest: pass
        try: await update.message.delete()
        except BadRequest: pass
        return

    text = update.message.text.partition(" ")[2]
    if "|" not in text:
        msg = await update.message.reply_text(
            "`/neuenspiel <Beschreibung> | <YYYY-MM-DD HH:MM>`",
            parse_mode="Markdown"
        )
        await asyncio.sleep(5)
        try: await msg.delete()
        except BadRequest: pass
        try: await update.message.delete()
        except BadRequest: pass
        return

    besch, _, zeit = text.partition("|")
    besch = besch.strip()
    zeit = zeit.strip()

    try:
        dt = datetime.fromisoformat(zeit)
    except ValueError:
        msg = await update.message.reply_text("❌ Nutze `YYYY-MM-DD HH:MM`", parse_mode="Markdown")
        await asyncio.sleep(5)
        try: await msg.delete()
        except BadRequest: pass
        try: await update.message.delete()
        except BadRequest: pass
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO spielen (beschreibung, startzeit) VALUES (?, ?)", (besch, dt.isoformat()))
    conn.commit()
    sid = c.lastrowid
    conn.close()

    await update.message.reply_text(
        f"✅ Spiel {sid}: *{besch}* am `{zeit}` angelegt.",
        parse_mode="Markdown"
    )
    try: await update.message.delete()
    except BadRequest: pass

async def spiele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeigt nur zukünftige Spiele (30 Sekunden sichtbar)."""
    now = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT spiel_id, beschreibung, startzeit FROM spielen WHERE startzeit > ? ORDER BY startzeit",
        (now,)
    )
    rows = c.fetchall()
    conn.close()

    if not rows:
        msg = await update.message.reply_text("📌 Keine aktiven Spiele.")
        await asyncio.sleep(30)
        try: await msg.delete()
        except BadRequest: pass
        try: await update.message.delete()
        except BadRequest: pass
        return

    text = "📅 **Aktuelle Spiele:**\n"
    for sid, besch, start in rows:
        dt = datetime.fromisoformat(start)
        text += f"ID {sid}: {besch} (Start {dt.strftime('%Y-%m-%d %H:%M')})\n"
    msg = await update.message.reply_text(text, parse_mode="Markdown")
    await asyncio.sleep(30)
    try: await msg.delete()
    except BadRequest: pass
    try: await update.message.delete()
    except BadRequest: pass

async def tippen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tippt ein Ergebnis."""
    if len(context.args) != 2 or ":" not in context.args[1]:
        msg = await update.message.reply_text("Usage: /tippen <Spiel-ID> <ToreHeim>:<ToreGast>")
        await asyncio.sleep(5)
        try: await msg.delete()
        except BadRequest: pass
        try: await update.message.delete()
        except BadRequest: pass
        return

    try:
        sid = int(context.args[0])
        th, tg = map(int, context.args[1].split(":"))
    except:
        msg = await update.message.reply_text("❌ Falsches Format. Beispiel: /tippen 1 2:1")
        await asyncio.sleep(5)
        try: await msg.delete()
        except BadRequest: pass
        try: await update.message.delete()
        except BadRequest: pass
        return

    user, uid = update.effective_user.username or update.effective_user.first_name, update.effective_user.id
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT startzeit FROM spielen WHERE spiel_id = ?", (sid,))
    row = c.fetchone()
    if not row:
        conn.close()
        await update.message.reply_text("❌ Spiel existiert nicht.")
        return

    start = datetime.fromisoformat(row[0])
    if datetime.now() >= start:
        conn.close()
        await update.message.reply_text("⏰ Tippphase vorbei.")
        return

    c.execute("SELECT 1 FROM tipps WHERE spiel_id = ? AND user_id = ?", (sid, uid))
    if c.fetchone():
        conn.close()
        await update.message.reply_text("⚠️ Du hast bereits getippt.")
        return

    c.execute(
        "INSERT INTO tipps (spiel_id, user_id, username, tore_heim, tore_gast) VALUES (?,?,?,?,?)",
        (sid, uid, user, th, tg)
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Dein Tipp für Spiel {sid}: {th}:{tg}")
    try: await update.message.delete()
    except BadRequest: pass

async def ergebnis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin trägt echt Ergebnis ein."""
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if member.status not in ("administrator", "creator"):
            await update.message.reply_text("❌ Nur Admins dürfen das Ergebnis setzen.")
            return
    except:
        await update.message.reply_text("⚠️ Admin-Check fehlgeschlagen.")
        return

    if len(context.args) != 2 or ":" not in context.args[1]:
        await update.message.reply_text("Usage: /ergebnis <Spiel-ID> <Heim>:<Gast>")
        return

    try:
        sid = int(context.args[0])
        eh, eg = map(int, context.args[1].split(":"))
    except:
        await update.message.reply_text("❌ Falsches Format. Beispiel: /ergebnis 1 2:1")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE spielen SET tore_heim=?, tore_gast=? WHERE spiel_id=?", (eh, eg, sid))
    conn.commit()
    conn.close()

    berechne_punkte(sid)
    await update.message.reply_text(f"✅ Ergebnis Spiel {sid}: {eh}:{eg} – Punkte berechnet.")

async def rangliste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeigt Top 20 Tipper (30 Sekunden)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT username, SUM(punkte) as sum_punkte
        FROM tipps
        GROUP BY username
        ORDER BY sum_punkte DESC
        LIMIT 20
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        msg = await update.message.reply_text("Noch keine Tipps.")
        await asyncio.sleep(30)
        try: await msg.delete()
        except BadRequest: pass
        try: await update.message.delete()
        except BadRequest: pass
        return

    text = "🏆 Rangliste 🏆\n"
    for i, (user, pts) in enumerate(rows, start=1):
        text += f"{i}. {user}: {pts}\n"
    msg = await update.message.reply_text(text)
    await asyncio.sleep(30)
    try: await msg.delete()
    except BadRequest: pass
    try: await update.message.delete()
    except BadRequest: pass

# --- 5) Bot starten ---
if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(os.environ.get('TOKEN')).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("neuenspiel", neuenspiel))
    app.add_handler(CommandHandler("spiele", spiele))
    app.add_handler(CommandHandler("tippen", tippen))
    app.add_handler(CommandHandler("ergebnis", ergebnis))
    app.add_handler(CommandHandler("rangliste", rangliste))

    WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL")
    if not WEBHOOK_URL:
        raise RuntimeError("ENV VAR 'RENDER_EXTERNAL_URL' fehlt!")

    PORT = int(os.environ.get("PORT", "8443"))
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=os.environ.get('TOKEN'),
        webhook_url=f"{WEBHOOK_URL}/{os.environ.get('TOKEN')}"
    )
