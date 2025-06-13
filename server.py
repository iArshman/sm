from telegram import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters
)
from db import db

# States for adding server
NAME, IP, USERNAME, SSH_KEY = range(4)

async def list_servers(update, context):
    servers = list(db.db.servers.find({"user_id": update.effective_user.id}))
    
    if not servers:
        await update.message.reply_text(
            "No servers found. Add one first!",
            reply_markup=ReplyKeyboardMarkup([['Add Server']], resize_keyboard=True)
        )
        return
    
    buttons = [
        [InlineKeyboardButton(f"{s['name']} ({s['ip']})", callback_data=f"view:{s['_id']}")]
        for s in servers
    ]
    
    await update.message.reply_text(
        "Select a server:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def add_server_start(update, context):
    await update.message.reply_text(
        "Let's add a new server. First, send me the server name:",
        reply_markup=ReplyKeyboardMarkup([['Cancel']], resize_keyboard=True)
    )
    return NAME

async def add_server_name(update, context):
    context.user_data['name'] = update.message.text
    await update.message.reply_text("Great! Now send me the server IP address:")
    return IP

async def add_server_ip(update, context):
    context.user_data['ip'] = update.message.text
    await update.message.reply_text("Now send me the SSH username:")
    return USERNAME

async def add_server_username(update, context):
    context.user_data['username'] = update.message.text
    await update.message.reply_text("Finally, upload your SSH private key file:")
    return SSH_KEY

async def add_server_ssh_key(update, context):
    file = await update.message.document.get_file()
    key_content = (await file.download_as_bytearray()).decode('utf-8')
    
    server = {
        'user_id': update.effective_user.id,
        'name': context.user_data['name'],
        'ip': context.user_data['ip'],
        'username': context.user_data['username'],
        'ssh_key': key_content
    }
    
    db.db.servers.insert_one(server)
    
    await update.message.reply_text(
        "Server added successfully!",
        reply_markup=ReplyKeyboardMarkup([['My Servers']], resize_keyboard=True)
    )
    return ConversationHandler.END

async def cancel(update, context):
    await update.message.reply_text(
        "Cancelled.",
        reply_markup=ReplyKeyboardMarkup([['My Servers', 'Add Server']], resize_keyboard=True)
    )
    return ConversationHandler.END

def setup_handlers(application):
    # Main menu buttons
    application.add_handler(MessageHandler(filters.Regex('^My Servers$'), list_servers))
    
    # Add server conversation
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^Add Server$'), add_server_start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_server_name)],
            IP: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_server_ip)],
            USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_server_username)],
            SSH_KEY: [MessageHandler(filters.Document.ALL, add_server_ssh_key)]
        },
        fallbacks=[MessageHandler(filters.Regex('^Cancel$'), cancel)]
    )
    application.add_handler(conv_handler)

async def server_details(update, context):
    query = update.callback_query
    await query.answer()
    
    server_id = query.data.split('_')[1]
    server = db.db.servers.find_one({"_id": server_id})
    
    if not server:
        await query.edit_message_text("Server not found!")
        return
    
    status = "🟢 Online" if await check_server_status(server) else "🔴 Offline"
    
    keyboard = [
        [InlineKeyboardButton("📁 File Manager", callback_data=f"files_{server_id}")],
        [InlineKeyboardButton("ℹ️ Server Info", callback_data=f"info_{server_id}")],
        [InlineKeyboardButton("✏️ Edit Server", callback_data=f"edit_{server_id}")],
        [InlineKeyboardButton("🗑️ Delete Server", callback_data=f"delete_{server_id}")]
    ]
    
    await query.edit_message_text(
        f"Server: {server['name']}\n"
        f"IP: {server['ip']}\n"
        f"Status: {status}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
