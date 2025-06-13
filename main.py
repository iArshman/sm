import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ConversationHandler, ContextTypes, filters
from db import db
from config import BOT_TOKEN

# Setup logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# States
ADD_NAME, ADD_IP, ADD_USER, ADD_KEY = range(4)
EDIT_CHOOSE, EDIT_NAME, EDIT_IP, EDIT_USER = range(4)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🖥️ *Server Manager*",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("📋 My Servers")], [KeyboardButton("➕ Add Server")]],
            resize_keyboard=True,
            input_field_placeholder="Select an option..."
        )
    )

async def list_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    servers = list(db.db.servers.find({}))
    
    if not servers:
        await update.message.reply_text("No servers found. Add one first!",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("➕ Add Server")]], resize_keyboard=True))
        return
    
    buttons = [[InlineKeyboardButton(f"🖥️ {s['name']} ({s['ip']})", callback_data=f"server_{s['_id']}")] for s in servers]
    await update.message.reply_text("Your servers:", reply_markup=InlineKeyboardMarkup(buttons))

async def server_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    server_id = query.data.split('_')[1]
    
    keyboard = [
        [InlineKeyboardButton("📁 File Manager", callback_data=f"files_{server_id}")],
        [InlineKeyboardButton("🤖 Bot Manager", callback_data=f"bots_{server_id}")],
        [InlineKeyboardButton("ℹ️ Server Info", callback_data=f"info_{server_id}")],
        [InlineKeyboardButton("⚙️ Edit Server", callback_data=f"edit_{server_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_servers")]
    ]
    await query.edit_message_text("Select an action:", reply_markup=InlineKeyboardMarkup(keyboard))

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Main commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^📋 My Servers$"), list_servers))
    
    # Server actions
    app.add_handler(CallbackQueryHandler(server_menu, pattern="^server_"))
    app.add_handler(CallbackQueryHandler(list_servers, pattern="^back_servers$"))
    
    # Import and setup other handlers
    try:
        from filemanager import setup_file_handlers
        setup_file_handlers(app)
    except ImportError:
        logging.warning("File manager module not found")
    
    try:
        from botmanager import setup_bot_handlers
        setup_bot_handlers(app)
    except ImportError:
        logging.warning("Bot manager module not found")
    
    app.run_polling()

if __name__ == "__main__":
    main()
