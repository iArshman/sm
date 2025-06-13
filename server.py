from telegram import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    Update
)
from telegram.ext import (
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)
from db import db
import paramiko
from io import StringIO
import time
import re

# Conversation states
NAME, IP, USER, KEY = range(4)

# UI Constants
HEADER = "⚡ *Server Manager*\n_________________________"
DIVIDER = "\n_________________________"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main menu with persistent buttons"""
    await update.message.reply_text(
        f"{HEADER}\n\n"
        "Manage your servers with these options:",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardMarkup(
            [['📋 My Servers', '➕ Add Server']],
            resize_keyboard=True,
            input_field_placeholder="Tap a command..."
        )
    )

async def list_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all servers with real-time status"""
    servers = list(db.db.servers.find({"user_id": update.effective_user.id}))
    
    if not servers:
        await update.message.reply_text(
            f"{HEADER}\n\n"
            "❌ No servers found. Add your first server!",
            reply_markup=ReplyKeyboardMarkup([['➕ Add Server']], resize_keyboard=True)
        )
        return
    
    # Check all servers' status in parallel
    status_results = []
    for server in servers:
        try:
            ssh = await db.get_ssh(server)
            stdin, stdout, stderr = ssh.exec_command("echo 'Ping'", timeout=3)
            status_results.append("🟢")
        except:
            status_results.append("🔴")
    
    buttons = [
        [InlineKeyboardButton(
            f"{status} {server['name']}", 
            callback_data=f"view_{server['_id']}"
        )]
        for server, status in zip(servers, status_results)
    ]
    
    await update.message.reply_text(
        f"{HEADER}\n\n"
        "🟢 Online | 🔴 Offline\n"
        "Select a server:",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def view_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Server detail view with live stats"""
    query = update.callback_query
    await query.answer()
    server_id = query.data.split('_')[1]
    server = db.db.servers.find_one({"_id": server_id})
    
    if not server:
        await query.edit_message_text("❌ Server not found!")
        return
    
    # Live system check
    try:
        ssh = await db.get_ssh(server)
        stdin, stdout, stderr = ssh.exec_command("""
            echo -n '🖥️ Hostname: '; hostname;
            echo -n '⏱️ Uptime: '; uptime -p;
            echo -n '🧠 Memory: '; free -m | awk '/Mem:/{printf "%.1f%% of %sMB", $3*100/$2, $2}';
            echo -n '💾 Disk: '; df -h / --output=pcent | tail -n1
        """, timeout=5)
        stats = stdout.read().decode()
        status = "🟢 Online"
    except Exception as e:
        stats = f"🔴 Connection failed: {str(e)}"
        status = "🔴 Offline"
    
    await query.edit_message_text(
        f"{HEADER}\n\n"
        f"🔧 *{server['name']}*\n"
        f"`{server['ip']}` | {status}\n\n"
        f"{stats}{DIVIDER}",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📁 Files", callback_data=f"files_{server_id}"),
             InlineKeyboardButton("🤖 Bots", callback_data=f"bots_{server_id}")],
            [InlineKeyboardButton("⚙️ Edit", callback_data=f"edit_{server_id}"),
             InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_{server_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_servers")]
        ])
    )

async def delete_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete confirmation dialog"""
    query = update.callback_query
    await query.answer()
    server_id = query.data.split('_')[1]
    
    await query.edit_message_text(
        f"⚠️ *Confirm Deletion*\n\n"
        f"Are you sure you want to delete this server?{DIVIDER}",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, delete", callback_data=f"confirm_delete_{server_id}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"view_{server_id}")]
        ])
    )

async def confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute server deletion"""
    query = update.callback_query
    await query.answer()
    server_id = query.data.split('_')[2]
    
    db.db.servers.delete_one({"_id": server_id})
    await query.edit_message_text(
        "✅ Server deleted successfully!",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 My Servers", callback_data="back_servers")]
        ])
    )

# Server Addition Flow
async def add_server_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"{HEADER}\n\n"
        "Let's add a new server!\n\n"
        "📝 *Step 1/4*: Send me the server name\n"
        "(e.g. 'Production Web Server')",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardMarkup([['❌ Cancel']], resize_keyboard=True)
    )
    return NAME

async def add_server_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not re.match(r"^[\w\s-]{3,50}$", update.message.text):
        await update.message.reply_text("❌ Invalid name! Use 3-50 characters (letters, numbers, spaces)")
        return NAME
    
    context.user_data['name'] = update.message.text
    await update.message.reply_text(
        "📝 *Step 2/4*: Enter server IP address\n"
        "(e.g. '192.168.1.100')",
        parse_mode='Markdown'
    )
    return IP

async def add_server_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", update.message.text):
        await update.message.reply_text("❌ Invalid IP format! Use IPv4 like '192.168.1.100'")
        return IP
    
    context.user_data['ip'] = update.message.text
    await update.message.reply_text(
        "📝 *Step 3/4*: Enter SSH username\n"
        "(e.g. 'ubuntu')",
        parse_mode='Markdown'
    )
    return USER

async def add_server_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not re.match(r"^[a-z_][a-z0-9_-]{0,31}$", update.message.text):
        await update.message.reply_text("❌ Invalid username! Use lowercase letters, numbers, underscores")
        return USER
    
    context.user_data['user'] = update.message.text
    await update.message.reply_text(
        "📝 *Step 4/4*: Upload your SSH private key file\n"
        "(Send the .pem or .key file)",
        parse_mode='Markdown'
    )
    return KEY

async def add_server_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        await update.message.reply_text("❌ Please upload a key file!")
        return KEY
    
    file = await update.message.document.get_file()
    key_content = (await file.download_as_bytearray()).decode('utf-8')
    
    # Validate key
    try:
        key = paramiko.RSAKey.from_private_key(StringIO(key_content))
    except Exception as e:
        await update.message.reply_text(f"❌ Invalid SSH key: {str(e)}")
        return KEY
    
    server = {
        'user_id': update.effective_user.id,
        'name': context.user_data['name'],
        'ip': context.user_data['ip'],
        'username': context.user_data['user'],
        'ssh_key': key_content,
        'created_at': time.time()
    }
    
    # Test connection immediately
    try:
        ssh = await db.get_ssh(server)
        stdin, stdout, stderr = ssh.exec_command("echo 'Connection successful!'", timeout=5)
        db.db.servers.insert_one(server)
        
        await update.message.reply_text(
            f"✅ *Server Added Successfully!*\n\n"
            f"Name: {server['name']}\n"
            f"IP: `{server['ip']}`\n"
            f"Status: 🟢 Online",
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardMarkup(
                [['📋 My Servers']],
                resize_keyboard=True
            )
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ *Connection Failed!*\n\n"
            f"Error: {str(e)}\n\n"
            "Please check your details and try again.",
            parse_mode='Markdown'
        )
    finally:
        context.user_data.clear()
        return ConversationHandler.END

async def cancel_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Server addition cancelled.",
        reply_markup=ReplyKeyboardMarkup(
            [['📋 My Servers', '➕ Add Server']],
            resize_keyboard=True
        )
    )
    return ConversationHandler.END

def setup_handlers(application):
    # Main commands
    application.add_handler(MessageHandler(filters.Regex('^📋 My Servers$'), list_servers))
    
    # Server actions
    application.add_handler(CallbackQueryHandler(view_server, pattern="^view_"))
    application.add_handler(CallbackQueryHandler(delete_server, pattern="^delete_"))
    application.add_handler(CallbackQueryHandler(confirm_delete, pattern="^confirm_delete_"))
    application.add_handler(CallbackQueryHandler(list_servers, pattern="^back_servers$"))
    
    # Add server conversation
    application.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^➕ Add Server$'), add_server_start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_server_name)],
            IP: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_server_ip)],
            USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_server_user)],
            KEY: [MessageHandler(filters.Document.ALL, add_server_key)]
        },
        fallbacks=[MessageHandler(filters.Regex('^❌ Cancel$'), cancel_add)]
    ))
