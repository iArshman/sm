import logging
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)
from db import db
from config import BOT_TOKEN
from file import setup_handlers  # Import from separate file.py
from botmanager import setup_handlers  # Import from separate botmanager.py

# --- Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- States ---
ADD_NAME, ADD_IP, ADD_USER, ADD_KEY = range(4)
EDIT_CHOOSE, EDIT_NAME, EDIT_IP, EDIT_USER = range(4)

# --- Main Menu ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main menu with vertical buttons"""
    await update.message.reply_text(
        "🖥️ *Server Manager*",
        reply_markup=ReplyKeyboardMarkup(
            [
                [KeyboardButton("📋 My Servers")],
                [KeyboardButton("➕ Add Server")]
            ],
            resize_keyboard=True,
            input_field_placeholder="Select an option..."
        )
    )

# --- Server List ---
async def list_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    servers = list(db.db.servers.find({}))
    
    if not servers:
        await update.message.reply_text(
            "No servers found. Add one first!",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("➕ Add Server")]], resize_keyboard=True)
        )
        return
    
    buttons = [
        [InlineKeyboardButton(f"🖥️ {s['name']} ({s['ip']})", callback_data=f"server_{s['_id']}")]
        for s in servers
    ]
    
    await update.message.reply_text(
        "Your servers:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# --- Server Menu ---
async def server_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    server_id = query.data.split('_')[1]
    
    # Vertical server action buttons
    keyboard = [
        [InlineKeyboardButton("📁 File Manager", callback_data=f"files_{server_id}")],
        [InlineKeyboardButton("🤖 Bot Manager", callback_data=f"bots_{server_id}")],
        [InlineKeyboardButton("ℹ️ Server Info", callback_data=f"info_{server_id}")],
        [InlineKeyboardButton("⚙️ Edit Server", callback_data=f"edit_{server_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_servers")]
    ]
    
    await query.edit_message_text(
        "Select an action:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# --- Edit Server (with Delete inside) ---
async def edit_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    server_id = query.data.split('_')[1]
    context.user_data['edit_server'] = server_id
    
    keyboard = [
        [InlineKeyboardButton("✏️ Change Name", callback_data="edit_name")],
        [InlineKeyboardButton("🌐 Change IP", callback_data="edit_ip")],
        [InlineKeyboardButton("👤 Change User", callback_data="edit_user")],
        [InlineKeyboardButton("🗑️ Delete Server", callback_data="delete_server")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"server_{server_id}")]
    ]
    
    await query.edit_message_text(
        "Edit Server:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return EDIT_CHOOSE

async def delete_server_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("✅ Confirm Delete", callback_data="confirm_delete")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"edit_{context.user_data['edit_server']}")]
    ]
    
    await query.edit_message_text(
        "⚠️ *This will permanently delete the server!*",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def delete_server_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    server_id = context.user_data['edit_server']
    
    # Delete from database
    db.db.servers.delete_one({"_id": server_id})
    
    await query.edit_message_text(
        "✅ Server deleted successfully!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 My Servers", callback_data="back_servers")]
        ])
    )
    return ConversationHandler.END

# --- Application Setup ---
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Main commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^📋 My Servers$"), list_servers))
    
    # Server actions
    app.add_handler(CallbackQueryHandler(server_menu, pattern="^server_"))
    app.add_handler(CallbackQueryHandler(list_servers, pattern="^back_servers$"))
    
    # Edit/Delete conversation
    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_server, pattern="^edit_")],
        states={
            EDIT_CHOOSE: [
                CallbackQueryHandler(delete_server_confirm, pattern="^delete_server$"),
                CallbackQueryHandler(delete_server_execute, pattern="^confirm_delete$")
            ]
        },
        fallbacks=[]
    )
    app.add_handler(edit_conv)
    
    # Setup handlers from separate files
    setup_file_handlers(app)  # From file.py
    setup_bot_handlers(app)  # From botmanager.py
    
    app.run_polling()

if __name__ == "__main__":
    main()
