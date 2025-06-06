import os
import sqlite3
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- 1) Token und Port aus ENV lesen ---
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise RuntimeError("ENV VAR 'TOKEN' fehlt!")

PORT = int(os.environ.get("PORT", "8443"))

# --- 2) Pfad zur SQLite-Datenbank ---
DB_PATH = "database.db"

# --- 3) Datenbank initialisieren (mit Startzeit) ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Tabelle für Spiele mit 'startzeit' als ISO-String
    c.execute("""
    CREATE TABLE IF NOT EXISTS spielen (
        spiel_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        beschreibung TEXT NOT NULL,
        startzeit    TEXT NOT NULL,
        tore_heim    INTEGER,
        tore_gast    INTEGER
    )
    """)
    # Tabelle für Tipps (ein Tipp pro Spiel & User)
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

# --- 4) Punkteberechnung mit Streak-Logik (Multiplier capped at 2) ---
def berechne_punkte(spiel_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Echtes Ergebnis abrufen
    c.execute("SELECT tore_heim, tore_gast FROM spielen WHERE spiel_id = ?", (spiel_id,))
    ergebnis = c.fetchone()
    if not ergebnis or ergebnis[0] is None:
        conn.close()
        return  # kein Ergebnis eingetragen
    eh, eg = ergebnis

    # Für jeden Tipp Punkte berechnen und Streaks updaten
    c.execute("SELECT user_id, username, tore_heim, tore_gast FROM tipps WHERE spiel_id = ?", (spiel_id,))
    alle_tipps = c.fetchall()
    for user_id, username, th, tg in alle_tipps:
        # Basis-Punkte (3/1/0)
        if th == eh and tg == eg:
            base_punkte = 3
        elif (th - tg) * (eh - eg) > 0:
            base_punkte = 1
        else:
            base_punkte = 0

        # Aktuellen Streak abrufen
        c.execute("SELECT streak_count FROM streaks WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        old_streak = row[0] if row else 0

        if base_punkte > 0:
            new_streak = old_streak + 1
            # Multiplier = 2 ab dem 3. korrekten Tipp, bleibt bei 2 auch bei größerem Streak
            multiplier = 2 if new_streak >= 3 else 1
            actual_punkte = base_punkte * multiplier
            # Streak aktualisieren
            if row:
                c.execute("UPDATE streaks SET streak_count = ? WHERE user_id = ?", (new_streak, user_id))
            else:
                c.execute("INSERT INTO streaks (user_id, streak_count) VALUES (?, ?)", (user_id, new_streak))
        else:
            new_streak = 0
            actual_punkte = 0
            # Streak zurücksetzen
            if row:
                c.execute("UPDATE streaks SET streak_count = 0 WHERE user_id = ?", (user_id,))
            else:
                c.execute("INSERT INTO streaks (user_id, streak_count) VALUES (?, ?)", (user_id, 0))

        # Punkte in der Tabelle speichern
        c.execute("""
            UPDATE tipps SET punkte = ?
            WHERE spiel_id = ? AND user_id = ?
        """, (actual_punkte, spiel_id, user_id))

    conn.commit()
    conn.close()

# --- 5) Bot-Handler-Funktionen ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "Willkommen beim Fußball-Tipp-Bot!\n\n"
        "Befehle:\n"
        "/neuenspiel <Beschreibung> | <YYYY-MM-DD HH:MM> – als Admin ein neues Spiel anlegen\n"
        "/tippen <Spiel-ID> <ToreHeim>:<ToreGast> – Tipp abgeben (nur 1 Tipp pro Spiel, vor Startzeit)\n"
        "/ergebnis <Spiel-ID> <ToreHeim>:<ToreGast> – als Admin echtes Ergebnis eintragen\n"
        "/spiele – zeigt alle aktiven Spiele mit ID und Beschreibung\n"
        "/rangliste – zeige aktuelle Rangliste aller Tipper"
    )
    await update.message.reply_text(txt)
    await asyncio.sleep(5)
    await update.message.delete()

async def neuenspiel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-Befehl: Ein neues Spiel anlegen mit Startzeit."""
    try:
        chat_admin = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if chat_admin.status not in ("administrator", "creator"):
            await update.message.reply_text("❌ Nur Gruppen-Admins dürfen ein neues Spiel anlegen.")
            await asyncio.sleep(5)
            await update.message.delete()
            return
    except Exception as e:
        await update.message.reply_text(f"⚠️ Fehler beim Admin-Check: {e}")
        await asyncio.sleep(5)
        await update.message.delete()
        return

    # Erwartetes Format: Beschreibung | YYYY-MM-DD HH:MM
    text = update.message.text.partition(" ")[2]
    if "|" not in text:
        await update.message.reply_text(
            "📌 Falsches Format. Bitte so eingeben:\n"
            "`/neuenspiel <Beschreibung> | <YYYY-MM-DD HH:MM>`",
            parse_mode="Markdown"
        )
        await asyncio.sleep(5)
        await update.message.delete()
        return

    beschreibung_part, _, zeit_part = text.partition("|")
    beschreibung = beschreibung_part.strip()
    startzeit_str = zeit_part.strip()

    # Startzeit validieren
    try:
        startzeit = datetime.fromisoformat(startzeit_str)
    except ValueError:
        await update.message.reply_text(
            "❌ Ungültiges Datum/Uhrzeit-Format. Beispiel:\n"
            "`/neuenspiel Norwegen vs Italien | 2025-06-06 20:45`",
            parse_mode="Markdown"
        )
        await asyncio.sleep(5)
        await update.message.delete()
        return

    # In DB speichern
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
    await asyncio.sleep(5)
    await update.message.delete()

async def spiele(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeigt alle aktiven Spiele mit ID und Beschreibung."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT spiel_id, beschreibung, startzeit FROM spielen")
    rows = c.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📌 Aktuell sind keine Spiele angelegt.")
        await asyncio.sleep(5)
        await update.message.delete()
        return

    text = "📅 **Aktuelle Spiele:**\n\n"
    for spiel_id, beschreibung, startzeit_iso in rows:
        # Optional: nur zukünftige Spiele anzeigen
        startzeit = datetime.fromisoformat(startzeit_iso)
        text += f"ID {spiel_id}: {beschreibung} (Start: {startzeit.strftime('%Y-%m-%d %H:%M')})\n"
    await update.message.reply_text(text, parse_mode="Markdown")
    await asyncio.sleep(5)
    await update.message.delete()

async def tippen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User-Befehl: Normales Tipp-Ergebnis abgeben (nur 1 Tipp pro Spiel, vor Startzeit)."""
    if len(context.args) != 2 or ":" not in context.args[1]:
        await update.message.reply_text("Usage: /tippen <Spiel-ID> <ToreHeim>:<ToreGast>")
        await asyncio.sleep(5)
        await update.message.delete()
        return

    try:
        spiel_id = int(context.args[0])
        th, tg = map(int, context.args[1].split(":"))
    except ValueError:
        await update.message.reply_text("Ungültiges Format. Beispiel: /tippen 1 2:1")
        await asyncio.sleep(5)
        await update.message.delete()
        return

    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Prüfen, ob das Spiel existiert und Startzeit abrufen
    c.execute("SELECT beschreibung, startzeit FROM spielen WHERE spiel_id = ?", (spiel_id,))
    row = c.fetchone()
    if not row:
        # Ungültige Spiel-ID → Liste der aktiven Spiele anzeigen
        c.execute("SELECT spiel_id, beschreibung, startzeit FROM spielen")
        alle_spiele = c.fetchall()
        conn.close()
        if not alle_spiele:
            await update.message.reply_text("❌ Spiel-ID ungültig. Es sind aktuell keine Spiele angelegt.")
        else:
            text = "❌ Spiel-ID ungültig. Aktuelle Spiele:\n"
            for sid, beschr, start_iso in alle_spiele:
                start_dt = datetime.fromisoformat(start_iso)
                text += f"ID {sid}: {beschr} (Start: {start_dt.strftime('%Y-%m-%d %H:%M')})\n"
            await update.message.reply_text(text)
        await asyncio.sleep(5)
        await update.message.delete()
        return

    beschreibung, startzeit_iso = row
    startzeit = datetime.fromisoformat(startzeit_iso)

    # Prüfen, ob Spiel bereits gestartet ist
    if datetime.now() >= startzeit:
        conn.close()
        await update.message.reply_text("⏰ Das Spiel ist bereits gestartet – Tipps sind nicht mehr möglich.")
        await asyncio.sleep(5)
        await update.message.delete()
        return

    # Prüfen, ob der User bereits getippt hat
    c.execute("SELECT 1 FROM tipps WHERE spiel_id = ? AND user_id = ?", (spiel_id, user_id))
    if c.fetchone():
        conn.close()
        await update.message.reply_text(
            f"⚠️ Du hast für Spiel {spiel_id} bereits einen Tipp abgegeben. Änderungen sind nicht möglich."
        )
        await asyncio.sleep(5)
        await update.message.delete()
        return

    # Tipp speichern
    c.execute("""
        INSERT INTO tipps (spiel_id, user_id, username, tore_heim, tore_gast)
        VALUES (?, ?, ?, ?, ?)
    """, (spiel_id, user_id, username, th, tg))
    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"{username}, dein Tipp für Spiel {spiel_id} wurde gespeichert: {th}:{tg}. Viel Glück!"
    )
    await asyncio.sleep(5)
    await update.message.delete()

async def ergebnis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-Befehl: Echtes Ergebnis eintragen und Punkte berechnen."""
    try:
        chat_admin = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        if chat_admin.status not in ("administrator", "creator"):
            await update.message.reply_text("❌ Nur Gruppen-Admins dürfen das Ergebnis eintragen.")
            await asyncio.sleep(5)
            await update.message.delete()
            return
    except Exception as e:
        await update.message.reply_text(f"⚠️ Fehler beim Admin-Check: {e}")
        await asyncio.sleep(5)
        await update.message.delete()
        return

    if len(context.args) != 2 or ":" not in context.args[1]:
        await update.message.reply_text(
            "📌 Bitte korrekt eingeben:\n`/ergebnis <Spiel-ID> <ToreHeim>:<ToreGast>`",
            parse_mode="Markdown"
        )
        await asyncio.sleep(5)
        await update.message.delete()
        return

    try:
        spiel_id = int(context.args[0])
        eh, eg = map(int, context.args[1].split(":"))
    except ValueError:
        await update.message.reply_text("Ungültiges Format. Beispiel: `/ergebnis 1 2:1`", parse_mode="Markdown")
        await asyncio.sleep(5)
        await update.message.delete()
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM spielen WHERE spiel_id = ?", (spiel_id,))
    if not c.fetchone():
        conn.close()
        await update.message.reply_text(f"❌ Spiel mit ID {spiel_id} existiert nicht.")
        await asyncio.sleep(5)
        await update.message.delete()
        return

    c.execute("""
        UPDATE spielen SET tore_heim = ?, tore_gast = ?
        WHERE spiel_id = ?
    """, (eh, eg, spiel_id))
    conn.commit()
    conn.close()

    berechne_punkte(spiel_id)
    await update.message.reply_text(
        f"✅ Ergebnis für Spiel {spiel_id} gesetzt: {eh}:{eg} – Punkte berechnet."
    )
    await asyncio.sleep(5)
    await update.message.delete()

async def rangliste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeigt Rangliste aller Tipper (inkl. Streak-Punkte)."""
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
        await update.message.reply_text("Noch keine Tipps bzw. keine Punkte.")
        await asyncio.sleep(5)
        await update.message.delete()
        return

    text = "🏆 Rangliste 🏆\n\n"
    for idx, (user, pts) in enumerate(rows, start=1):
        text += f"{idx}. {user}: {pts} Punkte\n"
    await update.message.reply_text(text)
    await asyncio.sleep(5)
    await update.message.delete()

# --- 6) Bot-Einrichtung & Webhook starten ---
if __name__ == "__main__":
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    # CommandHandler registrieren
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("neuenspiel", neuenspiel))
    app.add_handler(CommandHandler("spiele", spiele))
    app.add_handler(CommandHandler("tippen", tippen))
    app.add_handler(CommandHandler("ergebnis", ergebnis))
    app.add_handler(CommandHandler("rangliste", rangliste))

    # Webhook-URL aus ENV lesen
    WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL")
    if not WEBHOOK_URL:
        raise RuntimeError("ENV VAR 'RENDER_EXTERNAL_URL' fehlt!")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
    )
