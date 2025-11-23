#!/usr/bin/env python3
# telegram_poll_bot_advanced.py
"""
Bot avanzato per Telegram su Railway
Features:
- sondaggi programmati con intervalli e orari specifici (daily HH:MM)
- multi-sondaggi per chat, pause/riattiva, invio manuale
- pannello admin (inline keyboard) per gestire sondaggi (delete/pause/resume/send)
- supporto PostgreSQL via DATABASE_URL (psycopg2) o fallback a SQLite
- job-queue reliability via python-telegram-bot job queue
- timezone support via zoneinfo (env TIMEZONE, default Europe/Rome)
"""

import os
import sys
import json
import time
import logging
import sqlite3
from typing import List, Optional, Tuple
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo
import dateutil.parser

# DB: will use psycopg2 if DATABASE_URL provided that starts with 'postgres', otherwise sqlite3
DATABASE_URL = os.environ.get("DATABASE_URL")
DATABASE = os.environ.get("BOT_DB", "polls.db")
TIMEZONE = os.environ.get("TIMEZONE", "Europe/Rome")

# Telegram
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ChatType
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes, ConversationHandler,
    MessageHandler, filters, CallbackQueryHandler
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("pollbot-adv")

# DB helpers
USE_POSTGRES = False
if DATABASE_URL and DATABASE_URL.startswith("postgres"):
    try:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        USE_POSTGRES = True
    except Exception as e:
        logger.warning("psycopg2 non disponibile o errore import: %s. Uso SQLite.", e)
        USE_POSTGRES = False

def get_conn():
    if USE_POSTGRES:
        return psycopg2.connect(DATABASE_URL)
    conn = sqlite3.connect(DATABASE, check_same_thread=False)
    return conn

def init_db():
    if USE_POSTGRES:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS polls (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                question TEXT NOT NULL,
                options TEXT NOT NULL,
                interval_minutes INTEGER,
                schedule_times TEXT, -- JSON array of HH:MM strings
                pinned BOOLEAN DEFAULT FALSE,
                last_sent BIGINT DEFAULT 0,
                last_message_id BIGINT DEFAULT NULL,
                delete_previous BOOLEAN DEFAULT FALSE,
                active BOOLEAN DEFAULT TRUE,
                creator_id BIGINT
            )
        """)
        conn.commit()
        conn.close()
    else:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS polls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                options TEXT NOT NULL,
                interval_minutes INTEGER,
                schedule_times TEXT,
                pinned INTEGER DEFAULT 0,
                last_sent INTEGER DEFAULT 0,
                last_message_id INTEGER DEFAULT NULL,
                delete_previous INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                creator_id INTEGER
            )
        """)
        conn.commit()
        conn.close()

def execute(query: str, params: Tuple = ()):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(query, params)
    try:
        rows = cur.fetchall()
    except Exception:
        rows = None
    conn.commit()
    conn.close()
    return rows

def add_poll(chat_id:int, question:str, options:List[str], interval_minutes:Optional[int], schedule_times:Optional[List[str]], pinned:bool, delete_previous:bool, creator_id:Optional[int]) -> int:
    optj = json.dumps(options, ensure_ascii=False)
    timesj = json.dumps(schedule_times) if schedule_times else None
    if USE_POSTGRES:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("INSERT INTO polls (chat_id, question, options, interval_minutes, schedule_times, pinned, last_sent, last_message_id, delete_previous, active, creator_id) VALUES (%s,%s,%s,%s,%s,%s,0,NULL,%s,TRUE,%s) RETURNING id", (chat_id, question, optj, interval_minutes, timesj, pinned, 1 if delete_previous else 0, creator_id))
        pid = cur.fetchone()[0]
        conn.commit(); conn.close()
        return pid
    else:
        conn = get_conn(); cur = conn.cursor()
        cur.execute("INSERT INTO polls (chat_id, question, options, interval_minutes, schedule_times, pinned, last_sent, last_message_id, delete_previous, active, creator_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (chat_id, question, optj, interval_minutes, timesj, 1 if pinned else 0, 0, None, 1 if delete_previous else 0, 1, creator_id))
        pid = cur.lastrowid; conn.commit(); conn.close()
        return pid

def list_polls_for_chat(chat_id:int):
    if USE_POSTGRES:
        rows = execute("SELECT id,chat_id,question,options,interval_minutes,schedule_times,pinned,last_sent,last_message_id,delete_previous,active,creator_id FROM polls WHERE chat_id=%s", (chat_id,))
    else:
        rows = execute("SELECT id,chat_id,question,options,interval_minutes,schedule_times,pinned,last_sent,last_message_id,delete_previous,active,creator_id FROM polls WHERE chat_id=?", (chat_id,))
    return rows or []

def get_poll(poll_id:int):
    if USE_POSTGRES:
        rows = execute("SELECT id,chat_id,question,options,interval_minutes,schedule_times,pinned,last_sent,last_message_id,delete_previous,active,creator_id FROM polls WHERE id=%s", (poll_id,))
    else:
        rows = execute("SELECT id,chat_id,question,options,interval_minutes,schedule_times,pinned,last_sent,last_message_id,delete_previous,active,creator_id FROM polls WHERE id=?", (poll_id,))
    return rows[0] if rows else None

def update_last_sent(poll_id:int, ts:int):
    if USE_POSTGRES:
        execute("UPDATE polls SET last_sent=%s WHERE id=%s", (ts, poll_id))
    else:
        execute("UPDATE polls SET last_sent=? WHERE id=?", (ts, poll_id))


def update_last_sent_and_message(poll_id:int, ts:int, message_id:Optional[int]):
    if USE_POSTGRES:
        execute("UPDATE polls SET last_sent=%s, last_message_id=%s WHERE id=%s", (ts, message_id, poll_id))
    else:
        execute("UPDATE polls SET last_sent=?, last_message_id=? WHERE id=?", (ts, message_id, poll_id))

def set_active(poll_id:int, active:bool):
    if USE_POSTGRES:
        execute("UPDATE polls SET active=%s WHERE id=%s", (active, poll_id))
    else:
        execute("UPDATE polls SET active=? WHERE id=?", (1 if active else 0, poll_id))

def delete_poll_db(poll_id:int):
    if USE_POSTGRES:
        execute("DELETE FROM polls WHERE id=%s", (poll_id,))
    else:
        execute("DELETE FROM polls WHERE id=?", (poll_id,))

# -----------------------------------------------------------------------------
# Bot handlers: creation conversation will allow either interval or schedule times
# -----------------------------------------------------------------------------
Q_QUESTION, Q_OPTIONS, Q_FLOWCHOICE, Q_INTERVAL, Q_SCHEDULE, Q_PIN, Q_DELETE_PREV = range(7)

async def start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot avanzato attivo. /createpoll per creare un sondaggio. /admin per pannello amministratore.")

async def createpoll_start(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == ChatType.PRIVATE:
        await update.message.reply_text("Crea sondaggi direttamente nel gruppo.")
        return ConversationHandler.END
    await update.message.reply_text("Inserisci la domanda del sondaggio:")
    return Q_QUESTION

async def set_question(update:Update, context:ContextTypes.DEFAULT_TYPE):
    context.user_data['question'] = update.message.text.strip()
    await update.message.reply_text("Inserisci le opzioni separate da virgola:")
    return Q_OPTIONS

async def set_options(update:Update, context:ContextTypes.DEFAULT_TYPE):
    opts = [o.strip() for o in update.message.text.split(",") if o.strip()]
    if len(opts) < 2:
        await update.message.reply_text("Servono almeno 2 opzioni.")
        return Q_OPTIONS
    context.user_data['options'] = opts
    # ask flow: interval or schedule times
    await update.message.reply_text("Vuoi invio a intervallo (es. ogni N minuti) o a orari precisi giornalieri? Scrivi 'interval' o 'times'")
    return Q_FLOWCHOICE

async def set_flowchoice(update:Update, context:ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip().lower()
    if txt.startswith('i'):
        await update.message.reply_text("Inserisci l'intervallo in minuti (es. 60):")
        return Q_INTERVAL
    elif txt.startswith('t'):
        await update.message.reply_text("Inserisci gli orari giornalieri separati da virgola in formato HH:MM (es. 09:00,18:30):")
        return Q_SCHEDULE
    else:
        await update.message.reply_text("Risposta non valida, scrivi 'interval' o 'times'")
        return Q_FLOWCHOICE

async def set_interval(update:Update, context:ContextTypes.DEFAULT_TYPE):
    try:
        mins = int(update.message.text.strip())
        if mins <= 0:
            raise ValueError()
    except Exception:
        await update.message.reply_text("Intervallo non valido. Inserisci un numero intero positivo.")
        return Q_INTERVAL
    context.user_data['interval'] = mins
    await update.message.reply_text("Vuoi pinnare il sondaggio? (sì/no)")
    return Q_PIN

def valid_times_list(text:str) -> Optional[List[str]]:
    parts = [p.strip() for p in text.split(",") if p.strip()]
    out = []
    for p in parts:
        try:
            dt = dateutil.parser.parse(p)
            # keep HH:MM
            out.append(dt.strftime("%H:%M"))
        except Exception:
            return None
    return out

async def set_schedule_times(update:Update, context:ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    times = valid_times_list(txt)
    if not times:
        await update.message.reply_text("Formato orari non valido. Usa HH:MM separati da virgola.")
        return Q_SCHEDULE
    context.user_data['times'] = times
    await update.message.reply_text("Vuoi pinnare il sondaggio? (sì/no)")
    return Q_PIN

async def set_pin(update:Update, context:ContextTypes.DEFAULT_TYPE):
    # ask whether to pin then ask whether to delete previous
    context.user_data['pinned'] = update.message.text.strip().lower() in ('si','s','yes','y')
    await update.message.reply_text("Vuoi eliminare automaticamente il precedente sondaggio quando questo sarà inviato? (sì/no)")
    return Q_DELETE_PREV


async def set_delete_prev(update:Update, context:ContextTypes.DEFAULT_TYPE):
    delete_prev = update.message.text.strip().lower() in ('si','s','yes','y')
    question = context.user_data.get('question')
    options = context.user_data.get('options')
    interval = context.user_data.get('interval')
    times = context.user_data.get('times')
    pinned = context.user_data.get('pinned', False)
    pid = add_poll(update.effective_chat.id, question, options, interval, times, pinned, delete_prev, update.effective_user.id)
    await update.message.reply_text(f"Sondaggio creato con ID {pid}. delete_previous={delete_prev}")
    return ConversationHandler.END

# -----------------------------------------------------------------------------
# Admin panel (inline keyboard)
# -----------------------------------------------------------------------------

async def is_user_admin(chat_id:int, user_id:int, context:ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status in ("creator","owner"):
            return True
        if member.status == "administrator":
            if getattr(member,"is_anonymous",False):
                return False
            return True
        return False
    except Exception as e:
        logger.warning("Errore controllo admin: %s", e)
        return False

async def admin_panel(update:Update, context:ContextTypes.DEFAULT_TYPE):
    # restrict to chat admins
    if not await is_user_admin(update.effective_chat.id, update.effective_user.id, context):
        await update.message.reply_text("Accesso negato: solo admin possono usare il pannello.")
        return
    polls = list_polls_for_chat(update.effective_chat.id)
    if not polls:
        await update.message.reply_text("Nessun sondaggio attivo.")
        return
    keyboard = []
    for p in polls:
        pid = p[0]
        q = p[2] if USE_POSTGRES==False else p[2]
        keyboard.append([InlineKeyboardButton(f"ID {pid}: {q[:30]}", callback_data=f"view:{pid}")])
    keyboard.append([InlineKeyboardButton("Chiudi", callback_data="close")])
    await update.message.reply_text("Pannello Admin - seleziona un sondaggio:", reply_markup=InlineKeyboardMarkup(keyboard))

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return  # Non è un callback — ignora
    data = query.data
    if data == "close":
        await query.message.delete()
        return
    if data.startswith("view:"):
        pid = int(data.split(":",1)[1])
        row = get_poll(pid)
        if not row:
            await query.answer("Sondaggio non trovato")
            return
        # build action buttons
        active = row[8] if USE_POSTGRES else row[8]
        text = f"ID {row[0]}\\nDomanda: {row[2]}\\n"
        text += f"Opzioni: {len(json.loads(row[3]))}\\n"
        keyboard = [
            [InlineKeyboardButton("Invia ora", callback_data=f"send:{pid}"), InlineKeyboardButton("Elimina", callback_data=f"del:{pid}")],
            [InlineKeyboardButton("Pausa" if active else "Riattiva", callback_data=f"toggle:{pid}")],
            [InlineKeyboardButton("Chiudi", callback_data="close")]
        ]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        await query.answer()
        return
    if data.startswith("send:"):
        pid = int(data.split(":",1)[1])
        row = get_poll(pid)
        if row:
            await send_poll_from_row(context, row)
            update_last_sent(pid, int(time.time()))
            await query.answer("Sondaggio inviato")
        else:
            await query.answer("Non trovato")
        return
    if data.startswith("del:"):
        pid = int(data.split(":",1)[1])
        delete_poll_db(pid)
        await query.answer("Sondaggio eliminato")
        await query.message.delete()
        return
    if data.startswith("toggle:"):
        pid = int(data.split(":",1)[1])
        row = get_poll(pid)
        if not row:
            await query.answer("Non trovato")
            return
        active = row[8] if USE_POSTGRES else row[8]
        set_active(pid, not bool(active))
        await query.answer("Stato cambiato")
        await query.message.delete()
        return

# -----------------------------------------------------------------------------
# Sending polls and scheduling
# -----------------------------------------------------------------------------

async def send_poll_from_row(context, row):
    pid, chat_id, question, opts_json, mins, timesj, pinned, last_sent, last_message_id, delete_previous, active, creator = row
    if not active:
        return
    options = json.loads(opts_json)
    try:
        msg = await context.bot.send_poll(chat_id=chat_id, question=question, options=options, is_anonymous=False)
        message_id = msg.message_id
        # delete previous if requested
        if delete_previous and last_message_id:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=last_message_id)
            except Exception as e:
                logger.warning(f"Impossibile eliminare messaggio precedente: {e}")
        # update last_sent and last_message_id
        update_last_sent_and_message(pid, int(time.time()), message_id)
        if pinned:
            try:
                await context.bot.pin_chat_message(chat_id, message_id)
            except Exception as e:
                logger.warning("Pin failed: %s", e)
    except Exception as e:
        logger.exception("Error sending poll %s: %s", pid, e)
def schedule_jobs(app):
    """Schedule jobs for polls with schedule_times and ensure interval-driven polls are handled by periodic check"""
    jq = app.job_queue
    tz = ZoneInfo(TIMEZONE)
    # schedule daily times
    all_polls = execute("SELECT id,chat_id,question,options,interval_minutes,schedule_times,pinned,last_sent,active,creator_id FROM polls", ())
    if not all_polls:
        return
    for row in all_polls:
        pid = row[0]
        schedule_times = row[5]
        active = row[8] if not USE_POSTGRES else row[8]
        if not active:
            continue
        if schedule_times:
            try:
                times = json.loads(schedule_times)
            except Exception:
                times = None
            if times:
                for t in times:
                    try:
                        hh,mm = map(int, t.split(":"))
                        # schedule daily at this time in tz - use job_queue.run_daily
                        target_time = dtime(hour=hh, minute=mm, tzinfo=tz)
                        # create job with name
                        jq.run_daily(daily_job_callback, target_time, days=(0,1,2,3,4,5,6), name=f"poll_{pid}_{t}", context=pid)
                        logger.info("Scheduled daily job for poll %s at %s", pid, t)
                    except Exception as e:
                        logger.warning("Invalid time %s for poll %s: %s", t, pid, e)

async def daily_job_callback(context:ContextTypes.DEFAULT_TYPE):
    pid = context.job.context
    row = get_poll(pid)
    if row:
        await send_poll_from_row(context, row)
        update_last_sent(pid, int(time.time()))

async def periodic_check(context:ContextTypes.DEFAULT_TYPE):
    """Check interval-based polls and send if due"""
    now = int(time.time())
    rows = execute("SELECT id,chat_id,question,options,interval_minutes,schedule_times,pinned,last_sent,active,creator_id FROM polls", ())
    if not rows:
        return
    for row in rows:
        pid, chat_id, q, opts_json, mins, timesj, pinned, last_sent, active, creator = row
        if not active:
            continue
        if mins and (last_sent==0 or now >= last_sent + mins*60):
            await send_poll_from_row(context, row)
            update_last_sent(pid, now)

# -----------------------------------------------------------------------------
# Main and setup handlers
# -----------------------------------------------------------------------------
def main():
    BOT_TOKEN = os.environ.get("BOT_TOKEN")
    if not BOT_TOKEN:
        print("BOT_TOKEN non impostato. Esco.")
        sys.exit(1)
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("createpoll", createpoll_start)],
        states={
            Q_QUESTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_question)],
            Q_OPTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_options)],
            Q_FLOWCHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_flowchoice)],
            Q_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_interval)],
            Q_SCHEDULE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_schedule_times)],
            Q_PIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_pin)],
            Q_DELETE_PREV: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_delete_prev)]
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: ConversationHandler.END)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CommandHandler("listpolls", lambda u,c: u.message.reply_text("Use /admin to manage polls.")))
    app.add_handler(CommandHandler("deletepoll", lambda u,c: u.message.reply_text("Use admin panel.")))
    app.add_handler(CommandHandler("sendpollnow", lambda u,c: u.message.reply_text("Use admin panel.")))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(on_callback))

    # schedule jobs (runs once at startup)
    schedule_jobs(app)

    # periodic check for interval-based polls
    app.job_queue.run_repeating(periodic_check, interval=60, first=20)

    logger.info("Bot avviato.")
    app.run_polling()

if __name__ == "__main__":
    main()
