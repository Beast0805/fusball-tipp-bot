import os
import sqlite3
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- 1) Token und Port aus ENV lesen ---
TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise RuntimeError("ENV VAR 'TOKEN' fehlt!")

PORT = int(os.environ.get("PORT", "8443"))

# --- 2) Datenbank initialisieren ---
DB_PATH = "database.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Tabelle für Spiele
    c.execute("""
    CREATE TABLE IF NOT EXISTS spielen (
        spiel_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        beschreibung TEXT NOT NULL,
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
    conn.commit()
    conn.close()

# --- 3) Helfer-Funktionen: Punkte berechnen ---
def berechne_punkte(spiel_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Hole das echte Ergebnis
    c.execute("SELECT tore_heim, tore_gast FROM spielen WHERE spiel_id = ?", (spiel_id,))
    ergebnis = c.fetchone()
    if not ergebnis or ergebnis[0] is None:
        conn.close()
        return  # kein Ergebnis eingetragen
    eh, eg = ergebnis

    # Für jeden Tipp desselben Spiels Punkte setzen
    c.execute("SELECT user_id, tore_heim, tore_gast FROM tipps WHERE spiel_id = ?", (spiel_id,))
    alle_tipps = c.fetchall()
    for user_id, th, tg in alle_tipps:
        # exaktes Ergebnis?
        if th == eh and tg == eg:
            punkte = 3
        # richtige Tendenz?
        elif (th - tg) * (eh - eg) > 0 or (th == tg == eh == eg):
            # (Sieg Heim vs Sieg Heim) oder (Unentschieden erkannt)
            punkte = 1
        else:
            punkte = 0
        c.execute("""
            UPDATE tipps SET punkte = ? 
            WHERE spiel_id = ? AND user_id = ?
        """, (punkte, spiel_id, user_id))
    conn.commit()
    conn.close()

# --- 4) Bot-Handler --- 
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "Willkommen beim Fußball-Tipp-Bot!\n\n"
        "Befehle:\n"
        "/neuenspiel <Beschreibung> – als Admin ein neues Spiel anlegen\n"
        "/tippen <Spiel-ID> <ToreHeim>:<ToreGast> – Tipp abgeben\n"
        "/ergebnis <Spiel-ID> <ToreHeim>:<ToreGast> – als Admin echtes Ergebnis eintragen\n"
        "/rangliste – zeige aktuelle Rangliste aller Tipper"
    )
    await update.message.reply_text(txt)

async def neuenspiel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-Befehl: Ein neues Spiel anlegen."""
    # Nur Chats, in denen Bot Admin ist (oder eine bestimmte User-ID), dürfen das
    mitglied = update.effective_chat.get_member(update.effective_user.id)
    if not mitglied.status in ("administrator", "creator"):
        await update.message.reply_text("Nur Admins dürfen neue Spiele anlegen.")
        return

    if len(context.args) < 1:
        await update.message.reply_text("Usage: /neuenspiel <Beschreibung>")
        return

    beschr = " ".join(context.args)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO spielen (beschreibung) VALUES (?)", (beschr,))
    conn.commit()
    spiel_id = c.lastrowid
    conn.close()
    await update.message.reply_text(f"Spiel angelegt mit ID {spiel_id}: {beschr}")

async def tippen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User-Befehl: Tipp abgeben."""
    if len(context.args) != 2 or ":" not in context.args[1]:
        await update.message.reply_text("Usage: /tippen <Spiel-ID> <ToreHeim>:<ToreGast>")
        return

    try:
        spiel_id = int(context.args[0])
        tore_str = context.args[1]
        th, tg = map(int, tore_str.split(":"))
    except ValueError:
        await update.message.reply_text("Ungültiges Format. Beispiel: /tippen 1 2:1")
        return

    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Existiert das Spiel?
    c.execute("SELECT 1 FROM spielen WHERE spiel_id = ?", (spiel_id,))
    if not c.fetchone():
        conn.close()
        await update.message.reply_text(f"Spiel mit ID {spiel_id} existiert nicht.")
        return

    # Tipp speichern oder updaten
    c.execute("""
        INSERT INTO tipps (spiel_id, user_id, username, tore_heim, tore_gast)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(spiel_id, user_id) DO UPDATE SET
          tore_heim = excluded.tore_heim,
          tore_gast = excluded.tore_gast
    """, (spiel_id, user_id, username, th, tg))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"{username}, dein Tipp für Spiel {spiel_id} wurde gespeichert: {th}:{tg}")

async def ergebnis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-Befehl: Echtes Ergebnis eintragen und Punkte berechnen."""
    # Nur Admins dürfen Ergebnis setzen
    mitglied = update.effective_chat.get_member(update.effective_user.id)
    if not mitglied.status in ("administrator", "creator"):
        await update.message.reply_text("Nur Admins dürfen das Ergebnis eintragen.")
        return

    if len(context.args) != 2 or ":" not in context.args[1]:
        await update.message.reply_text("Usage: /ergebnis <Spiel-ID> <ToreHeim>:<ToreGast>")
        return

    try:
        spiel_id = int(context.args[0])
        tore_str = context.args[1]
        eh, eg = map(int, tore_str.split(":"))
    except ValueError:
        await update.message.reply_text("Ungültiges Format. Beispiel: /ergebnis 1 2:1")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Existiert das Spiel?
    c.execute("SELECT 1 FROM spielen WHERE spiel_id = ?", (spiel_id,))
    if not c.fetchone():
        conn.close()
        await update.message.reply_text(f"Spiel mit ID {spiel_id} existiert nicht.")
        return

    # Ergebnis updaten
    c.execute("""
        UPDATE spielen SET tore_heim = ?, tore_gast = ?
        WHERE spiel_id = ?
    """, (eh, eg, spiel_id))
    conn.commit()
    conn.close()

    # Punkte berechnen
    berechne_punkte(spiel_id)
    await update.message.reply_text(f"Ergebnis für Spiel {spiel_id} gesetzt: {eh}:{eg} – Punkte berechnet.")

async def rangliste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Zeigt Rangliste aller Tipper über alle Spiele an."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Summiere Punkte je Nutzer über alle Spiele
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
        return

    text = "🏆 Rangliste 🏆\n\n"
    for idx, (user, pts) in enumerate(rows, start=1):
        text += f"{idx}. {user}: {pts} Punkte\n"
    await update.message.reply_text(text)

# --- 5) Bot-Einrichtung & Webhook starten ---
if __name__ == "__main__":
    init_db()  # Datenbank und Tabellen erstellen
    app = ApplicationBuilder().token(TOKEN).build()

    # Registere alle CommandHandler
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("neuenspiel", neuenspiel))
    app.add_handler(CommandHandler("tippen", tippen))
    app.add_handler(CommandHandler("ergebnis", ergebnis))
    app.add_handler(CommandHandler("rangliste", rangliste))

    # Webhook-URL aus ENV holen
    WEBHOOK_URL = os.environ.get("RENDER_EXTERNAL_URL")
    if not WEBHOOK_URL:
        raise RuntimeError("ENV VAR 'RENDER_EXTERNAL_URL' fehlt!")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TOKEN}"
    )
