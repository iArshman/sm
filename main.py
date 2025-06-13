from telegram.ext import Updater, CommandHandler
from server import setup_handlers
from files import setup_handlers as setup_file_handlers
from botmanager import setup_handlers as setup_bot_handlers
from config import BOT_TOKEN
import logging

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def start(update, context):
    update.message.reply_text(
        "🚀 Server Management Bot",
        reply_markup=ReplyKeyboardMarkup([['My Servers']], resize_keyboard=True)
    )

def main():
    updater = Updater(BOT_TOKEN)
    dp = updater.dispatcher
    
    dp.add_handler(CommandHandler("start", start))
    setup_handlers(dp)
    setup_file_handlers(dp)
    setup_bot_handlers(dp)
    
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
