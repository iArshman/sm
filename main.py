import os
import asyncio
import paramiko
import platform
import subprocess
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from config import BOT_TOKEN, ADMIN_IDS
from db import *
from file import handle_file_manager
from botmanager import handle_bot_manager

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

user_states = {}
temp_data = {}


def vertical_keyboard(buttons):
    markup = InlineKeyboardMarkup(row_width=1)
    for b in buttons:
        markup.add(b)
    return markup


def ssh_connect(ip, username, key_path):
    try:
        key = paramiko.RSAKey.from_private_key_file(key_path)
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(ip, username=username, pkey=key, timeout=10)
        return ssh
    except Exception as e:
        return None


@dp.message_handler(commands=['start'])
async def start_handler(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.reply("❌ Access Denied")

    user_id = message.from_user.id
    all_servers = get_servers(user_id)
    buttons = [InlineKeyboardButton(f"🖥 {srv['name']}", callback_data=f"server_{srv['name']}") for srv in all_servers]
    buttons.append(InlineKeyboardButton("➕ Add Server", callback_data="add_server"))
    await message.answer("<b>📡 Choose a server:</b>", reply_markup=vertical_keyboard(buttons))


@dp.callback_query_handler(lambda c: c.data.startswith("server_"))
async def manage_server(call: types.CallbackQuery):
    name = call.data.split("_", 1)[1]
    srv = get_server_by_name(call.from_user.id, name)
    if not srv:
        return await call.message.edit_text("❌ Server not found")

    temp_data[call.from_user.id] = srv
    btns = [
        InlineKeyboardButton("📂 File Manager", callback_data="file_manager"),
        InlineKeyboardButton("🤖 Bot Manager", callback_data="bot_manager"),
        InlineKeyboardButton("ℹ️ Server Info", callback_data="server_info"),
        InlineKeyboardButton("⚙️ Edit Server", callback_data="edit_server")
    ]
    await call.message.edit_text(f"<b>🔧 Managing:</b> {name}", reply_markup=vertical_keyboard(btns))


@dp.callback_query_handler(lambda c: c.data == "add_server")
async def add_server_step1(call: types.CallbackQuery):
    user_states[call.from_user.id] = 'awaiting_name'
    await call.message.answer("📝 Enter server name:")

@dp.message_handler(lambda msg: user_states.get(msg.from_user.id) == 'awaiting_name')
async def add_server_step2(msg: types.Message):
    temp_data[msg.from_user.id] = {'name': msg.text}
    user_states[msg.from_user.id] = 'awaiting_username'
    await msg.answer("👤 Enter SSH username:")

@dp.message_handler(lambda msg: user_states.get(msg.from_user.id) == 'awaiting_username')
async def add_server_step3(msg: types.Message):
    temp_data[msg.from_user.id]['username'] = msg.text
    user_states[msg.from_user.id] = 'awaiting_ip'
    await msg.answer("🌐 Enter IP address:")

@dp.message_handler(lambda msg: user_states.get(msg.from_user.id) == 'awaiting_ip')
async def add_server_step4(msg: types.Message):
    temp_data[msg.from_user.id]['ip'] = msg.text
    user_states[msg.from_user.id] = 'awaiting_key'
    await msg.answer("🔑 Send SSH private key file:")

@dp.message_handler(content_types=types.ContentType.DOCUMENT)
async def add_server_step5(msg: types.Message):
    if user_states.get(msg.from_user.id) != 'awaiting_key': return

    path = f"ssh_keys/{msg.document.file_name}"
    await msg.document.download(destination_file=path)

    td = temp_data[msg.from_user.id]
    ssh = ssh_connect(td['ip'], td['username'], path)
    if not ssh:
        os.remove(path)
        return await msg.answer("❌ SSH connection failed. Check credentials.")
    ssh.close()

    add_server(msg.from_user.id, td['name'], td['username'], td['ip'], path)
    user_states.pop(msg.from_user.id)
    temp_data.pop(msg.from_user.id)
    await msg.answer("✅ Server added successfully! Use /start to manage.")


@dp.callback_query_handler(lambda c: c.data == "server_info")
async def show_server_info(call: types.CallbackQuery):
    srv = temp_data.get(call.from_user.id)
    ssh = ssh_connect(srv['ip'], srv['username'], srv['key_file_path'])
    if not ssh:
        return await call.message.edit_text("❌ Cannot connect to server.")

    def run_cmd(cmd):
        stdin, stdout, stderr = ssh.exec_command(cmd)
        return stdout.read().decode()

    uname = run_cmd("uname -a")
    uptime = run_cmd("uptime -p")
    mem = run_cmd("free -h")
    disk = run_cmd("df -h")
    cpu = run_cmd("top -bn1 | grep '%Cpu'")

    info = f"""
ℹ️ <b>Server Info</b>
👤 <b>User:</b> {srv['username']}
🌐 <b>IP:</b> {srv['ip']}
🖥 <b>OS:</b> {uname.strip()}
⏱ <b>Uptime:</b> {uptime.strip()}

🧠 <b>RAM:</b>
<pre>{mem.strip()}</pre>
💾 <b>Disk:</b>
<pre>{disk.strip()}</pre>
⚙️ <b>CPU:</b>
<pre>{cpu.strip()}</pre>
"""
    await call.message.edit_text(info)
    ssh.close()


@dp.callback_query_handler(lambda c: c.data == "edit_server")
async def edit_server(call: types.CallbackQuery):
    btns = [
        InlineKeyboardButton("✏️ Change Name", callback_data="edit_name"),
        InlineKeyboardButton("👤 Change Username", callback_data="edit_username"),
        InlineKeyboardButton("🗑 Delete Server", callback_data="delete_server")
    ]
    await call.message.edit_text("⚙️ Edit Options:", reply_markup=vertical_keyboard(btns))


@dp.callback_query_handler(lambda c: c.data == "edit_name")
async def edit_name(call: types.CallbackQuery):
    user_states[call.from_user.id] = 'editing_name'
    await call.message.answer("✏️ Send new server name:")

@dp.message_handler(lambda msg: user_states.get(msg.from_user.id) == 'editing_name')
async def set_new_name(msg: types.Message):
    srv = temp_data[msg.from_user.id]
    update_server_name(msg.from_user.id, srv['name'], msg.text)
    await msg.answer("✅ Name updated.")
    user_states.pop(msg.from_user.id)

@dp.callback_query_handler(lambda c: c.data == "edit_username")
async def edit_username(call: types.CallbackQuery):
    user_states[call.from_user.id] = 'editing_username'
    await call.message.answer("👤 Send new SSH username:")

@dp.message_handler(lambda msg: user_states.get(msg.from_user.id) == 'editing_username')
async def set_new_username(msg: types.Message):
    srv = temp_data[msg.from_user.id]
    update_server_username(msg.from_user.id, srv['name'], msg.text)
    await msg.answer("✅ Username updated.")
    user_states.pop(msg.from_user.id)

@dp.callback_query_handler(lambda c: c.data == "delete_server")
async def confirm_delete(call: types.CallbackQuery):
    btns = [
        InlineKeyboardButton("✅ Confirm", callback_data="delete_confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel")
    ]
    await call.message.edit_text("⚠️ Are you sure you want to delete this server?", reply_markup=vertical_keyboard(btns))

@dp.callback_query_handler(lambda c: c.data == "delete_confirm")
async def do_delete(call: types.CallbackQuery):
    srv = temp_data[call.from_user.id]
    delete_server(call.from_user.id, srv['name'])
    await call.message.edit_text("🗑 Server deleted.")

@dp.callback_query_handler(lambda c: c.data == "cancel")
async def cancel(call: types.CallbackQuery):
    await call.message.edit_text("❌ Canceled.")


@dp.callback_query_handler(lambda c: c.data == "file_manager")
async def file_manager(call: types.CallbackQuery):
    await handle_file_manager(call.message, temp_data[call.from_user.id])

@dp.callback_query_handler(lambda c: c.data == "bot_manager")
async def bot_manager(call: types.CallbackQuery):
    await handle_bot_manager(call.message, temp_data[call.from_user.id])


if __name__ == '__main__':
    os.makedirs("ssh_keys", exist_ok=True)
    executor.start_polling(dp, skip_updates=True)


