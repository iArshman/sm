from telegram.ext import CallbackQueryHandler

def setup_handlers(dp):
    dp.add_handler(CallbackQueryHandler(bot_callback, pattern='^srv:bots:'))

def bot_callback(update, context):
    query = update.callback_query
    query.answer()
    query.edit_message_text("🤖 Bot manager coming soon!")
