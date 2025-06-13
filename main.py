# main.py

import logging
import os
import platform
import paramiko
import psutil
import uptime
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputFile
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
        await add_server(data['name'], data['username'], data['ip'], key_path)
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

    text = (
        f"🖥 <b>{server['name']}</b>\n"
        f"👤 User: <code>{server['username']}</code>\n"
        f"🌐 IP: <code>{server['ip']}</code>\n"
        f"💻 OS: {platform.system()} {platform.release()}\n"
        f"⏱ Uptime: {uptime.uptime() // 3600} hrs\n"
        f"🧠 RAM: {round(psutil.virtual_memory().total / 1e9, 2)} GB\n"
        f"💾 Disk: {round(psutil.disk_usage('/').total / 1e9, 2)} GB\n"
        f"🔥 CPU Usage: {psutil.cpu_percent()}%"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_button(f"server_{server_id}"))

# --- PLACEHOLDER MODULES ---

@dp.callback_query_handler(lambda c: c.data.startswith("file_"))
async def file_manager(callback: types.CallbackQuery):
    await callback.message.edit_text("🗂 File Manager: Coming soon", reply_markup=back_button(callback.data.replace("file_", "server_")))

@dp.callback_query_handler(lambda c: c.data.startswith("bot_"))
async def bot_manager(callback: types.CallbackQuery):
    await callback.message.edit_text("🤖 Bot Manager: Coming soon", reply_markup=back_button(callback.data.replace("bot_", "server_")))

# --- TO DO: Edit Server ---


# Edit Server Menu
@dp.callback_query_handler(lambda c: c.data.startswith("edit:"))
async def edit_server(callback: types.CallbackQuery):
    sid = callback.data.split(":")[1]
    server = get_server_by_id(sid)
    if not server:
        return await callback.message.edit_text("❌ Server not found.")

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📝 Change Name", callback_data=f"rename:{sid}"),
        InlineKeyboardButton("👤 Change Username", callback_data=f"reuser:{sid}")
    )
    kb.add(InlineKeyboardButton("🗑️ Delete Server", callback_data=f"delete_confirm:{sid}"))
    kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"server:{sid}"))

    await callback.message.edit_text(f"✏️ Edit Server: <b>{server['name']}</b>", reply_markup=kb)

# Delete Confirmation
@dp.callback_query_handler(lambda c: c.data.startswith("delete_confirm:"))
async def confirm_delete(callback: types.CallbackQuery):
    sid = callback.data.split(":")[1]
    server = get_server_by_id(sid)
    if not server:
        return await callback.message.edit_text("❌ Server not found.")

    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("✅ Yes, delete", callback_data=f"delete:{sid}"))
    kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"server:{sid}"))

    await callback.message.edit_text(f"⚠️ Are you sure you want to delete <b>{server['name']}</b>?", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("delete:"))
async def do_delete(callback: types.CallbackQuery):
    sid = callback.data.split(":")[1]
    delete_server_by_id(sid)
    await callback.message.edit_text("✅ Server deleted.")
    await asyncio.sleep(1)
    await show_main_menu(callback)

# Rename
@dp.callback_query_handler(lambda c: c.data.startswith("rename:"))
async def rename_server(callback: types.CallbackQuery):
    sid = callback.data.split(":")[1]
    user_state[callback.from_user.id] = {'rename': sid}
    await callback.message.edit_text("📝 Send new name:\n/cancel to abort")

@dp.message_handler(lambda m: m.from_user.id in user_state and 'rename' in user_state[m.from_user.id])
async def handle_rename(message: types.Message):
    sid = user_state[message.from_user.id]['rename']
    update_server_name(sid, message.text)
    user_state.pop(message.from_user.id)
    await message.answer("✅ Server name updated.")
    await show_main_menu(message)

# Reuser
@dp.callback_query_handler(lambda c: c.data.startswith("reuser:"))
async def reuser_server(callback: types.CallbackQuery):
    sid = callback.data.split(":")[1]
    user_state[callback.from_user.id] = {'reuser': sid}
    await callback.message.edit_text("👤 Send new username:\n/cancel to abort")

@dp.message_handler(lambda m: m.from_user.id in user_state and 'reuser' in user_state[m.from_user.id])
async def handle_reuser(message: types.Message):
    sid = user_state[message.from_user.id]['reuser']
    update_server_username(sid, message.text)
    user_state.pop(message.from_user.id)
    await message.answer("✅ Server username updated.")
    await show_main_menu(message)

# Cancel
@dp.callback_query_handler(lambda c: c.data == "back")
async def cancel_back(callback: types.CallbackQuery):
    await show_main_menu(callback)

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)


