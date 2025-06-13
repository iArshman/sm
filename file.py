from telegram.ext import CallbackQueryHandler

def setup_handlers(dp):
    dp.add_handler(CallbackQueryHandler(file_callback, pattern='^srv:files:'))

def file_callback(update, context):
    query = update.callback_query
    query.answer()
    query.edit_message_text("📁 File manager coming soon!")
