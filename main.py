import logging
import asyncio
import platform
import paramiko
import psutil
import socket
import time
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import BOT_TOKEN, ADMIN_IDS
from db import (
    add_server, get_servers, get_server_by_id,
    update_server_name, update_server_username, delete_server_by_id
)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)
logging.basicConfig(level=logging.INFO)

# Util
async def is_ssh_accessible(ip, username, pkey_str):
    try:
        key = paramiko.RSAKey.from_private_key_file(pkey_str)
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(ip, username=username, pkey=key, timeout=5)
        ssh.close()
        return True
    except Exception as e:
        return False

# Start Command
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("Access denied.")
    await show_main_menu(message)

async def show_main_menu(message_or_callback):
    servers = get_servers()
    kb = InlineKeyboardMarkup(row_width=2)
    for s in servers:
        kb.add(InlineKeyboardButton(f"🖥️ {s['name']}", callback_data=f"server:{s['_id']}"))
    kb.add(InlineKeyboardButton("➕ Add Server", callback_data="add_server"))

    text = "<b>🌐 Server Manager</b>\nSelect a server to manage:"
    if isinstance(message_or_callback, types.CallbackQuery):
        await message_or_callback.message.edit_text(text, reply_markup=kb)
    else:
        await message_or_callback.answer(text, reply_markup=kb)

# Add Server Flow
user_state = {}

@dp.callback_query_handler(lambda c: c.data == "add_server")
async def handle_add_server(callback: types.CallbackQuery):
    user_state[callback.from_user.id] = {'step': 'name'}
    await callback.message.edit_text("📝 Enter server name:\n\n❌ /cancel to abort")

@dp.message_handler(lambda m: m.from_user.id in user_state)
async def collect_server_info(message: types.Message):
    uid = message.from_user.id
    state = user_state.get(uid, {})
    step = state.get('step')

    if message.text == "/cancel":
        user_state.pop(uid, None)
        return await message.answer("❌ Cancelled.", reply_markup=types.ReplyKeyboardRemove())

    if step == 'name':
        state['name'] = message.text
        state['step'] = 'username'
        await message.answer("👤 Enter SSH username:")
    elif step == 'username':
        state['username'] = message.text
        state['step'] = 'ip'
        await message.answer("📡 Enter server IP address:")
    elif step == 'ip':
        state['ip'] = message.text
        state['step'] = 'pkey'
        await message.answer("📎 Send the private key file (.pem):")
    else:
        await message.answer("❌ Unexpected step. Send /cancel to restart.")

@dp.message_handler(content_types=types.ContentType.DOCUMENT)
async def handle_pkey_file(message: types.Message):
    uid = message.from_user.id
    state = user_state.get(uid)
    if not state or state.get('step') != 'pkey':
        return

    doc = message.document
    if not doc.file_name.endswith(".pem"):
        return await message.answer("❌ Invalid file. Must be .pem format.")

    file_path = f"/tmp/{doc.file_name}"
    await doc.download(destination_file=file_path)

    await message.answer("⏳ Testing SSH connection...")
    access = await is_ssh_accessible(state['ip'], state['username'], file_path)

    if not access:
        return await message.answer("❌ SSH login failed. Check key/IP/username and try again.")

    add_server({
        'name': state['name'],
        'username': state['username'],
        'ip': state['ip'],
        'pkey': file_path
    })
    user_state.pop(uid, None)
    await message.answer("✅ Server added successfully!")
    await show_main_menu(message)

# Server Selected
@dp.callback_query_handler(lambda c: c.data.startswith("server:"))
async def handle_server_selected(callback: types.CallbackQuery):
    sid = callback.data.split(":")[1]
    server = get_server_by_id(sid)
    if not server:
        return await callback.message.edit_text("❌ Server not found.")

    # Simulate info
    info = f"🖥️ <b>Server Info: {server['name']}</b>\n\n"
    info += f"👤 <b>User:</b> {server['username']}\n"
    info += f"📡 <b>IP:</b> {server['ip']}\n"
    info += f"💻 <b>OS:</b> Ubuntu 22.04 LTS\n"
    info += f"⏱️ <b>Uptime:</b> 3 days, 5 hours\n\n"
    info += f"📊 <b>Resources:</b>\n🔋 RAM: 8 GB | 3.2 GB used\n💽 Disk: 100 GB | 55 GB used\n🧠 CPU: 18%\n"

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🗂️ File Manager", callback_data=f"file:{sid}"),
        InlineKeyboardButton("🤖 Bot Manager", callback_data=f"bot:{sid}")
    )
    kb.add(InlineKeyboardButton("✏️ Edit", callback_data=f"edit:{sid}"))
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data="back"))

    await callback.message.edit_text(info, reply_markup=kb)

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
