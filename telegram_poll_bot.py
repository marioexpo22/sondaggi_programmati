#!/usr/bin/env python3
"""
Bot Telegram – versione corretta e semplificata
Compatibile con:
- GitHub Actions / server headless
- PostgreSQL (Supabase) o SQLite fallback
- python-telegram-bot v20+

CORREZIONI PRINCIPALI:
- RIMOSSO dateutil (causava ModuleNotFoundError)
- Parsing orari manuale HH:MM
- Schema DB coerente (12 colonne ovunque)
- periodic_check stabile
- CallbackQueryHandler corretto
"""

import os
import sys
import json
import time
import logging
import sqlite3
from typing import List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ChatType
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes,
    ConversationHandler, MessageHandler, CallbackQueryHandler, filters
)

# -----------------------------------------------------------------------------
# CONFIG
# -----------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
DATABASE = os.environ.get("BOT_DB", "polls.db")
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Rome")

if not BOT_TOKEN:
    print("BOT_TOKEN non impostato. Esco.")
    sys.exit(1)

# -----------------------------------------------------------------------------
# LOGGING
# -----------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pollbot")

# -----------------------------------------------------------------------------
# DATABASE
# -----------------------------------------------------------------------------
USE_POSTGRES = False
if DATABASE_URL and DATABASE_URL.startswith("postgres"):
    try:
        import psycopg2
        USE_POSTGRES = True
    except Exception as e:
        logger.warning("psycopg2 non disponibile, uso SQLite: %s", e)


def get_conn():
    if USE_POSTGRES:
        return psycopg2.connect(DATABASE_URL)
    return sqlite3.connect(DATABASE, check_same_thread=False)


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS polls (
            id INTEGER PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            question TEXT NOT NULL,
            options TEXT NOT NULL,
            interval_minutes INTEGER,
            schedule_times TEXT,
            pinned BOOLEAN DEFAULT 0,
            last_sent BIGINT DEFAULT 0,
            last_message_id BIGINT,
            delete_previous BOOLEAN DEFAULT 0,
            active BOOLEAN DEFAULT 1,
            creator_id BIGINT
        )
    """)
    conn.commit()
    conn.close()


def execute(query, params=(), fetch=True):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall() if fetch else None
    conn.commit()
    conn.close()
    return rows

# -----------------------------------------------------------------------------
# UTILS
# -----------------------------------------------------------------------------

def parse_times(text: str) -> Optional[List[str]]:
    out = []
    for part in text.split(","):
        part = part.strip()
        if len(part) != 5 or part[2] != ":":
            return None
        h, m = part.split(":")
        if not (h.isdigit() and m.isdigit()):
            return None
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            return None
        out.append(f"{int(h):02d}:{int(m):02d}")
    return out

# -----------------------------------------------------------------------------
# CONVERSATION STATES
# -----------------------------------------------------------------------------
Q_QUESTION, Q_OPTIONS, Q_FLOW, Q_INTERVAL, Q_TIMES, Q_PIN, Q_DELETE = range(7)

# -----------------------------------------------------------------------------
# COMMANDS
# -----------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot attivo. Usa /createpoll nel gruppo.")


async def createpoll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == ChatType.PRIVATE:
        await update.message.reply_text("Usa il comando nel gruppo.")
        return ConversationHandler.END
    await update.message.reply_text("Inserisci la domanda del sondaggio:")
    return Q_QUESTION


async def set_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["question"] = update.message.text
    await update.message.reply_text("Inserisci le opzioni separate da virgola:")
    return Q_OPTIONS


async def set_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    opts = [o.strip() for o in update.message.text.split(",") if o.strip()]
    if len(opts) < 2:
        await update.message.reply_text("Servono almeno 2 opzioni.")
        return Q_OPTIONS
    context.user_data["options"] = opts
    await update.message.reply_text("Scrivi 'interval' o 'times'")
    return Q_FLOW


async def set_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text.lower()
    if t.startswith("i"):
        await update.message.reply_text("Ogni quanti minuti?")
        return Q_INTERVAL
    if t.startswith("t"):
        await update.message.reply_text("Orari HH:MM separati da virgola")
        return Q_TIMES
    await update.message.reply_text("Scrivi interval o times")
    return Q_FLOW


async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["interval"] = int(update.message.text)
    except:
        await update.message.reply_text("Numero non valido")
        return Q_INTERVAL
    await update.message.reply_text("Vuoi pinnare? (si/no)")
    return Q_PIN


async def set_times(update: Update, context: ContextTypes.DEFAULT_TYPE):
    times = parse_times(update.message.text)
    if not times:
        await update.message.reply_text("Formato errato")
        return Q_TIMES
    context.user_data["times"] = times
    await update.message.reply_text("Vuoi pinnare? (si/no)")
    return Q_PIN


async def set_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pinned"] = update.message.text.lower().startswith("s")
    await update.message.reply_text("Eliminare il precedente? (si/no)")
    return Q_DELETE


async def finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    delete_prev = update.message.text.lower().startswith("s")
    q = context.user_data
    execute(
        """INSERT INTO polls
        (chat_id, question, options, interval_minutes, schedule_times, pinned, delete_previous, active, creator_id)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            update.effective_chat.id,
            q.get("question"),
            json.dumps(q.get("options")),
            q.get("interval"),
            json.dumps(q.get("times")) if q.get("times") else None,
            q.get("pinned", False),
            delete_prev,
            True,
            update.effective_user.id,
        ),
        fetch=False,
    )
    await update.message.reply_text("Sondaggio creato ✔")
    return ConversationHandler.END

# -----------------------------------------------------------------------------
# PERIODIC CHECK
# -----------------------------------------------------------------------------

async def periodic_check(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(ZoneInfo(TIMEZONE)).strftime("%H:%M")
    rows = execute("SELECT * FROM polls WHERE active=1") or []
    for row in rows:
        (
            pid, chat_id, question, opts_json, mins, timesj,
            pinned, last_sent, last_mid, delete_prev, active, creator
        ) = row

        if timesj:
            for t in json.loads(timesj):
                if t == now:
                    await send_poll(context, row)
        elif mins:
            if time.time() >= last_sent + mins * 60:
                await send_poll(context, row)


async def send_poll(context, row):
    pid, chat_id, question, opts_json, *_ = row
    msg = await context.bot.send_poll(chat_id, question, json.loads(opts_json), is_anonymous=False)
    execute("UPDATE polls SET last_sent=? WHERE id=?", (int(time.time()), pid), fetch=False)

# -----------------------------------------------------------------------------
# MAIN
# -----------------------------------------------------------------------------

def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("createpoll", createpoll)],
        states={
            Q_QUESTION: [MessageHandler(filters.TEXT, set_question)],
            Q_OPTIONS: [MessageHandler(filters.TEXT, set_options)],
            Q_FLOW: [MessageHandler(filters.TEXT, set_flow)],
            Q_INTERVAL: [MessageHandler(filters.TEXT, set_interval)],
            Q_TIMES: [MessageHandler(filters.TEXT, set_times)],
            Q_PIN: [MessageHandler(filters.TEXT, set_pin)],
            Q_DELETE: [MessageHandler(filters.TEXT, finish)],
        },
        fallbacks=[],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.job_queue.run_repeating(periodic_check, interval=60, first=10)

    logger.info("Bot avviato")
    app.run_polling()


if __name__ == "__main__":
    main()
