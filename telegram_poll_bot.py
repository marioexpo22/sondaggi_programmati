import os
import time
import json
import sqlite3
import logging
from typing import List, Optional

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

# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("pollbot")

# =============================================================================
# CONFIG
# =============================================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE = os.environ.get("BOT_DB", "polls.db")
CHECK_INTERVAL_SECONDS = 60  # Railway è molto stabile con 60 sec

# =============================================================================
# DATABASE
# =============================================================================

def get_db():
    """Connessione sicura e pulita."""
    conn = sqlite3.connect(DATABASE, check_same_thread=False)
    return conn

def init_db():
    conn = get_db()
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
        );
    """)
    conn.commit()
    conn.close()


def add_poll(chat_id: int, question: str, options: List[str],
             interval_minutes: int, pinned: bool, creator_id: Optional[int]) -> int:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO polls (chat_id, question, options, interval_minutes, pinned, last_sent, creator_id)
        VALUES (?, ?, ?, ?, ?, 0, ?)
    """, (chat_id, question, json.dumps(options), interval_minutes, int(pinned), creator_id))
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return pid


def list_polls(chat_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM polls WHERE chat_id = ?", (chat_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_poll(poll_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM polls WHERE id = ?", (poll_id,))
    row = cur.fetchone()
    conn.close()
    return row


def delete_poll(poll_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM polls WHERE id = ?", (poll_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def set_last_sent(poll_id: int, timestamp: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE polls SET last_sent = ? WHERE id = ?", (timestamp, poll_id))
    conn.commit()
    conn.close()


# =============================================================================
# BOT LOGIC
# =============================================================================

Q_QUESTION, Q_OPTIONS, Q_INTERVAL, Q_PIN = range(4)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Bot attivo!\nUsa /createpoll per creare un nuovo sondaggio programmato."
    )


async def createpoll_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == ChatType.PRIVATE:
        await update.message.reply_text("Puoi creare sondaggi solo nei gruppi.")
        return ConversationHandler.END

    await update.message.reply_text("✏️ Inserisci la domanda del sondaggio:")
    return Q_QUESTION


async def set_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["question"] = update.message.text.strip()
    await update.message.reply_text("🧩 Inserisci le opzioni separate da virgola:")
    return Q_OPTIONS


async def set_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    opts = [o.strip() for o in update.message.text.split(",") if o.strip()]
    if len(opts) < 2:
        await update.message.reply_text("❗ Devi inserire almeno 2 opzioni.")
        return Q_OPTIONS
    if len(opts) > 10:
        await update.message.reply_text("❗ Puoi inserire massimo 10 opzioni.")
        return Q_OPTIONS

    context.user_data["options"] = opts
    await update.message.reply_text("⏱ Ogni quanti minuti inviare il sondaggio?")
    return Q_INTERVAL


async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        mins = int(update.message.text.strip())
        if mins <= 0:
            raise ValueError()
    except:
        await update.message.reply_text("❗ Inserisci un numero di minuti valido.")
        return Q_INTERVAL

    context.user_data["interval"] = mins
    await update.message.reply_text("📌 Vuoi pinnare il sondaggio? (sì/no)")
    return Q_PIN


async def set_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pinned = update.message.text.lower() in ("si", "s", "yes", "y")

    poll_id = add_poll(
        chat_id=update.effective_chat.id,
        question=context.user_data["question"],
        options=context.user_data["options"],
        interval_minutes=context.user_data["interval"],
        pinned=pinned,
        creator_id=update.effective_user.id,
    )

    await update.message.reply_text(
        f"🎉 Sondaggio creato!\nID: {poll_id}\n"
        f"Intervallo: {context.user_data['interval']} minuti\n"
        f"Pinnato: {pinned}"
    )
    return ConversationHandler.END


async def listpolls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    polls = list_polls(update.effective_chat.id)
    if not polls:
        await update.message.reply_text("📭 Nessun sondaggio programmato.")
        return

    msg = "📋 *Sondaggi attivi:*\n\n"
    for p in polls:
        pid, chat, q, opts, mins, pinned, last, *_ = p
        last_s = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last)) if last else "mai"
        msg += f"• ID {pid}: _{q}_\n  intervallo: {mins}m | pinned: {bool(pinned)} | ultimo: {last_s}\n\n"

    await update.message.reply_markdown(msg)


async def deletepoll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso corretto: /deletepoll <id>")
        return
    try:
        pid = int(context.args[0])
    except:
        await update.message.reply_text("ID non valido.")
        return

    if delete_poll(pid):
        await update.message.reply_text(f"🗑 Sondaggio {pid} eliminato.")
    else:
        await update.message.reply_text("❌ Nessun sondaggio trovato.")


async def sendpollnow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Uso: /sendpollnow <id>")
        return

    try:
        pid = int(context.args[0])
    except:
        await update.message.reply_text("ID non valido.")
        return

    row = get_poll(pid)
    if not row:
        await update.message.reply_text("❌ Sondaggio inesistente.")
        return

    await send_poll_from_row(context, row)
    set_last_sent(pid, int(time.time()))
    await update.message.reply_text(f"📨 Sondaggio {pid} inviato manualmente.")


async def send_poll_from_row(context, row):
    pid, chat_id, question, opts_json, mins, pinned, last_sent, *_ = row
    opts = json.loads(opts_json)

    try:
        msg = await context.bot.send_poll(
            chat_id=chat_id,
            question=question,
            options=opts,
            is_anonymous=False
        )

        if pinned:
            try:
                await context.bot.pin_chat_message(chat_id, msg.message_id)
            except Exception as e:
                logger.warning(f"Non posso pinnare in {chat_id}: {e}")

    except Exception as e:
        logger.error(f"Errore nell'invio sondaggio {pid}: {e}")


async def check_jobs(context: ContextTypes.DEFAULT_TYPE):
    """Invia automaticamente i sondaggi quando è ora."""
    now = int(time.time())
    polls = list_polls_for_all()

    for row in polls:
        pid, chat_id, q, opts_json, mins, pinned, last_sent, *_ = row
        due = (last_sent == 0) or (now >= last_sent + mins * 60)

        if due:
            await send_poll_from_row(context, row)
            set_last_sent(pid, now)


def list_polls_for_all():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM polls")
    rows = cur.fetchall()
    conn.close()
    return rows

# =============================================================================
# MAIN
# =============================================================================

def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN non impostato.")

    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("createpoll", createpoll_start)],
        states={
            Q_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_question)],
            Q_OPTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_options)],
            Q_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_interval)],
            Q_PIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_pin)],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CommandHandler("listpolls", listpolls))
    app.add_handler(CommandHandler("deletepoll", deletepoll))
    app.add_handler(CommandHandler("sendpollnow", sendpollnow))

    app.job_queue.run_repeating(check_jobs, interval=CHECK_INTERVAL_SECONDS, first=10)

    logger.info("🚀 Bot avviato.")
    app.run_polling()


if __name__ == "__main__":
    main()
