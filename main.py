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
    await update.message.reply_text(
        "🖥️ *Server Manager*\n\n"
        "Manage your servers with ease!",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardMarkup(
            [['📋 My Servers', '➕ Add Server']],
            resize_keyboard=True,
            input_field_placeholder="Choose an option..."
        )
    )

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    setup_handlers(application)
    application.run_polling()

if __name__ == '__main__':
    main()
