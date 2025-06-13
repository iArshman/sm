import logging
import os
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

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# --- UTILS ---

def cancel_button():
    return InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Cancel", callback_data="cancel"))

def back_button(to):
    return InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Back", callback_data=to))

# --- TEMP STATE ---
user_input = {}

# --- HELPER: FETCH REMOTE SERVER STATS ---
def get_remote_stats(ip, username, key_path):
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(ip, username=username, key_filename=key_path, timeout=10)

        # Get OS info
        stdin, stdout, stderr = ssh.exec_command("uname -sr")
        os_info = stdout.read().decode().strip()

        # Get uptime
        stdin, stdout, stderr = ssh.exec_command("uptime -p")
        uptime = stdout.read().decode().strip().replace("up ", "")

        # Get RAM (in GB)
        stdin, stdout, stderr = ssh.exec_command("free -m")
        ram_lines = stdout.read().decode().splitlines()
        ram_total = round(int(ram_lines[1].split()[1]) / 1024, 2)  # Convert MB to GB

        # Get disk (in GB)
        stdin, stdout, stderr = ssh.exec_command("df -h /")
        disk_lines = stdout.read().decode().splitlines()
        disk_total = float(disk_lines[1].split()[1].replace("G", ""))  # Extract GB

        # Get CPU usage
        stdin, stdout, stderr = ssh.exec_command("top -bn1 | head -n3")
        cpu_line = stdout.read().decode().splitlines()[2]
        cpu_idle = float(cpu_line.split()[-2])
        cpu_usage = round(100 - cpu_idle, 2)

        ssh.close()
        return {
            "os": os_info,
            "uptime": uptime,
            "ram_total": ram_total,
            "disk_total": disk_total,
            "cpu_usage": cpu_usage,
            "error": None
        }
    except Exception as e:
        return {"error": str(e)}

# --- START ---
@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    servers = await get_servers()
    kb = InlineKeyboardMarkup(row_width=1)
    for server in servers:
        kb.add(InlineKeyboardButton(f"🖥 {server['name']}", callback_data=f"server_{server['_id']}"))
    kb.add(InlineKeyboardButton("➕ Add Server", callback_data="add_server"))
    await message.answer("🔧 <b>Multi Server Manager</b>", parse_mode='HTML', reply_markup=kb)

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

@dp.message_handler()
async def handle_inputs(message: types.Message):
    uid = message.from_user.id
    if uid not in user_input or 'step' not in user_input[uid]:
        return
    step = user_input[uid]['step']

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

    file = await bot.download_file_by_id(message.document.file_id)
    key_path = f"/tmp/{message.document.file_name}"
    with open(key_path, "wb") as f:
        f.write(file.read())

    data = user_input[uid]
    await message.answer("🔌 Connecting to server...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(data['ip'], username=data['username'], key_filename=key_path, timeout=10)
        ssh.close()
        await add_server(data)
        await message.answer("✅ Server added successfully!")
    except Exception as e:
        await message.answer(f"❌ SSH connection failed: {e}")

    user_input.pop(uid, None)
    await start(message)

# --- SERVER MENU ---
@dp.callback_query_handler(lambda c: c.data.startswith("server_"))
async def view_server(callback: types.CallbackQuery):
    server_id = callback.data.split("_")[1]
    server = await get_server_by_id(server_id)
    if not server:
        await callback.message.edit_text("❌ Server not found.")
        return

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🗂 File Manager", callback_data=f"file_{server_id}"),
        InlineKeyboardButton("📊 Server Info", callback_data=f"info_{server_id}"),
        InlineKeyboardButton("🤖 Bot Manager", callback_data=f"bot_{server_id}"),
        InlineKeyboardButton("✏️ Edit", callback_data=f"edit_{server_id}"),
    )
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="start"))
    await callback.message.edit_text(f"🖥 <b>{server['name']}</b>", parse_mode='HTML', reply_markup=kb)

# --- SERVER INFO ---
@dp.callback_query_handler(lambda c: c.data.startswith("info_"))
async def server_info(callback: types.CallbackQuery):
    server_id = callback.data.split("_")[1]
    server = await get_server_by_id(server_id)
    if not server:
        await callback.message.edit_text("❌ Server not found.")
        return

    stats = get_remote_stats(server['ip'], server['username'], server['key_path'])
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
            f"🧠 RAM: {stats['ram_total']} GB\n"
            f"💾 Disk: {stats['disk_total']} GB\n"
            f"🔥 CPU Usage: {stats['cpu_usage']}%"
        )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_button(f"server_{server_id}"))

# --- FILE MANAGER PLACEHOLDER ---
@dp.callback_query_handler(lambda c: c.data.startswith("file_"))
async def file_manager(callback: types.CallbackQuery):
    await callback.message.edit_text("🗂 File Manager: Coming soon", reply_markup=back_button(callback.data.replace("file_", "server_")))

# --- BOT MANAGER PLACEHOLDER ---
@dp.callback_query_handler(lambda c: c.data.startswith("bot_"))
async def bot_manager(callback: types.CallbackQuery):
    await callback.message.edit_text("🤖 Bot Manager: Coming soon", reply_markup=back_button(callback.data.replace("bot_", "server_")))

# --- EDIT SERVER ---
@dp.callback_query_handler(lambda c: c.data.startswith("edit_"))
async def edit_server(callback: types.CallbackQuery):
    server_id = callback.data.split("_")[1]
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✏️ Rename", callback_data=f"rename_{server_id}"),
        InlineKeyboardButton("👤 Change Username", callback_data=f"reuser_{server_id}"),
        InlineKeyboardButton("🗑 Delete", callback_data=f"delete_{server_id}"),
    )
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"server_{server_id}"))
    await callback.message.edit_text("✏️ Edit Server", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("rename_"))
async def rename_server(callback: types.CallbackQuery):
    sid = callback.data.split("_")[1]
    user_input[callback.from_user.id] = {'edit': 'name', 'id': sid}
    await bot.send_message(callback.from_user.id, "✏️ Enter new name:", reply_markup=cancel_button())

@dp.callback_query_handler(lambda c: c.data.startswith("reuser_"))
async def change_username(callback: types.CallbackQuery):
    sid = callback.data.split("_")[1]
    user_input[callback.from_user.id] = {'edit': 'username', 'id': sid}
    await bot.send_message(callback.from_user.id, "👤 Enter new username:", reply_markup=cancel_button())

@dp.callback_query_handler(lambda c: c.data.startswith("delete_"))
async def confirm_delete(callback: types.CallbackQuery):
    sid = callback.data.split("_")[1]
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("✅ Yes, delete", callback_data=f"delete_confirm_{sid}"),
        InlineKeyboardButton("⬅️ Back", callback_data=f"edit_{sid}")
    )
    await callback.message.edit_text("⚠️ Are you sure you want to delete this server?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("delete_confirm_"))
async def delete_confirm(callback: types.CallbackQuery):
    sid = callback.data.split("_")[2]
    await delete_server_by_id(sid)
    await callback.message.edit_text("✅ Server deleted.")
    await start(callback.message)

@dp.message_handler()
async def handle_renames(message: types.Message):
    uid = message.from_user.id
    if uid not in user_input or 'edit' not in user_input[uid]:
        return
    data = user_input[uid]
    sid = data['id']

    if data['edit'] == 'name':
        await update_server_name(sid, message.text)
        await message.answer("✅ Name updated.")
    elif data['edit'] == 'username':
        await update_server_username(sid, message.text)
        await message.answer("✅ Username updated.")

    user_input.pop(uid, None)
    await start(message)

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
