from telegram.ext import CallbackQueryHandler

def handle_file_manager(update, context):
    query = update.callback_query
    query.answer()
    server_id = query.data.split('_')[1]
    query.edit_message_text(f"📁 File Manager for server {server_id}")

def setup_file_handlers(application):
    application.add_handler(CallbackQueryHandler(handle_file_manager, pattern="^file_"))
