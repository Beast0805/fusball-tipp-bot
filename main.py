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
    # Tabelle für Spiele
    c.execute("""
    CREATE TABLE IF NOT EXISTS spielen (
        spiel_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        beschreibung TEXT NOT NULL,
        startzeit    TEXT NOT NULL,
        tore_heim    INTEGER,
        tore_gast    INTEGER
    )
    """)
    # Tabelle für Tipps
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
    # Tabelle für Streaks
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

    # Echtes Ergebnis abrufen
    c.execute("SELECT tore_heim, tore_gast FROM spielen WHERE spiel_id = ?", (spiel_id,))
    ergebnis = c.fetchone()
    if not ergebnis or ergebnis[0] is None:
        conn.close()
        return
    eh, eg = ergebnis

    # Alle Tipps für dieses Spiel laden
    c.execute("SELECT user_id, username, tore_heim, tore_gast FROM tipps WHERE spiel_id = ?", (spiel_id,))
    alle_tipps = c.fetchall()

    for user_id, username, th, tg in alle_tipps:
        if th == eh and tg == eg:
            base_punkte = 3
        elif (th - tg) * (eh - eg) > 0:
            base_punkte = 1
        else:
            base_punkte = 0

        # Streak abrufen
        c.execute("SELECT streak_count FROM streaks WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        old_streak = row[0] if row else 0

        if base_punkte > 0:
            new_streak = old_streak + 1
            multiplier = 2 if new_streak >= 3 else 1
            actual_punkte = base_punkte * multiplier
            if row:
                c.execute("UPDATE streaks SET streak_count = ? WHERE user_id = ?", (new_streak, user_id))
            else:
                c.execute("INSERT INTO streaks (user_id, streak_count) VALUES (?, ?)", (user_id, new_streak))
        else:
            new_streak = 0
            actual_punkte = 0
            if row:
                c.execute("UPDATE streaks SET streak_count = 0 WHERE user_id = ?", (user_id,))
            else:
                c.execute("INSERT INTO streaks (user_id, streak_count) VALUES (?, ?)", (user_id, 0))

        # Punkte aktualisieren
        c.execute("""
            UPDATE tipps SET punkte = ?
            WHERE spiel_id = ? AND user_id = ?
        """, (actual_punkte, spiel_id, user_id))

    conn.commit()
    conn.close()

# --- 4) Bot-Handler-Funktionen ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "Willkommen beim Fußball-Tipp-Bot!\n\n"
        "Befehle:\n"
        "/neuenspiel <Beschreibung> | <YYYY-MM-DD HH:MM> – als Admin ein neues Spiel anlegen\n"
        "/tippen <Spiel-ID> <ToreHeim>:<ToreGast> – Tipp abgeben (nur 1 Tipp pro Spiel, vor Startzeit)\n"
        "/ergebnis <Spiel-ID> <ToreHeim>:<ToreGast> – als Admin echtes Ergebnis eintragen\n"
        "/spiele – zeigt alle aktiven Spiele mit ID und Beschreibung (30 Sekunden sichtbar)\n"
        "/rangliste – zeige aktuelle Rangliste aller Tipper (30 Sekunden sichtbar)"
    )
    msg = await update.message.reply_text(txt)
    await asyncio.sleep(5)
    try: await msg.delete()
    except BadRequest: pass
    try: await update.message.delete()
    except BadRequest: pass

async def neuenspiel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-Befehl: Neues Spiel anlegen."""
    # Admin-Check
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if member.status not in ("administrator", "creator"):
            msg = await update.message.reply_text("❌ Nur Gruppen-Admins dürfen ein neues Spiel anlegen.")
            await asyncio.sleep(5)
            try: await msg.delete()
            except BadRequest: pass
            try: await update.message.delete()
            except BadRequest: pass
            return
    except Exception as e:
        msg = await update.message.reply_text(f"⚠️ Fehler beim Admin-Check: {e}")
        await asyncio.sleep(5)
        try: await msg.delete()
        except BadRequest: pass
        try: await update.message.delete()
        except BadRequest: pass
        return

    # Text zerlegen
    text = update.message.text.partition(" ")[2]
    if "|" not in text:
        msg = await update.message.reply_text(
            "📌 Falsches Format. Bitte so eingeben:\n"
            "`/neuenspiel <Beschreibung> | <YYYY-MM-DD HH:MM>`",
            parse_mode="Markdown"
        )
        await asyncio.sleep(5)
        try: await msg.delete()
        except BadRequest: pass
        try: await update.message.delete()
        except BadRequest: pass
        return

    beschreibung_part, _, zeit_part = text.partition("|")
    beschreibung = beschreibung_part.strip()
    startzeit_str = zeit_part.strip()

    # Format validieren
    try:
        startzeit = datetime.fromisoformat(startzeit_str)
    except ValueError:
        msg = await update.message.reply_text(
            "❌ Ungültiges Datum/Uhrzeit-Format. Beispiel:\n"
            "`/neuenspiel Norwegen vs Italien | 2025-06-06 20:45`",
            parse_mode="Markdown"
        )
        await asyncio.sleep(5)
        try: await msg.delete()
        except BadRequest: pass
        try: await update.message.delete()
        except BadRequest: pass
        return

    # Insert
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO spielen (beschreibung, startzeit) VALUES (?, ?)", (beschreibung, startzeit.isoformat()))
    conn.commit()
    spiel_id = c.lastrowid
    conn.close()

    await update.message.reply_text(
        f"✅ Neues Spiel angelegt: *{beschreibung}*\n"
        f"🆔 Spiel-ID: `{spiel_id}`\n"
        f"⏰ Startzeit: `{startzeit_str}`",
        parse_mode="Markdown"
    )
    try: await update.message.delete()
    except BadRequest: pass

async def spiele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Alle aktiven Spiele anzeigen (30 Sekunden)."""
    now_iso = datetime.now().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT spiel_id, beschreibung, startzeit FROM spielen WHERE startzeit > ? ORDER BY startzeit",
        (now_iso,)
    )
    rows = c.fetchall()
    conn.close()

    if not rows:
        msg = await update.message.reply_text("📌 Aktuell sind keine aktiven Spiele.")
        await asyncio.sleep(30)
        try: await msg.delete()
        except BadRequest: pass
        try: await update.message.delete()
        except BadRequest: pass
        return

    text = "📅 **Aktuelle Spiele:**\n\n"
    for spiel_id, beschreibung, startzeit_iso in rows:
        start = datetime.fromisoformat(startzeit_iso)
        text += f"ID {spiel_id}: {beschreibung} (Start: {start.strftime('%Y-%m-%d %H:%M')})\n"
    msg = await update.message.reply_text(text, parse_mode="Markdown")
    await asyncio.sleep(30)                # ← hier 30 Sekunden
    try: await msg.delete()
    except BadRequest: pass
    try: await update.message.delete()
    except BadRequest: pass

# (tippen, ergebnis, rangliste bleiben unverändert, rangliste hat bereits 30 Sekunden Sleep)

if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(os.environ.get('TOKEN')).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("neuenspiel", neuenspiel))   # Filter entfernt
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
