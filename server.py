from telegram import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters
)
from db import db
import time

# Edit server states
EDIT_CHOOSE, EDIT_NAME, EDIT_IP, EDIT_USER = range(4)

async def list_servers(update, context):
    servers = list(db.db.servers.find({"user_id": update.effective_user.id}))
    
    buttons = [
        [InlineKeyboardButton(
            f"{await get_status(server)} {server['name']} ({server['ip']})", 
            callback_data=f"srv:menu:{server['_id']}"
        )]
        for server in servers
    ]
    
    await update.message.reply_text(
        "Your servers: 🟢 Online | 🔴 Offline",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def get_status(server):
    try:
        ssh = await db.get_ssh(server)
        ssh.exec_command("exit", timeout=1)
        return "🟢"
    except:
        return "🔴"

async def handle_action(update, context):
    query = update.callback_query
    await query.answer()
    data = query.data.split(':')
    
    if data[1] == 'menu':
        await show_server_menu(query, data[2])
    elif data[1] == 'info':
        await show_server_info(query, data[2])
    elif data[1] == 'delete':
        await confirm_delete(query, data[2])
    elif data[1] == 'confirm_delete':
        await delete_server(query, data[2])

async def show_server_menu(query, server_id):
    server = db.db.servers.find_one({"_id": server_id})
    
    buttons = [
        [InlineKeyboardButton("📁 File Manager", callback_data=f"srv:files:{server_id}")],
        [InlineKeyboardButton("🤖 Bot Manager", callback_data=f"srv:bots:{server_id}")],
        [InlineKeyboardButton("ℹ️ Server Info", callback_data=f"srv:info:{server_id}")],
        [
            InlineKeyboardButton("✏️ Edit Server", callback_data=f"srv:edit:{server_id}"),
            InlineKeyboardButton("🗑️ Delete", callback_data=f"srv:delete:{server_id}")
        ]
    ]
    
    await query.edit_message_text(
        f"⚙️ Managing: {server['name']}\nIP: {server['ip']}\nUser: {server['username']}",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def show_server_info(query, server_id):
    server = db.db.servers.find_one({"_id": server_id})
    
    try:
        ssh = await db.get_ssh(server)
        stdin, stdout, stderr = ssh.exec_command("""
            echo -n '🖥️ Hostname: '; hostname;
            echo -n '🔄 Uptime: '; uptime -p;
            echo -n '💾 Disk: '; df -h / --output=used,size,pcent | tail -n1;
            echo -n '🧠 Memory: '; free -m | awk '/Mem:/{print $3"/"$2 " ("$3/$2*100"%)"}';
            echo -n '🔥 CPU: '; top -bn1 | grep 'Cpu(s)' | awk '{print $2+$4"%"}'
        """, timeout=3)
        info = stdout.read().decode()
        
        await query.edit_message_text(
            f"📊 {server['name']} Status\n\n{info}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data=f"srv:menu:{server_id}")
            ]])
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Error: {str(e)}")

async def confirm_delete(query, server_id):
    buttons = [
        [InlineKeyboardButton("✅ Yes, delete", callback_data=f"srv:confirm_delete:{server_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"srv:menu:{server_id}")]
    ]
    await query.edit_message_text(
        "⚠️ Are you sure you want to delete this server?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def delete_server(query, server_id):
    db.db.servers.delete_one({"_id": server_id})
    await query.edit_message_text("✅ Server deleted successfully!")

# Edit server flow
async def start_edit(update, context):
    query = update.callback_query
    await query.answer()
    context.user_data['edit_server'] = query.data.split(':')[2]
    
    buttons = [
        [InlineKeyboardButton("✏️ Change Name", callback_data="edit:name")],
        [InlineKeyboardButton("🌐 Change IP", callback_data="edit:ip")],
        [InlineKeyboardButton("👤 Change User", callback_data="edit:user")],
        [InlineKeyboardButton("🔙 Cancel", callback_data=f"srv:menu:{context.user_data['edit_server']}")]
    ]
    
    await query.edit_message_text(
        "What do you want to edit?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return EDIT_CHOOSE

async def choose_edit_field(update, context):
    query = update.callback_query
    await query.answer()
    choice = query.data.split(':')[1]
    
    if choice == 'name':
        await query.edit_message_text("Enter new server name:")
        return EDIT_NAME
    elif choice == 'ip':
        await query.edit_message_text("Enter new server IP:")
        return EDIT_IP
    elif choice == 'user':
        await query.edit_message_text("Enter new SSH username:")
        return EDIT_USER

async def save_name(update, context):
    db.db.servers.update_one(
        {"_id": context.user_data['edit_server']},
        {"$set": {"name": update.message.text}}
    )
    await update.message.reply_text("✅ Server name updated!")
    return ConversationHandler.END

async def save_ip(update, context):
    db.db.servers.update_one(
        {"_id": context.user_data['edit_server']},
        {"$set": {"ip": update.message.text}}
    )
    await update.message.reply_text("✅ Server IP updated!")
    return ConversationHandler.END

async def save_user(update, context):
    db.db.servers.update_one(
        {"_id": context.user_data['edit_server']},
        {"$set": {"username": update.message.text}}
    )
    await update.message.reply_text("✅ SSH username updated!")
    return ConversationHandler.END

def setup_handlers(application):
    application.add_handler(MessageHandler(filters.Regex('^My Servers$'), list_servers))
    application.add_handler(CallbackQueryHandler(handle_action, pattern='^srv:'))
    
    # Edit server conversation
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_edit, pattern='^srv:edit:')],
        states={
            EDIT_CHOOSE: [CallbackQueryHandler(choose_edit_field)],
            EDIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_name)],
            EDIT_IP: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_ip)],
            EDIT_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_user)]
        },
        fallbacks=[]
    )
    application.add_handler(conv_handler)
