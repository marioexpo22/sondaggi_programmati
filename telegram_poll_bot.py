import os
import logging
from telegram.ext import ApplicationBuilder, CommandHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")

async def start(update, context):
    await update.message.reply_text("Bot attivo e funzionante su Railway.app!")

def main():
    if not BOT_TOKEN:
        print("ERRORE: Variabile BOT_TOKEN non impostata")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))

    logger.info("Bot avviato.")
    app.run_polling()

if __name__ == "__main__":
    main()
