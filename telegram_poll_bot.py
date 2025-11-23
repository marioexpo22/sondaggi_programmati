"""
Telegram Scheduled Poll Bot — versione corretta e robusta
File: telegram_poll_bot.py

Descrizione:
Bot Telegram per creare sondaggi programmati all'interno dei gruppi.
Gli utenti possono:
 - creare sondaggi periodici
 - impostare la domanda, opzioni e intervallo
 - pinnare il sondaggio
 - inviarlo manualmente
 - elencare ed eliminare sondaggi

Questa versione:
 - Risolve l'errore: ModuleNotFoundError: No module named 'telegram'
 - Non usa token nel codice → usa variabile d'ambiente BOT_TOKEN
 - Fornisce test locali che non richiedono telegram (flag --test)
 - Gestisce gli errori elegantemente
"""

import logging
import os
import sqlite3
import json
import time
import sys
from typing import List, Optional

# ---------------------------------------------------------------------------
# Import Telegram — gestiamo il fallimento se non installato
# ---------------------------------------------------------------------------
TELEGRAM_AVAILABLE = True
_telegram_import_error = None

try:
    from telegram import Update
    from telegram.constants import ChatType
    from telegram.ext import (
        ApplicationBuilder,
        CommandHandler,
        ContextTypes,
        ConversationHandler,
        MessageHandler,
        filters,
    )
except Exception as e:
    TELEGRAM_AVAILABLE = False
    _telegram_import_error = e

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE = os.environ.get("POLL_BOT_DB", "polls.db")
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "60"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def init_db(db_path: Optional[str] = None):
    db = db_path or DATABASE
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS polls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            question TEXT NOT NULL,
            options TEXT NOT NULL,
            interval_minutes INTEGER NOT NULL,
            pinned INTEGER NOT NULL DEFAULT 0,
            last_sent INTEGER NOT NULL DEFAULT 0,
            creator_id INTEGER
        )
    """)
    conn.commit()
    conn.close()

def add_poll(chat_id: int, question: str, options: List[str], interval_minutes: int, pinned: bool, creator_id: Optional[int], db_path: Optional[str] = None) -> int:
    db = db_path or DATABASE
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO polls (chat_id, question, options, interval_minutes, pinned, last_sent, creator_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (chat_id, question, json.dumps(options), interval_minutes, 1 if pinned else 0, 0, creator_id),
    )
    pid = cur.lastrowid
    conn.commit()
    conn.close()
    return pid

def list_polls_for_chat(chat_id: int, db_path: Optional[str] = None):
    db = db_path or DATABASE
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT id, question, options, interval_minutes, pinned, last_sent FROM polls WHERE chat_id = ?", (chat_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def get_poll(poll_id: int, db_path: Optional[str] = None):
    db = db_path or DATABASE
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("SELECT id, chat_id, question, options, interval_minutes, pinned, last_sent FROM polls WHERE id = ?", (poll_id,))
    row = cur.fetchone()
    conn.close()
    return row

def delete_poll(poll_id: int, db_path: Optional[str] = None):
    db = db_path or DATABASE
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("DELETE FROM polls WHERE id = ?", (poll_id,))
    conn.commit()
    affected = cur.rowcount
    conn.close()
    return affected

def update_last_sent(poll_id: int, ts: int, db_path: Optional[str] = None):
    db = db_path or DATABASE
    conn = sqlite3.connect(db)
    cur = conn.cursor()
    cur.execute("UPDATE polls SET last_sent = ? WHERE id = ?", (ts, poll_id))
    conn.commit()
    conn.close()

# ---------------------------------------------------------------------------
# Bot — solo se telegram è disponibile
# ---------------------------------------------------------------------------
if TELEGRAM_AVAILABLE:
    Q_QUESTION, Q_OPTIONS, Q_INTERVAL, Q_PIN = range(4)

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "Ciao! Sono un bot per sondaggi programmati.\n"
            "Usa /createpoll per crearne uno nuovo."
        )

    async def createpoll_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type == ChatType.PRIVATE:
            await update.message.reply_text("Devi usare /createpoll direttamente nel gruppo.")
            return ConversationHandler.END

        await update.message.reply_text("Inserisci la domanda del sondaggio:")
        return Q_QUESTION

    async def createpoll_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["question"] = update.message.text.strip()
        await update.message.reply_text("Inserisci le opzioni separate da virgola (max 10):")
        return Q_OPTIONS

    async def createpoll_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
        options = [o.strip() for o in update.message.text.split(",") if o.strip()]
        if len(options) < 2:
            await update.message.reply_text("Servono almeno 2 opzioni.")
            return Q_OPTIONS
        if len(options) > 10:
            await update.message.reply_text("Massimo 10 opzioni.")
            return Q_OPTIONS

        context.user_data["options"] = options
        await update.message.reply_text("Ogni quanti minuti inviare il sondaggio?")
        return Q_INTERVAL

    async def createpoll_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            minutes = int(update.message.text.strip())
            if minutes <= 0:
                raise ValueError()
        except ValueError:
            await update.message.reply_text("Devi inserire un numero intero positivo.")
            return Q_INTERVAL

        context.user_data["interval"] = minutes
        await update.message.reply_text("Vuoi pinnare il sondaggio? (sì/no)")
        return Q_PIN

    async def createpoll_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
        pinned = update.message.text.lower() in ("si", "s", "yes", "y")

        pid = add_poll(
            update.effective_chat.id,
            context.user_data["question"],
            context.user_data["options"],
            context.user_data["interval"],
            pinned,
            update.effective_user.id,
        )

        await update.message.reply_text(f"Sondaggio creato con ID {pid}.")
        return ConversationHandler.END

    async def listpolls_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        rows = list_polls_for_chat(update.effective_chat.id)
        if not rows:
            await update.message.reply_text("Nessun sondaggio nel gruppo.")
            return

        out = []
        for pid, q, opts, mins, pinned, last in rows:
            last_s = time.strftime("%Y-%m-%d %H:%M", time.localtime(last)) if last else "mai"
            out.append(f"ID {pid}: '{q}' ogni {mins}m | pinned={bool(pinned)} | ultimo invio: {last_s}")

        await update.message.reply_text("\n".join(out))

    async def deletepoll_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Uso corretto: /deletepoll <id>")
            return

        try:
            pid = int(context.args[0])
        except ValueError:
            await update.message.reply_text("ID non valido.")
            return

        row = get_poll(pid)
        if not row:
            await update.message.reply_text("Sondaggio inesistente.")
            return

        if row[1] != update.effective_chat.id:
            await update.message.reply_text("Il sondaggio appartiene a un altro gruppo.")
            return

        delete_poll(pid)
        await update.message.reply_text(f"Sondaggio {pid} eliminato.")

    async def sendpollnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Uso: /sendpollnow <id>")
            return

        try:
            pid = int(context.args[0])
        except ValueError:
            await update.message.reply_text("ID non valido.")
            return

        row = get_poll(pid)
        if not row:
            await update.message.reply_text("Sondaggio inesistente.")
            return

        if row[1] != update.effective_chat.id:
            await update.message.reply_text("Il sondaggio appartiene a un altro gruppo.")
            return

        await send_poll_by_row(context, row)
        update_last_sent(pid, int(time.time()))
        await update.message.reply_text(f"Sondaggio {pid} inviato.")

    async def send_poll_by_row(context, row):
        pid, chat_id, question, opts_json, mins, pinned, last_sent = row
        options = json.loads(opts_json)

        try:
            msg = await context.bot.send_poll(
                chat_id=chat_id,
                question=question,
                options=options,
                is_anonymous=False,
            )
            if pinned:
                try:
                    await context.bot.pin_chat_message(chat_id, msg.message_id)
                except Exception as e:
                    logger.warning(f"Non posso pinnare in {chat_id}: {e}")
        except Exception as e:
            logger.exception(f"Errore invio sondaggio {pid}: {e}")

    async def periodic_check(context):
        now = int(time.time())

        conn = sqlite3.connect(DATABASE)
        cur = conn.cursor()
        cur.execute("SELECT id, chat_id, question, options, interval_minutes, pinned, last_sent FROM polls")
        rows = cur.fetchall()
        conn.close()

        for row in rows:
            pid, chat_id, q, opts_json, mins, pinned, last_sent = row
            next_send = last_sent + mins * 60

            if last_sent == 0 or next_send <= now:
                await send_poll_by_row(context, row)
                update_last_sent(pid, now)

    def run_bot():
        if not BOT_TOKEN:
            print("ERRORE: devi impostare la variabile d'ambiente BOT_TOKEN")
            return

        init_db()

        app = ApplicationBuilder().token(BOT_TOKEN).build()

        conv = ConversationHandler(
            entry_points=[CommandHandler("createpoll", createpoll_start)],
            states={
                Q_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, createpoll_question)],
                Q_OPTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, createpoll_options)],
                Q_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, createpoll_interval)],
                Q_PIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, createpoll_pin)],
            },
            fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        )

        app.add_handler(CommandHandler("start", start))
        app.add_handler(conv)
        app.add_handler(CommandHandler("listpolls", listpolls_cmd))
        app.add_handler(CommandHandler("deletepoll", deletepoll_cmd))
        app.add_handler(CommandHandler("sendpollnow", sendpollnow_cmd))

        # scheduler
        app.job_queue.run_repeating(periodic_check, interval=CHECK_INTERVAL_SECONDS, first=10)

        logger.info("Bot avviato.")
        app.run_polling()

# ---------------------------------------------------------------------------
# Test Locale (non richiede Telegram)
# ---------------------------------------------------------------------------
def _run_tests():
    import tempfile

    print("== TEST SU DATABASE ==")
    fd, tmp = tempfile.mkstemp(prefix="test_polls_", suffix=".db")
    os.close(fd)

    try:
        init_db(tmp)
        print("init_db OK")

        pid = add_poll(1, "Q", ["A", "B"], 10, False, 123, tmp)
        assert pid > 0
        print("add_poll OK")

        rows = list_polls_for_chat(1, tmp)
        assert len(rows) == 1
        print("list_polls_for_chat OK")

        row = get_poll(pid, tmp)
        assert row[0] == pid
        print("get_poll OK")

        now = int(time.time())
        update_last_sent(pid, now, tmp)
        row2 = get_poll(pid, tmp)
        assert row2[6] == now
        print("update_last_sent OK")

        assert delete_poll(pid, tmp) == 1
        assert len(list_polls_for_chat(1, tmp)) == 0
        print("delete_poll OK")

        print("TUTTI I TEST PASSATI")
    finally:
        try:
            os.remove(tmp)
        except:
            pass

# ---------------------------------------------------------------------------
def main():
    if "--test" in sys.argv:
        _run_tests()
        return

    if not TELEGRAM_AVAILABLE:
        print("ERRORE: modulo 'telegram' non trovato:", repr(_telegram_import_error))
        print("Installa python-telegram-bot con:")
        print("   pip install python-telegram-bot --upgrade")
        return

    run_bot()

if __name__ == "__main__":
    main()
