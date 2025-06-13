from telegram.ext import CallbackQueryHandler

def handle_bot_manager(update, context):
    query = update.callback_query
    query.answer()
    server_id = query.data.split('_')[1]
    query.edit_message_text(f"🤖 Bot Manager for server {server_id}")

def setup_bot_handlers(application):
    application.add_handler(CallbackQueryHandler(handle_bot_manager, pattern="^bot_"))
