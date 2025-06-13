from telegram import ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler
from server import setup_handlers
from config import BOT_TOKEN
import logging

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update, context):
    # This will show persistent buttons at the bottom of the chat
    await update.message.reply_text(
        "Welcome to Server Manager!",
        reply_markup=ReplyKeyboardMarkup(
            [['My Servers', 'Add Server']],  # Two buttons in one row
            resize_keyboard=True,  # Makes buttons smaller to fit
            one_time_keyboard=False  # Buttons stay until replaced
        )
    )

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    setup_handlers(application)
    
    application.run_polling()

if __name__ == '__main__':
    main()
