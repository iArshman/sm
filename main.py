import logging
import io
import os
import sys
from pathlib import Path
import paramiko
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.utils import executor
from datetime import datetime

# Add the project root to Python path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

try:
    from bot.config import BOT_TOKEN, LOG_LEVEL
    from bot.db import (
        add_server,
        get_servers,
        get_server_by_id,
        update_server_name,
        update_server_username,
        delete_server_by_id
    )
    from bot.file_manager import init_file_manager
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Make sure all required files are in the correct location")
    sys.exit(1)

from bson.objectid import ObjectId
from bson.errors import InvalidId

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize bot and dispatcher
try:
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(bot)
    dp.middleware.setup(LoggingMiddleware())
    logger.info("✅ Bot initialized successfully")
except Exception as e:
    logger.error(f"❌ Failed to initialize bot: {e}")
    sys.exit(1)

# --- UTILS ---

def cancel_button():
    """Create cancel button"""
    return InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Cancel", callback_data="cancel"))

def back_button(to):
    """Create back button"""
    return InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Back", callback_data=to))

def format_uptime(seconds):
    """Convert seconds to human-readable uptime"""
    if not isinstance(seconds, (int, float)) or seconds < 0:
        return "Unknown"
    
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    
    return ", ".join(parts) or "Less than a minute"

def format_size(size_str):
    """Format file size for better display"""
    try:
        size = float(size_str)
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"
    except:
        return size_str

# --- GLOBAL STATE ---
user_input = {}
active_sessions = {}  # Store SSH sessions: {server_id: SSHClient}

# --- SSH SESSION MANAGEMENT ---

def get_ssh_session(server_id, ip, username, key_content):
    """Get or create SSH session"""
    logger.info(f"Getting SSH session for server {server_id} ({ip})")
    
    try:
        # Check if existing session is still active
        if server_id in active_sessions:
            try:
                transport = active_sessions[server_id].get_transport()
                if transport and transport.is_active():
                    logger.info(f"Reusing existing SSH session for {server_id}")
                    return active_sessions[server_id]
                else:
                    # Clean up dead session
                    logger.info(f"Cleaning up dead SSH session for {server_id}")
                    active_sessions.pop(server_id, None)
            except:
                active_sessions.pop(server_id, None)
        
        # Create new session
        key_file = io.StringIO(key_content)
        ssh_key = None
        
        # Try different key types
        for key_class in [paramiko.RSAKey, paramiko.ECDSAKey, paramiko.Ed25519Key, paramiko.DSSKey]:
            try:
                key_file.seek(0)
                ssh_key = key_class.from_private_key(key_file)
                break
            except paramiko.SSHException:
                continue
        
        if not ssh_key:
            raise paramiko.SSHException("Unsupported or invalid key format")
        
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(ip, username=username, pkey=ssh_key, timeout=15)
        
        active_sessions[server_id] = ssh
        logger.info(f"Created new SSH session for {server_id}")
        return ssh
        
    except Exception as e:
        logger.error(f"Failed to create SSH session for {ip}: {e}")
        raise

def close_ssh_session(server_id):
    """Close SSH session"""
    if server_id in active_sessions:
        try:
            active_sessions[server_id].close()
        except:
            pass
        active_sessions.pop(server_id, None)
        logger.info(f"Closed SSH session for {server_id}")

# --- SERVER STATS ---

def get_remote_stats(server_id, ip, username, key_content):
    """Fetch remote server statistics"""
    logger.info(f"Fetching stats for {ip} with user {username}")
    
    try:
        ssh = get_ssh_session(server_id, ip, username, key_content)
        stats = {"error": None}
        
        # OS Information
        try:
            stdin, stdout, stderr = ssh.exec_command("cat /etc/os-release 2>/dev/null || echo 'NAME=Unknown'")
            os_release = stdout.read().decode().strip()
            
            os_dict = {}
            for line in os_release.splitlines():
                if '=' in line:
                    key, value = line.split('=', 1)
                    os_dict[key.strip()] = value.strip().strip('"')
            
            distro = os_dict.get('PRETTY_NAME', os_dict.get('NAME', 'Unknown'))
            
            stdin, stdout, stderr = ssh.exec_command("uname -r 2>/dev/null || echo 'Unknown'")
            kernel = stdout.read().decode().strip() or "Unknown"
            
            stats['os'] = f"{distro}, Kernel {kernel}"
            
        except Exception as e:
            logger.error(f"OS info error: {e}")
            stats['os'] = "Unknown"
        
        # Uptime
        try:
            stdin, stdout, stderr = ssh.exec_command("cat /proc/uptime 2>/dev/null")
            uptime_data = stdout.read().decode().strip()
            
            if uptime_data:
                uptime_seconds = float(uptime_data.split()[0])
                stats['uptime'] = format_uptime(uptime_seconds)
            else:
                stats['uptime'] = "Unknown"
                
        except Exception as e:
            logger.error(f"Uptime error: {e}")
            stats['uptime'] = "Unknown"
        
        # Memory
        try:
            stdin, stdout, stderr = ssh.exec_command("free -m 2>/dev/null")
            mem_lines = stdout.read().decode().splitlines()
            
            if len(mem_lines) > 1:
                mem_data = mem_lines[1].split()
                stats['ram_total'] = round(int(mem_data[1]) / 1024, 2)
                stats['ram_used'] = round(int(mem_data[2]) / 1024, 2)
            else:
                stats['ram_total'] = stats['ram_used'] = "Unknown"
                
        except Exception as e:
            logger.error(f"Memory error: {e}")
            stats['ram_total'] = stats['ram_used'] = "Unknown"
        
        # Disk
        try:
            stdin, stdout, stderr = ssh.exec_command("df -h / 2>/dev/null")
            disk_lines = stdout.read().decode().splitlines()
            
            if len(disk_lines) > 1:
                disk_data = disk_lines[1].split()
                stats['disk_total'] = disk_data[1]
                stats['disk_used'] = disk_data[2]
            else:
                stats['disk_total'] = stats['disk_used'] = "Unknown"
                
        except Exception as e:
            logger.error(f"Disk error: {e}")
            stats['disk_total'] = stats['disk_used'] = "Unknown"
        
        # CPU Usage
        try:
            stdin, stdout, stderr = ssh.exec_command("top -bn1 | grep 'Cpu(s)' 2>/dev/null")
            cpu_line = stdout.read().decode().strip()
            
            if cpu_line:
                # Parse CPU usage from top output
                import re
                idle_match = re.search(r'(\d+\.?\d*)%?\s*id', cpu_line)
                if idle_match:
                    cpu_idle = float(idle_match.group(1))
                    stats['cpu_usage'] = round(100 - cpu_idle, 2)
                else:
                    stats['cpu_usage'] = "Unknown"
            else:
                stats['cpu_usage'] = "Unknown"
                
        except Exception as e:
            logger.error(f"CPU error: {e}")
            stats['cpu_usage'] = "Unknown"
        
        return stats
        
    except Exception as e:
        logger.error(f"SSH error for {ip}: {e}")
        return {"error": str(e)}

# --- STARTUP HOOK ---

async def on_startup(_):
    """Initialize bot on startup"""
    logger.info("🚀 Bot starting up...")
    
    try:
        servers = await get_servers()
        logger.info(f"Found {len(servers)} servers in database")
        
        # Pre-connect to all servers
        for server in servers:
            server_id = str(server['_id'])
            try:
                get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
                logger.info(f"✅ Connected to {server['name']} ({server['ip']})")
            except Exception as e:
                logger.error(f"❌ Failed to connect to {server['name']} ({server['ip']}): {e}")
    
    except Exception as e:
        logger.error(f"Startup error: {e}")
    
    # Initialize file manager
    init_file_manager(dp, bot, active_sessions, user_input)
    logger.info("✅ Bot startup complete")

# --- MAIN HANDLERS ---

@dp.message_handler(commands=['start'])
async def start_command(message: types.Message):
    """Handle /start command"""
    try:
        servers = await get_servers()
        
        if not servers:
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("➕ Add Your First Server", callback_data="add_server"))
            
            await message.answer(
                "🔧 <b>Multi Server Manager</b>\n\n"
                "Welcome! You don't have any servers configured yet.\n"
                "Add your first server to get started.",
                parse_mode='HTML',
                reply_markup=kb
            )
            return
        
        kb = InlineKeyboardMarkup(row_width=1)
        
        for server in servers:
            # Check server status
            server_id = str(server['_id'])
            status_icon = "🟢" if server_id in active_sessions else "🔴"
            
            kb.add(InlineKeyboardButton(
                f"{status_icon} {server['name']} ({server['ip']})",
                callback_data=f"server_{server['_id']}"
            ))
        
        kb.add(InlineKeyboardButton("➕ Add Server", callback_data="add_server"))
        
        await message.answer(
            "🔧 <b>Multi Server Manager</b>\n\n"
            "Select a server to manage:",
            parse_mode='HTML',
            reply_markup=kb
        )
        
    except Exception as e:
        logger.error(f"Start command error: {e}")
        await message.answer("❌ Error loading servers. Please try again.")

@dp.callback_query_handler(lambda c: c.data == "start")
async def back_to_start(callback: types.CallbackQuery):
    """Return to main menu"""
    try:
        await callback.message.delete()
        await start_command(callback.message)
        
    except Exception as e:
        logger.error(f"Back to start error: {e}")
        await callback.message.edit_text("❌ Error returning to main menu.")

@dp.callback_query_handler(lambda c: c.data == "cancel")
async def cancel_action(callback: types.CallbackQuery):
    """Cancel current action"""
    user_input.pop(callback.from_user.id, None)
    await callback.message.delete()
    await start_command(callback.message)

# --- SERVER MANAGEMENT ---

@dp.callback_query_handler(lambda c: c.data == "add_server")
async def add_server_start(callback: types.CallbackQuery):
    """Start adding a new server"""
    user_input[callback.from_user.id] = {'step': 'name'}
    
    await bot.send_message(
        callback.from_user.id,
        "📝 <b>Add New Server</b>\n\nEnter server name:",
        parse_mode='HTML',
        reply_markup=cancel_button()
    )

@dp.message_handler(lambda message: message.from_user.id in user_input and user_input[message.from_user.id].get('step'))
async def handle_server_inputs(message: types.Message):
    """Handle server configuration inputs"""
    uid = message.from_user.id
    if uid not in user_input or 'step' not in user_input[uid]:
        return
    
    step = user_input[uid]['step']
    logger.info(f"Handling server input for user {uid}, step: {step}")
    
    try:
        if step == 'name':
            user_input[uid]['name'] = message.text.strip()
            user_input[uid]['step'] = 'username'
            await message.answer(
                "👤 <b>Server Username</b>\n\nEnter SSH username:",
                parse_mode='HTML',
                reply_markup=cancel_button()
            )
            
        elif step == 'username':
            user_input[uid]['username'] = message.text.strip()
            user_input[uid]['step'] = 'ip'
            await message.answer(
                "🌐 <b>Server IP Address</b>\n\nEnter IP address or hostname:",
                parse_mode='HTML',
                reply_markup=cancel_button()
            )
            
        elif step == 'ip':
            user_input[uid]['ip'] = message.text.strip()
            user_input[uid]['step'] = 'key'
            await message.answer(
                "🔑 <b>SSH Private Key</b>\n\nSend your SSH private key file:",
                parse_mode='HTML',
                reply_markup=cancel_button()
            )
            
    except Exception as e:
        logger.error(f"Server input error: {e}")
        await message.answer("❌ Error processing input. Please try again.")

@dp.message_handler(content_types=types.ContentType.DOCUMENT)
async def handle_key_upload(message: types.Message):
    """Handle SSH key file upload"""
    uid = message.from_user.id
    
    # Check if this is for server setup
    if uid not in user_input or user_input[uid].get('step') != 'key':
        return
    
    try:
        await message.answer("🔄 Processing SSH key...")
        
        # Download and read key file
        file = await bot.download_file_by_id(message.document.file_id)
        key_content = file.read().decode('utf-8')
        
        data = user_input[uid]
        data['key_content'] = key_content
        
        await message.answer("🔌 Testing connection...")
        
        # Test SSH connection
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            key_file = io.StringIO(key_content)
            ssh_key = None
            
            # Try different key types
            for key_class in [paramiko.RSAKey, paramiko.ECDSAKey, paramiko.Ed25519Key, paramiko.DSSKey]:
                try:
                    key_file.seek(0)
                    ssh_key = key_class.from_private_key(key_file)
                    break
                except paramiko.SSHException:
                    continue
            
            if not ssh_key:
                raise ValueError("Invalid or unsupported key format")
            
            ssh.connect(data['ip'], username=data['username'], pkey=ssh_key, timeout=15)
            ssh.close()
            
            # Save server to database
            await add_server(data)
            
            # Get the new server ID and establish session
            servers = await get_servers()
            new_server = servers[-1]  # Get the last added server
            server_id = str(new_server['_id'])
            
            try:
                get_ssh_session(server_id, data['ip'], data['username'], key_content)
            except Exception as e:
                logger.error(f"Failed to establish session for new server {server_id}: {e}")
            
            await message.answer(
                f"✅ <b>Server Added Successfully!</b>\n\n"
                f"📝 Name: {data['name']}\n"
                f"👤 Username: {data['username']}\n"
                f"🌐 IP: {data['ip']}\n\n"
                f"Connection test passed!",
                parse_mode='HTML'
            )
            
        except Exception as e:
            logger.error(f"SSH connection test failed: {e}")
            await message.answer(
                f"❌ <b>Connection Failed</b>\n\n"
                f"Error: {str(e)}\n\n"
                f"Please check your credentials and try again.",
                parse_mode='HTML'
            )
        
        user_input.pop(uid, None)
        await start_command(message)
        
    except Exception as e:
        logger.error(f"Key upload error: {e}")
        await message.answer("❌ Error processing key file. Please try again.")

# --- SERVER MENU ---

@dp.callback_query_handler(lambda c: c.data.startswith("server_"))
async def view_server(callback: types.CallbackQuery):
    """Show server management menu"""
    try:
        server_id = callback.data.split('_')[1]
        server = await get_server_by_id(server_id)
        
        if not server:
            await callback.message.edit_text("❌ Server not found.")
            return
        
        logger.info(f"Viewing server {server_id}: {server['name']}")
        
        # Check connection status
        status_icon = "🟢" if server_id in active_sessions else "🔴"
        status_text = "Online" if server_id in active_sessions else "Offline"
        
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("🗂 File Manager", callback_data=f"file_manager_{server_id}"),
            InlineKeyboardButton("📊 Server Info", callback_data=f"info_{server_id}")
        )
        kb.add(
            InlineKeyboardButton("🤖 Bot Manager", callback_data=f"bot_manager_{server_id}"),
            InlineKeyboardButton("⚙️ Settings", callback_data=f"edit_{server_id}")
        )
        kb.add(InlineKeyboardButton("⬅️ Back to Servers", callback_data="start"))
        
        text = (
            f"🖥 <b>{server['name']}</b>\n\n"
            f"👤 Username: <code>{server['username']}</code>\n"
            f"🌐 IP Address: <code>{server['ip']}</code>\n"
            f"{status_icon} Status: <b>{status_text}</b>\n\n"
            f"Choose an option:"
        )
        
        await callback.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
        
    except Exception as e:
        logger.error(f"View server error: {e}")
        await callback.message.edit_text("❌ Error loading server details.")

# --- SERVER INFO ---

@dp.callback_query_handler(lambda c: c.data.startswith("info_"))
async def server_info(callback: types.CallbackQuery):
    """Show detailed server information"""
    try:
        server_id = callback.data.split('_')[1]
        server = await get_server_by_id(server_id)
        
        if not server:
            await callback.message.edit_text("❌ Server not found.")
            return
        
        await callback.message.edit_text("📊 <b>Fetching server statistics...</b>", parse_mode="HTML")
        
        stats = get_remote_stats(server_id, server['ip'], server['username'], server['key_content'])
        
        if stats.get('error'):
            text = (
                f"🖥 <b>{server['name']}</b>\n\n"
                f"👤 Username: <code>{server['username']}</code>\n"
                f"🌐 IP Address: <code>{server['ip']}</code>\n\n"
                f"❌ <b>Error fetching statistics:</b>\n"
                f"<code>{stats['error']}</code>"
            )
        else:
            # Format memory usage
            ram_usage = "Unknown"
            if stats['ram_total'] != "Unknown" and stats['ram_used'] != "Unknown":
                ram_percent = (stats['ram_used'] / stats['ram_total']) * 100
                ram_usage = f"{stats['ram_used']} GB / {stats['ram_total']} GB ({ram_percent:.1f}%)"
            
            text = (
                f"🖥 <b>{server['name']}</b>\n\n"
                f"👤 Username: <code>{server['username']}</code>\n"
                f"🌐 IP Address: <code>{server['ip']}</code>\n\n"
                f"💻 <b>System Information:</b>\n"
                f"OS: {stats['os']}\n"
                f"⏱ Uptime: {stats['uptime']}\n\n"
                f"📊 <b>Resource Usage:</b>\n"
                f"🧠 Memory: {ram_usage}\n"
                f"💾 Disk: {stats['disk_used']} / {stats['disk_total']}\n"
                f"🔥 CPU Usage: {stats['cpu_usage']}%"
            )
        
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=back_button(f"server_{server_id}")
        )
        
    except Exception as e:
        logger.error(f"Server info error: {e}")
        await callback.message.edit_text("❌ Error fetching server information.")

# --- BOT MANAGER PLACEHOLDER ---

@dp.callback_query_handler(lambda c: c.data.startswith("bot_manager_"))
async def bot_manager(callback: types.CallbackQuery):
    """Bot manager placeholder"""
    server_id = callback.data.split('_')[2]
    
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"server_{server_id}"))
    
    await callback.message.edit_text(
        "🤖 <b>Bot Manager</b>\n\n"
        "This feature is coming soon!\n\n"
        "Future capabilities:\n"
        "• Deploy and manage bots\n"
        "• Monitor bot status\n"
        "• View logs and metrics\n"
        "• Auto-restart functionality",
        parse_mode='HTML',
        reply_markup=kb
    )

# --- SERVER SETTINGS ---

@dp.callback_query_handler(lambda c: c.data.startswith("edit_"))
async def edit_server(callback: types.CallbackQuery):
    """Show server settings menu"""
    try:
        server_id = callback.data.split('_')[1]
        server = await get_server_by_id(server_id)
        
        if not server:
            await callback.message.edit_text("❌ Server not found.")
            return
        
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("✏️ Rename", callback_data=f"rename_{server_id}"),
            InlineKeyboardButton("👤 Change Username", callback_data=f"reuser_{server_id}")
        )
        kb.add(
            InlineKeyboardButton("🔄 Reconnect", callback_data=f"reconnect_{server_id}"),
            InlineKeyboardButton("🗑 Delete Server", callback_data=f"delete_{server_id}")
        )
        kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"server_{server_id}"))
        
        await callback.message.edit_text(
            f"⚙️ <b>Server Settings</b>\n\n"
            f"Managing: <b>{server['name']}</b>",
            parse_mode='HTML',
            reply_markup=kb
        )
        
    except Exception as e:
        logger.error(f"Edit server error: {e}")
        await callback.message.edit_text("❌ Error loading settings menu.")

# --- RECONNECT SERVER ---

@dp.callback_query_handler(lambda c: c.data.startswith("reconnect_"))
async def reconnect_server(callback: types.CallbackQuery):
    """Reconnect to server"""
    try:
        server_id = callback.data.split('_')[1]
        server = await get_server_by_id(server_id)
        
        if not server:
            await callback.message.edit_text("❌ Server not found.")
            return
        
        await callback.message.edit_text("🔄 <b>Reconnecting...</b>", parse_mode='HTML')
        
        # Close existing session
        close_ssh_session(server_id)
        
        try:
            # Create new session
            get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            
            await callback.message.edit_text(
                f"✅ <b>Reconnected Successfully!</b>\n\n"
                f"Server: {server['name']}\n"
                f"IP: {server['ip']}",
                parse_mode='HTML',
                reply_markup=back_button(f"edit_{server_id}")
            )
            
        except Exception as e:
            await callback.message.edit_text(
                f"❌ <b>Reconnection Failed</b>\n\n"
                f"Error: {str(e)}",
                parse_mode='HTML',
                reply_markup=back_button(f"edit_{server_id}")
            )
            
    except Exception as e:
        logger.error(f"Reconnect error: {e}")
        await callback.message.edit_text("❌ Error during reconnection.")

# --- RENAME SERVER ---

@dp.callback_query_handler(lambda c: c.data.startswith("rename_"))
async def rename_server(callback: types.CallbackQuery):
    """Start server rename process"""
    try:
        server_id = callback.data.split('_')[1]
        user_input[callback.from_user.id] = {'edit': 'name', 'id': server_id}
        
        await bot.send_message(
            callback.from_user.id,
            "✏️ <b>Rename Server</b>\n\nEnter new server name:",
            parse_mode='HTML',
            reply_markup=cancel_button()
        )
        
    except Exception as e:
        logger.error(f"Rename server error: {e}")
        await callback.message.edit_text("❌ Error initiating rename.")

# --- CHANGE USERNAME ---

@dp.callback_query_handler(lambda c: c.data.startswith("reuser_"))
async def change_username(callback: types.CallbackQuery):
    """Start username change process"""
    try:
        server_id = callback.data.split('_')[1]
        user_input[callback.from_user.id] = {'edit': 'username', 'id': server_id}
        
        await bot.send_message(
            callback.from_user.id,
            "👤 <b>Change Username</b>\n\nEnter new SSH username:",
            parse_mode='HTML',
            reply_markup=cancel_button()
        )
        
    except Exception as e:
        logger.error(f"Change username error: {e}")
        await callback.message.edit_text("❌ Error initiating username change.")

# --- DELETE SERVER ---

@dp.callback_query_handler(lambda c: c.data.startswith("delete_") and not c.data.startswith("delete_confirm_"))
async def confirm_delete_server(callback: types.CallbackQuery):
    """Confirm server deletion"""
    try:
        server_id = callback.data.split('_')[1]
        server = await get_server_by_id(server_id)
        
        if not server:
            await callback.message.edit_text("❌ Server not found.")
            return
        
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("✅ Yes, Delete", callback_data=f"delete_confirm_{server_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"edit_{server_id}")
        )
        
        await callback.message.edit_text(
            f"⚠️ <b>Confirm Deletion</b>\n\n"
            f"Are you sure you want to delete server:\n"
            f"<b>{server['name']}</b> ({server['ip']})\n\n"
            f"<b>This action cannot be undone!</b>",
            parse_mode='HTML',
            reply_markup=kb
        )
        
    except Exception as e:
        logger.error(f"Confirm delete error: {e}")
        await callback.message.edit_text("❌ Error initiating deletion.")

@dp.callback_query_handler(lambda c: c.data.startswith("delete_confirm_"))
async def delete_server_confirm(callback: types.CallbackQuery):
    """Execute server deletion"""
    try:
        server_id = callback.data.split('_')[2]
        server = await get_server_by_id(server_id)
        
        if not server:
            await callback.message.edit_text("❌ Server not found.")
            return
        
        # Close SSH session
        close_ssh_session(server_id)
        
        # Delete from database
        await delete_server_by_id(server_id)
        
        await callback.message.edit_text(
            f"✅ <b>Server Deleted</b>\n\n"
            f"Server '{server['name']}' has been removed successfully.",
            parse_mode='HTML'
        )
        
        # Return to main menu after 2 seconds
        await start_command(callback.message)
        
    except Exception as e:
        logger.error(f"Delete server error: {e}")
        await callback.message.edit_text("❌ Error deleting server.")

# --- HANDLE EDIT INPUTS ---

@dp.message_handler(lambda message: message.from_user.id in user_input and user_input[message.from_user.id].get('edit') in ['name', 'username'])
async def handle_edit_inputs(message: types.Message):
    """Handle server edit inputs"""
    uid = message.from_user.id
    if uid not in user_input or 'edit' not in user_input[uid]:
        return
    
    data = user_input[uid]
    server_id = data['id']
    edit_type = data['edit']
    
    logger.info(f"Handling {edit_type} edit for server {server_id}")
    
    try:
        if edit_type == 'name':
            await update_server_name(server_id, message.text.strip())
            await message.answer("✅ <b>Server name updated successfully!</b>", parse_mode='HTML')
            
        elif edit_type == 'username':
            await update_server_username(server_id, message.text.strip())
            
            # Reconnect with new username
            server = await get_server_by_id(server_id)
            if server:
                close_ssh_session(server_id)
                try:
                    get_ssh_session(server_id, server['ip'], message.text.strip(), server['key_content'])
                    await message.answer("✅ <b>Username updated and reconnected successfully!</b>", parse_mode='HTML')
                except Exception as e:
                    await message.answer(f"⚠️ <b>Username updated but reconnection failed:</b>\n{str(e)}", parse_mode='HTML')
            else:
                await message.answer("✅ <b>Username updated!</b>", parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Error updating {edit_type}: {e}")
        await message.answer(f"❌ Error updating {edit_type}: {str(e)}")
    
    finally:
        user_input.pop(uid, None)
        await start_command(message)

# --- ERROR HANDLERS ---

@dp.errors_handler()
async def errors_handler(update, exception):
    """Global error handler"""
    logger.error(f"Update {update} caused error {exception}")
    return True

# --- MAIN ---

if __name__ == '__main__':
    logger.info("🚀 Starting Multi Server Manager Bot...")
    try:
        executor.start_polling(
            dp,
            skip_updates=True,
            on_startup=on_startup
        )
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
        sys.exit(1)
