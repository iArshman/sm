import logging
import io
import paramiko
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.utils import executor
from config import BOT_TOKEN
from db import (
    add_server,
    get_servers,
    get_server_by_id,
    update_server_name,
    update_server_username,
    delete_server_by_id
)
from bson.objectid import ObjectId
from bson.errors import InvalidId
from file_manager import init_file_manager
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# --- UTILS ---

def cancel_button():
    return InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Cancel", callback_data="cancel"))

def back_button(to):
    return InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Back", callback_data=to))

# Convert seconds to human-readable uptime
def format_uptime(seconds):
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

# --- TEMP STATE ---
user_input = {}
active_sessions = {}  # Store SSH sessions: {server_id: SSHClient}

# --- HELPER: MANAGE SSH SESSION ---
def get_ssh_session(server_id, ip, username, key_content):
    logger.info(f"Getting SSH session for server {server_id} ({ip})")
    try:
        if server_id in active_sessions and active_sessions[server_id].get_transport().is_active():
            logger.info(f"Reusing existing SSH session for {server_id}")
            return active_sessions[server_id]
        key_file = io.StringIO(key_content)
        ssh_key = None
        try:
            ssh_key = paramiko.RSAKey.from_private_key(key_file)
        except paramiko.SSHException:
            logger.info("Not an RSA key, trying ECDSA...")
            key_file.seek(0)
            try:
                ssh_key = paramiko.ECDSAKey.from_private_key(key_file)
            except paramiko.SSHException:
                logger.info("Not an ECDSA key, trying Ed25519...")
                key_file.seek(0)
                ssh_key = paramiko.Ed25519Key.from_private_key(key_file)
        if not ssh_key:
            raise paramiko.SSHException("Unsupported or invalid key format")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(ip, username=username, pkey=ssh_key, timeout=10)
        active_sessions[server_id] = ssh
        logger.info(f"Created new SSH session for {server_id}")
        return ssh
    except Exception as e:
        logger.error(f"Failed to create SSH session for {ip}: {e}")
        raise

# --- HELPER: FETCH REMOTE SERVER STATS ---
def get_remote_stats(server_id, ip, username, key_content):
    logger.info(f"Fetching stats for {ip} with user {username}")
    try:
        ssh = get_ssh_session(server_id, ip, username, key_content)
        os_info = "Unknown"
        try:
            stdin, stdout, stderr = ssh.exec_command("cat /etc/os-release")
            os_release = stdout.read().decode().strip()
            stderr_output = stderr.read().decode().strip()
            if stderr_output:
                logger.warning(f"OS release command error: {stderr_output}")
            os_dict = {}
            for line in os_release.splitlines():
                if '=' in line:
                    key, value = line.split('=', 1)
                    os_dict[key.strip()] = value.strip().strip('"')
            distro = os_dict.get('PRETTY_NAME', 'Unknown')
            stdin, stdout, stderr = ssh.exec_command("uname -r")
            kernel = stdout.read().decode().strip() or "Unknown"
            stderr_output = stderr.read().decode().strip()
            if stderr_output:
                logger.warning(f"Kernel command error: {stderr_output}")
            os_info = f"{distro}, Kernel {kernel}"
            logger.debug(f"OS info: {os_info}")
        except Exception as e:
            logger.error(f"OS info parsing error: {e}")
        uptime = "Unknown"
        try:
            # Try uptime -s for boot time
            stdin, stdout, stderr = ssh.exec_command("uptime -s")
            boot_time = stdout.read().decode().strip()
            stderr_output = stderr.read().decode().strip()
            if not stderr_output and boot_time:
                boot_dt = datetime.strptime(boot_time, "%Y-%m-%d %H:%M:%S")
                uptime_seconds = (datetime.now() - boot_dt).total_seconds()
                logger.debug(f"Boot time: {boot_time}, Uptime: {uptime_seconds} seconds")
            else:
                # Fallback to /proc/uptime
                logger.warning(f"Uptime -s failed: {stderr_output}, falling back to /proc/uptime")
                stdin, stdout, stderr = ssh.exec_command("cat /proc/uptime")
                uptime_data = stdout.read().decode().strip()
                stderr_output = stderr.read().decode().strip()
                logger.debug(f"/proc/uptime output: {uptime_data}")
                if stderr_output:
                    logger.warning(f"Uptime command error: {stderr_output}")
                uptime_seconds = float(uptime_data.split()[0])
            if uptime_seconds < 0 or uptime_seconds > 1e9:  # Validate reasonable range
                raise ValueError(f"Invalid uptime seconds: {uptime_seconds}")
            uptime = format_uptime(uptime_seconds)
            logger.debug(f"Uptime: {uptime} ({uptime_seconds} seconds)")
        except (IndexError, ValueError, Exception) as e:
            logger.error(f"Uptime parsing error: {e}")
        ram_total = ram_used = "Unknown"
        try:
            stdin, stdout, stderr = ssh.exec_command("free -m")
            mem_lines = stdout.read().decode().splitlines()
            logger.debug(f"free -m output: {mem_lines}")
            if len(mem_lines) > 1:
                mem_data = mem_lines[1].split()
                ram_total = round(int(mem_data[1]) / 1024, 2)
                ram_used = round(int(mem_data[2]) / 1024, 2)
            else:
                logger.warning("Unexpected 'free -m' output format")
        except (IndexError, ValueError) as e:
            logger.error(f"Memory parsing error: {e}")
        disk_total = disk_used = "Unknown"
        try:
            stdin, stdout, stderr = ssh.exec_command("df -h /")
            disk_lines = stdout.read().decode().splitlines()
            logger.debug(f"df -h output: {disk_lines}")
            if len(disk_lines) > 1:
                disk_data = disk_lines[1].split()
                disk_total = float(disk_data[1].replace("G", ""))
                disk_used = float(disk_data[2].replace("G", ""))
            else:
                logger.warning("Unexpected 'df -h' output format")
        except (IndexError, ValueError) as e:
            logger.error(f"Disk parsing error: {e}")
        cpu_usage = "Unknown"
        try:
            stdin, stdout, stderr = ssh.exec_command("top -bn1 | head -n3")
            cpu_lines = stdout.read().decode().splitlines()
            logger.debug(f"top output: {cpu_lines}")
            if len(cpu_lines) > 2:
                cpu_line = [line for line in cpu_lines if line.startswith("%Cpu")][0]
                cpu_fields = cpu_line.split()
                idle_index = cpu_fields.index("id,") - 1
                cpu_idle = float(cpu_fields[idle_index])
                cpu_usage = round(100 - cpu_idle, 2)
            else:
                logger.warning("Unexpected 'top' output format")
        except (IndexError, ValueError) as e:
            logger.error(f"CPU parsing error: {e}")
        return {
            "os": os_info,
            "uptime": uptime,
            "ram_total": ram_total,
            "ram_used": ram_used,
            "disk_total": disk_total,
            "disk_used": disk_used,
            "cpu_usage": cpu_usage,
            "error": None
        }
    except Exception as e:
        logger.error(f"SSH error for {ip}: {e}")
        return {"error": str(e)}

# --- STARTUP HOOK ---
async def on_startup(_):
    logger.info("Bot starting, attempting to connect to all servers...")
    try:
        servers = await get_servers()
        for server in servers:
            server_id = str(server['_id'])
            try:
                get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
                logger.info(f"Successfully connected to server {server['name']} ({server['ip']})")
            except Exception as e:
                logger.error(f"Failed to connect to server {server['name']} ({server['ip']}: {e}")
    except Exception as e:
        logger.error(f"Error during startup: {e}")
    init_file_manager(dp, bot, active_sessions, user_input)

# --- START ---
@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    try:
        servers = await get_servers()
        kb = InlineKeyboardMarkup(row_width=1)
        for server in servers:
            kb.add(InlineKeyboardButton(f"🖥 {server['name']}", callback_data=f"server_{server['_id']}"))
        kb.add(InlineKeyboardButton("➕ Add Server", callback_data="add_server"))
        await message.answer("🔧 <b>Multi Server Manager</b>", parse_mode='HTML', reply_markup=kb)
    except Exception as e:
        logger.error(f"Start handler error: {e}")
        await message.answer("❌ Error loading servers. Please try again.")

# --- BACK TO START ---
@dp.callback_query_handler(lambda c: c.data == "start")
async def back_to_start(callback: types.CallbackQuery):
    try:
        await callback.message.delete()
        servers = await get_servers()
        kb = InlineKeyboardMarkup(row_width=1)
        for server in servers:
            kb.add(InlineKeyboardButton(f"🖥 {server['name']}", callback_data=f"server_{server['_id']}"))
        kb.add(InlineKeyboardButton("➕ Add Server", callback_data="add_server"))
        await bot.send_message(callback.from_user.id, "🔧 <b>Multi Server Manager</b>", parse_mode='HTML', reply_markup=kb)
    except Exception as e:
        logger.error(f"Back to start error: {e}")
        await callback.message.edit_text("❌ Error returning to main menu.")

# --- CANCEL HANDLER ---
@dp.callback_query_handler(lambda c: c.data == "cancel")
async def cancel_action(callback: types.CallbackQuery):
    user_input.pop(callback.from_user.id, None)
    await callback.message.delete()
    await start(callback.message)

# --- ADD SERVER ---
@dp.callback_query_handler(lambda c: c.data == "add_server")
async def add_server_start(callback: types.CallbackQuery):
    user_input[callback.from_user.id] = {}
    await bot.send_message(callback.from_user.id, "📝 Enter server name:", reply_markup=cancel_button())
    user_input[callback.from_user.id]['step'] = 'name'

@dp.message_handler(lambda message: message.from_user.id in user_input and user_input[message.from_user.id].get('step'))
async def handle_inputs(message: types.Message):
    uid = message.from_user.id
    if uid not in user_input or 'step' not in user_input[uid]:
        return
    step = user_input[uid]['step']
    logger.info(f"Handling input for user {uid}, step: {step}")
    if step == 'name':
        user_input[uid]['name'] = message.text
        user_input[uid]['step'] = 'username'
        await message.answer("👤 Enter username:", reply_markup=cancel_button())
    elif step == 'username':
        user_input[uid]['username'] = message.text
        user_input[uid]['step'] = 'ip'
        await message.answer("🌐 Enter IP address:", reply_markup=cancel_button())
    elif step == 'ip':
        user_input[uid]['ip'] = message.text
        user_input[uid]['step'] = 'key'
        await message.answer("🔑 Send SSH private key file:", reply_markup=cancel_button())

@dp.message_handler(content_types=types.ContentType.DOCUMENT)
async def handle_key_upload(message: types.Message):
    uid = message.from_user.id
    if uid not in user_input or user_input[uid].get('step') != 'key':
        return
    try:
        file = await bot.download_file_by_id(message.document.file_id)
        key_content = file.read().decode('utf-8')
        data = user_input[uid]
        data['key_content'] = key_content
        await message.answer("🔌 Connecting to server...")
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            key_file = io.StringIO(key_content)
            ssh_key = None
            try:
                ssh_key = paramiko.RSAKey.from_private_key(key_file)
            except paramiko.SSHException:
                key_file.seek(0)
                try:
                    ssh_key = paramiko.ECDSAKey.from_private_key(key_file)
                except paramiko.SSHException:
                    key_file.seek(0)
                    try:
                        ssh_key = paramiko.Ed25519Key.from_private_key(key_file)
                    except paramiko.SSHException:
                        raise ValueError("Invalid key format")
            ssh.connect(data['ip'], username=data['username'], pkey=ssh_key, timeout=10)
            ssh.close()
            if str(data.get('_id')) in active_sessions:
                try:
                    active_sessions[str(data.get('_id'))].close()
                except:
                    pass
                active_sessions.pop(str(data.get('_id')), None)
            await add_server(data)
            server_id = str((await get_servers())[-1]['_id'])
            try:
                get_ssh_session(server_id, data['ip'], data['username'], key_content)
            except Exception as e:
                logger.error(f"Failed to establish session for new server {server_id}: {e}")
            await message.answer("✅ Server added successfully!")
        except Exception as e:
            logger.error(f"SSH connection failed for {data['ip']}: {e}")
            await message.answer(f"❌ SSH connection failed: {e}")
        user_input.pop(uid, None)
        await start(message)
    except Exception as e:
        logger.error(f"Key upload error for user {uid}: {e}")
        await message.answer("❌ Error processing key file. Please try again.")

# --- SERVER MENU ---
@dp.callback_query_handler(lambda c: c.data.startswith("server_"))
async def view_server(callback: types.CallbackQuery):
    try:
        server_id = callback.data.split('_')[1]
        server = await get_server_by_id(server_id)
        if not server:
            await callback.message.edit_text("❌ Server not found.")
            return
        logger.info(f"Viewing server {server_id}: {server['name']}")
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("🗂 File Manager", callback_data=f"file_manager_{server_id}"),
            InlineKeyboardButton("📊 Server Info", callback_data=f"info_{server_id}"),
            InlineKeyboardButton("🤖 Bot Manager", callback_data=f"bot_manager_{server_id}"),
            InlineKeyboardButton("✏️ Edit", callback_data=f"edit_{server_id}"),
        )
        kb.add(InlineKeyboardButton("⬅️ Back", callback_data="start"))
        await callback.message.edit_text(f"🖥 <b>{server['name']}</b>", parse_mode='HTML', reply_markup=kb)
    except Exception as e:
        logger.error(f"View server error for {server_id}: {e}")
        await callback.message.edit_text("❌ Error loading server details.")

# --- SERVER INFO ---
@dp.callback_query_handler(lambda c: c.data.startswith("info_"))
async def server_info(callback: types.CallbackQuery):
    try:
        server_id = callback.data.split('_')[1]
        server = await get_server_by_id(server_id)
        if not server:
            await callback.message.edit_text("❌ Server not found.")
            return
        await callback.message.edit_text("📊 Fetching server stats...", parse_mode="HTML")
        stats = get_remote_stats(server_id, server['ip'], server['username'], server['key_content'])
        if stats.get('error'):
            text = (
                f"🖥 <b>{server['name']}</b>\n"
                f"👤 User: <code>{server['username']}</code>\n"
                f"🌐 IP: <code>{server['ip']}</code>\n"
                f"❌ Error fetching stats: {stats['error']}"
            )
        else:
            text = (
                f"🖥 <b>{server['name']}</b>\n"
                f"👤 User: <code>{server['username']}</code>\n"
                f"🌐 IP: <code>{server['ip']}</code>\n"
                f"💻 OS: {stats['os']}\n"
                f"⏱ Uptime: {stats['uptime']}\n"
                f"🧠 Total Memory: {stats['ram_total']} GB\n"
                f"🧠 Used Memory: {stats['ram_used']} GB\n"
                f"💾 Total Disk: {stats['disk_total']} GB\n"
                f"💾 Used Disk: {stats['disk_used']} GB\n"
                f"🔥 CPU Usage: {stats['cpu_usage']}%"
            )
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_button(f"server_{server_id}"))
    except Exception as e:
        logger.error(f"Server info error for server {server_id}: {e}")
        await callback.message.edit_text("❌ Error fetching server info. Please try again.")

# --- BOT MANAGER PLACEHOLDER ---
@dp.callback_query_handler(lambda c: c.data.startswith("bot_manager_"))
async def bot_manager(callback: types.CallbackQuery):
    server_id = callback.data.split('_')[2]
    await callback.message.edit_text("🤖 Bot Manager: Coming soon", reply_markup=back_button(f"server_{server_id}"))

# --- EDIT SERVER ---
@dp.callback_query_handler(lambda c: c.data.startswith("edit_"))
async def edit_server(callback: types.CallbackQuery):
    try:
        server_id = callback.data.split('_')[1]
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("✏️ Rename", callback_data=f"rename_{server_id}"),
            InlineKeyboardButton("👤 Change Username", callback_data=f"reuser_{server_id}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"delete_{server_id}"),
        )
        kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"server_{server_id}"))
        await callback.message.edit_text("✏️ Edit Server", parse_mode='HTML', reply_markup=kb)
    except Exception as e:
        logger.error(f"Edit server error: {e}")
        await callback.message.edit_text("❌ Error loading edit menu.")

# --- RENAME SERVER ---
@dp.callback_query_handler(lambda c: c.data.startswith("rename_"))
async def rename_server(callback: types.CallbackQuery):
    try:
        sid = callback.data.split('_')[1]
        user_input[callback.from_user.id] = {'edit': 'name', 'id': sid}
        await bot.send_message(callback.from_user.id, "✏️ Enter new name:", reply_markup=cancel_button())
    except Exception as e:
        logger.error(f"Rename server error: {e}")
        await callback.message.edit_text("❌ Error initiating rename.")

# --- CHANGE USERNAME ---
@dp.callback_query_handler(lambda c: c.data.startswith("reuser_"))
async def change_username(callback: types.CallbackQuery):
    try:
        sid = callback.data.split('_')[1]
        user_input[callback.from_user.id] = {'edit': 'username', 'id': sid}
        await bot.send_message(callback.from_user.id, "👤 Enter new username:", reply_markup=cancel_button())
    except Exception as e:
        logger.error(f"Change username error: {e}")
        await callback.message.edit_text("❌ Error initiating username change.")

# --- DELETE SERVER CONFIRMATION ---
@dp.callback_query_handler(lambda c: c.data.startswith("delete_") and not c.data.startswith("delete_confirm_"))
async def confirm_delete(callback: types.CallbackQuery):
    try:
        logger.info(f"Delete callback data: {callback.data}")
        parts = callback.data.split('_')
        if len(parts) != 2 or parts[0] != 'delete':
            raise ValueError(f"Invalid delete callback data: {callback.data}")
        sid = parts[1]
        try:
            ObjectId(sid)
        except InvalidId:
            raise ValueError(f"Invalid server ID: {sid}")
        server = await get_server_by_id(sid)
        if not server:
            raise ValueError(f"Server {sid} not found")
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("✅ Yes, delete", callback_data=f"delete_confirm_{sid}"),
            InlineKeyboardButton("⬅️ Back", callback_data=f"edit_{sid}")
        )
        await callback.message.edit_text(f"⚠️ Are you sure you want to delete server '{server['name']}'?", reply_markup=kb)
    except Exception as e:
        logger.error(f"Confirm delete error: {e}")
        try:
            await callback.message.edit_text(f"❌ Error initiating delete: {str(e)}")
        except:
            await bot.send_message(callback.from_user.id, f"❌ Error initiating delete: {str(e)}")

# --- DELETE CONFIRM ---
@dp.callback_query_handler(lambda c: c.data.startswith("delete_confirm_"))
async def delete_confirm(callback: types.CallbackQuery):
    try:
        logger.info(f"Delete confirm callback data: {callback.data}")
        parts = callback.data.split('_')
        if len(parts) != 3 or parts[0] != 'delete' or parts[1] != 'confirm':
            raise ValueError(f"Invalid delete confirm callback data: {callback.data}")
        sid = parts[2]
        try:
            ObjectId(sid)
        except InvalidId:
            raise ValueError(f"Invalid server ID: {sid}")
        server = await get_server_by_id(sid)
        if not server:
            raise ValueError(f"Server {sid} not found")
        await delete_server_by_id(sid)
        if sid in active_sessions:
            try:
                active_sessions[sid].close()
            except:
                pass
            active_sessions.pop(sid, None)
        await callback.message.edit_text(f"✅ Server '{server['name']}' deleted.")
        await start(callback.message)
    except Exception as e:
        logger.error(f"Delete confirm error: {e}")
        try:
            await callback.message.edit_text(f"❌ Error deleting server: {str(e)}")
        except:
            await bot.send_message(callback.from_user.id, f"❌ Error deleting server: {str(e)}")

# --- HANDLE RENAME/USERNAME INPUTS ---
@dp.message_handler(lambda message: message.from_user.id in user_input and user_input[message.from_user.id].get('edit') in ['name', 'username'])
async def handle_renames(message: types.Message):
    uid = message.from_user.id
    if uid not in user_input or 'edit' not in user_input[uid]:
        return
    data = user_input[uid]
    sid = data['id']
    edit_type = data['edit']
    logger.info(f"Handling rename/username for user {uid}, server {sid}, type: {edit_type}")
    try:
        if edit_type == 'name':
            await update_server_name(sid, message.text)
            await message.answer("✅ Name updated.")
        elif edit_type == 'username':
            await update_server_username(sid, message.text)
            await message.answer("✅ Username updated.")
    except Exception as e:
        logger.error(f"Error updating {edit_type} for server {sid}: {e}")
        await message.answer(f"❌ Error updating {edit_type}: {str(e)}")
    finally:
        user_input.pop(uid, None)
        await start(message)

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
