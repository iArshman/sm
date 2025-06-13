from telegram import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup
from telegram.ext import MessageHandler, Filters, CallbackQueryHandler, ConversationHandler
from db import db
import time

# Edit server states
EDIT_CHOOSE, EDIT_NAME, EDIT_IP, EDIT_USER = range(4)

def setup_handlers(dp):
    dp.add_handler(MessageHandler(Filters.regex('^My Servers$'), list_servers))
    dp.add_handler(CallbackQueryHandler(handle_action, pattern='^srv:'))
    
    # Edit server conversation
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_edit, pattern='^srv:edit:')],
        states={
            EDIT_CHOOSE: [CallbackQueryHandler(choose_edit_field)],
            EDIT_NAME: [MessageHandler(Filters.text, save_name)],
            EDIT_IP: [MessageHandler(Filters.text, save_ip)],
            EDIT_USER: [MessageHandler(Filters.text, save_user)]
        },
        fallbacks=[]
    )
    dp.add_handler(conv_handler)

def list_servers(update, context):
    servers = list(db.db.servers.find({"user_id": update.effective_user.id}))
    
    buttons = [
        [InlineKeyboardButton(
            f"{get_status(server)} {server['name']} ({server['ip']})", 
            callback_data=f"srv:menu:{server['_id']}"
        )]
        for server in servers
    ]
    
    update.message.reply_text(
        "Your servers: 🟢 Online | 🔴 Offline",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

def get_status(server):
    try:
        ssh = db.get_ssh(server)
        ssh.exec_command("exit", timeout=1)
        return "🟢"
    except:
        return "🔴"

def handle_action(update, context):
    query = update.callback_query
    query.answer()
    data = query.data.split(':')
    
    if data[1] == 'menu':
        show_server_menu(query, data[2])
    elif data[1] == 'info':
        show_server_info(query, data[2])
    elif data[1] == 'delete':
        confirm_delete(query, data[2])
    elif data[1] == 'confirm_delete':
        delete_server(query, data[2])

def show_server_menu(query, server_id):
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
    
    query.edit_message_text(
        f"⚙️ Managing: {server['name']}\nIP: {server['ip']}\nUser: {server['username']}",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

def show_server_info(query, server_id):
    server = db.db.servers.find_one({"_id": server_id})
    
    try:
        ssh = db.get_ssh(server)
        cmd = """
        echo -n '🖥️ Hostname: '; hostname;
        echo -n '🔄 Uptime: '; uptime -p;
        echo -n '💾 Disk: '; df -h / --output=used,size,pcent | tail -n1 | awk '{print $1"/"$2" ("$3")"}';
        echo -n '🧠 Memory: '; free -m | awk '/Mem:/{printf "%.1f%% of %sMB", $3*100/$2, $2}';
        echo -n '🔥 CPU: '; top -bn1 | grep 'Cpu(s)' | awk '{printf "%.1f%%", 100 - $8}';
        echo -n '👟 Processes: '; ps aux | wc -l
        """
        stdin, stdout, stderr = ssh.exec_command(cmd, timeout=3)
        info = stdout.read().decode()
        
        query.edit_message_text(
            f"📊 {server['name']} Status\n\n{info}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data=f"srv:menu:{server_id}")
            ]])
        )
    except Exception as e:
        query.edit_message_text(f"❌ Error: {str(e)}")

def confirm_delete(query, server_id):
    buttons = [
        [
            InlineKeyboardButton("✅ Yes, delete", callback_data=f"srv:confirm_delete:{server_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"srv:menu:{server_id}")
        ]
    ]
    query.edit_message_text(
        "⚠️ Are you sure you want to delete this server?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

def delete_server(query, server_id):
    db.db.servers.delete_one({"_id": server_id})
    query.edit_message_text("✅ Server deleted successfully!")

# Edit server functions
def start_edit(update, context):
    query = update.callback_query
    query.answer()
    server_id = query.data.split(':')[2]
    context.user_data['edit_server'] = server_id
    
    buttons = [
        [InlineKeyboardButton("✏️ Change Name", callback_data="edit:name")],
        [InlineKeyboardButton("🌐 Change IP", callback_data="edit:ip")],
        [InlineKeyboardButton("👤 Change User", callback_data="edit:user")],
        [InlineKeyboardButton("🔙 Cancel", callback_data=f"srv:menu:{server_id}")]
    ]
    
    query.edit_message_text(
        "What do you want to edit?",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
    return EDIT_CHOOSE

def choose_edit_field(update, context):
    query = update.callback_query
    query.answer()
    choice = query.data.split(':')[1]
    
    if choice == 'name':
        query.edit_message_text("Enter new server name:")
        return EDIT_NAME
    elif choice == 'ip':
        query.edit_message_text("Enter new server IP:")
        return EDIT_IP
    elif choice == 'user':
        query.edit_message_text("Enter new SSH username:")
        return EDIT_USER

def save_name(update, context):
    server_id = context.user_data['edit_server']
    db.db.servers.update_one(
        {"_id": server_id},
        {"$set": {"name": update.message.text}}
    )
    update.message.reply_text("✅ Server name updated!")
    return ConversationHandler.END

def save_ip(update, context):
    server_id = context.user_data['edit_server']
    db.db.servers.update_one(
        {"_id": server_id},
        {"$set": {"ip": update.message.text}}
    )
    update.message.reply_text("✅ Server IP updated!")
    return ConversationHandler.END

def save_user(update, context):
    server_id = context.user_data['edit_server']
    db.db.servers.update_one(
        {"_id": server_id},
        {"$set": {"username": update.message.text}}
    )
    update.message.reply_text("✅ SSH username updated!")
    return ConversationHandler.END
