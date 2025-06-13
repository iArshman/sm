import logging
import re
import paramiko
from io import StringIO
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

# --- Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- States ---
ADD_NAME, ADD_IP, ADD_USER, ADD_KEY = range(4)
EDIT_CHOOSE, EDIT_NAME, EDIT_IP, EDIT_USER, EDIT_DELETE = range(5)

# --- Main Menu ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🖥️ *Server Manager*",
        reply_markup=ReplyKeyboardMarkup(
            [
                [KeyboardButton("📋 My Servers")],
                [KeyboardButton("➕ Add Server")]
            ],
            resize_keyboard=True
        ),
        parse_mode='Markdown'
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
    
    buttons = []
    for server in servers:
        try:
            ssh = await db.get_ssh(server)
            ssh.exec_command("exit", timeout=2)
            status = "🟢"
        except:
            status = "🔴"
        
        buttons.append([InlineKeyboardButton(
            f"{status} {server['name']} ({server['ip']})", 
            callback_data=f"server_{server['_id']}"
        )])
    
    await update.message.reply_text(
        "Your servers: 🟢 Online | 🔴 Offline",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

# --- Server Menu ---
async def server_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    server_id = query.data.split('_')[1]
    context.user_data['current_server'] = server_id
    
    keyboard = [
        [InlineKeyboardButton("📁 File Manager", callback_data=f"file_{server_id}")],
        [InlineKeyboardButton("🤖 Bot Manager", callback_data=f"bot_{server_id}")],
        [InlineKeyboardButton("ℹ️ Server Info", callback_data=f"info_{server_id}")],
        [InlineKeyboardButton("⚙️ Edit Server", callback_data=f"edit_{server_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_servers")]
    ]
    
    await query.edit_message_text(
        "Server Management:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# --- Server Info ---
async def server_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    server_id = query.data.split('_')[1]
    server = db.db.servers.find_one({"_id": server_id})
    
    try:
        ssh = await db.get_ssh(server)
        stdin, stdout, stderr = ssh.exec_command("""
            echo -n '🖥️ '; hostname;
            echo -n '⏱️ '; uptime -p;
            echo -n '🧠 '; free -m | awk '/Mem:/{printf "%.1f%% of %sMB", $3*100/$2, $2}';
            echo -n '💾 '; df -h / --output=pcent | tail -n1
        """, timeout=5)
        stats = stdout.read().decode()
        status = "🟢 Online"
    except Exception as e:
        stats = f"🔴 Error: {str(e)}"
        status = "🔴 Offline"
    
    await query.edit_message_text(
        f"⚙️ *{server['name']}*\n"
        f"📍 `{server['ip']}` | {status}\n"
        f"👤 {server['username']}\n\n"
        f"📊 *Live Stats*\n{stats}",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data=f"server_{server_id}")]
        ])
    )

# --- Edit Server ---
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
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def delete_server_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    server_id = context.user_data['edit_server']
    
    db.db.servers.delete_one({"_id": server_id})
    await query.edit_message_text(
        "✅ Server deleted successfully!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 My Servers", callback_data="back_servers")]
        ])
    )
    return ConversationHandler.END

# --- Add Server ---
async def add_server_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Let's add a new server!\n\n"
        "📝 Step 1/4: Enter server name:",
        reply_markup=ReplyKeyboardMarkup([[KeyboardButton("❌ Cancel")]], resize_keyboard=True)
    )
    return ADD_NAME

async def add_server_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(update.message.text) < 3:
        await update.message.reply_text("❌ Name too short! Minimum 3 characters")
        return ADD_NAME
    
    context.user_data['name'] = update.message.text
    await update.message.reply_text("📝 Step 2/4: Enter server IP:")
    return ADD_IP

async def add_server_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', update.message.text):
        await update.message.reply_text("❌ Invalid IP format! Use IPv4 like '192.168.1.100'")
        return ADD_IP
    
    context.user_data['ip'] = update.message.text
    await update.message.reply_text("📝 Step 3/4: Enter SSH username:")
    return ADD_USER

async def add_server_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not re.match(r'^[a-z_][a-z0-9_-]{0,31}$', update.message.text):
        await update.message.reply_text("❌ Invalid username! Use lowercase letters/numbers")
        return ADD_USER
    
    context.user_data['user'] = update.message.text
    await update.message.reply_text("📝 Step 4/4: Upload SSH key file:")
    return ADD_KEY

async def add_server_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        await update.message.reply_text("❌ Please upload a key file!")
        return ADD_KEY
    
    try:
        file = await update.message.document.get_file()
        key_content = (await file.download_as_bytearray()).decode('utf-8')
        
        # Test connection
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        key_file = StringIO(key_content)
        ssh.connect(
            hostname=context.user_data['ip'],
            username=context.user_data['user'],
            pkey=paramiko.RSAKey.from_private_key(key_file),
            timeout=10
        )
        ssh.close()
        
        # Save server
        db.db.servers.insert_one({
            'name': context.user_data['name'],
            'ip': context.user_data['ip'],
            'username': context.user_data['user'],
            'ssh_key': key_content
        })
        
        await update.message.reply_text(
            "✅ Server added successfully!",
            reply_markup=ReplyKeyboardMarkup([[KeyboardButton("📋 My Servers")]], resize_keyboard=True)
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Failed to add server: {str(e)}")
    finally:
        context.user_data.clear()
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Operation cancelled",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("📋 My Servers"), KeyboardButton("➕ Add Server")]],
            resize_keyboard=True
        )
    )
    return ConversationHandler.END

# --- Main Application ---
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Core commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^📋 My Servers$"), list_servers))
    
    # Server actions
    app.add_handler(CallbackQueryHandler(server_menu, pattern="^server_"))
    app.add_handler(CallbackQueryHandler(server_info, pattern="^info_"))
    app.add_handler(CallbackQueryHandler(list_servers, pattern="^back_servers$"))
    
    # Add server
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Add Server$"), add_server_start)],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_server_name)],
            ADD_IP: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_server_ip)],
            ADD_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_server_user)],
            ADD_KEY: [MessageHandler(filters.Document.ALL, add_server_key)]
        },
        fallbacks=[MessageHandler(filters.Regex("^❌ Cancel$"), cancel)]
    ))
    
    # Edit/Delete
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_server, pattern="^edit_")],
        states={
            EDIT_CHOOSE: [
                CallbackQueryHandler(delete_server_confirm, pattern="^delete_server$"),
                CallbackQueryHandler(delete_server_execute, pattern="^confirm_delete$")
            ]
        },
        fallbacks=[]
    ))
    
    # Import other handlers
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
