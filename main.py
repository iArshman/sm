from telegram.ext import Application, CommandHandler
from server import setup_handlers
from file import setup_handlers as setup_file_handlers
from botmanager import setup_handlers as setup_bot_handlers
from config import BOT_TOKEN
import logging
import asyncio

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def post_init(app):
    logging.info("Bot is ready")

async def start(update, context):
    await update.message.reply_text(
        "🚀 Server Management Bot",
        reply_markup=ReplyKeyboardMarkup([['My Servers']], resize_keyboard=True)
    )

def main():
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # Register handlers
    setup_handlers(application)
    setup_file_handlers(application)
    setup_bot_handlers(application)
    
    # Add commands
    application.add_handler(CommandHandler("start", start))
    
    # Start bot
    application.run_polling()

if __name__ == '__main__':
    main()
