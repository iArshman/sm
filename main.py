import logging
import paramiko
import asyncio
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import BOT_TOKEN, ADMIN_IDS
from db import add_server, get_servers, get_server_by_id, update_server_name, update_server_username, delete_server_by_id

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

user_states = {}
temp_server_data = {}


def cancel_back_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("🔙 Back", callback_data="cancel")
    )
    return keyboard


def server_list_keyboard():
    servers = get_servers()
    keyboard = InlineKeyboardMarkup(row_width=2)
    for server in servers:
        keyboard.add(InlineKeyboardButton(f"🖥️ {server['name']}", callback_data=f"server_{server['_id']}"))
    keyboard.add(InlineKeyboardButton("➕ Add Server", callback_data="add_server"))
    return keyboard


def server_action_keyboard(server_id):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("📁 File Manager", callback_data=f"file_{server_id}"),
        InlineKeyboardButton("🤖 Bot Manager", callback_data=f"bot_{server_id}"),
        InlineKeyboardButton("ℹ️ Server Info", callback_data=f"info_{server_id}"),
        InlineKeyboardButton("✏️ Edit Server", callback_data=f"edit_{server_id}")
    )
    keyboard.add(InlineKeyboardButton("🔙 Back", callback_data="cancel"))
    return keyboard


def edit_server_keyboard(server_id):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("✏️ Change Name", callback_data=f"change_name_{server_id}"),
        InlineKeyboardButton("👤 Change Username", callback_data=f"change_username_{server_id}"),
        InlineKeyboardButton("❌ Delete Server", callback_data=f"delete_{server_id}")
    )
    keyboard.add(InlineKeyboardButton("🔙 Back", callback_data=f"server_{server_id}"))
    return keyboard


@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("Access Denied.")
    await message.answer("👋 Welcome to Multi Server Manager", reply_markup=server_list_keyboard())


@dp.callback_query_handler(lambda c: True)
async def handle_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    data = callback_query.data

    if data == "cancel":
        user_states.pop(user_id, None)
        temp_server_data.pop(user_id, None)
        await callback_query.message.edit_text("🔙 Back to server list:", reply_markup=server_list_keyboard())
        return

    if data == "add_server":
        user_states[user_id] = 'adding_name'
        temp_server_data[user_id] = {}
        await callback_query.message.edit_text("🖋️ Enter server name:", reply_markup=cancel_back_keyboard())
        return

    if data.startswith("server_"):
        server_id = data.split("_")[1]
        server = get_server_by_id(server_id)
        if not server:
            await callback_query.message.edit_text("❌ Server not found.", reply_markup=server_list_keyboard())
            return
        await callback_query.message.edit_text(f"📡 Server: <b>{server['name']}</b>", parse_mode='HTML', reply_markup=server_action_keyboard(server_id))
        return

    if data.startswith("edit_"):
        server_id = data.split("_")[1]
        await callback_query.message.edit_text("🛠️ Edit Server:", reply_markup=edit_server_keyboard(server_id))
        return

    if data.startswith("change_name_"):
        server_id = data.split("_")[2]
        user_states[user_id] = f'changing_name_{server_id}'
        await callback_query.message.edit_text("✏️ Enter new name:", reply_markup=cancel_back_keyboard())
        return

    if data.startswith("change_username_"):
        server_id = data.split("_")[2]
        user_states[user_id] = f'changing_username_{server_id}'
        await callback_query.message.edit_text("👤 Enter new username:", reply_markup=cancel_back_keyboard())
        return

    if data.startswith("delete_"):
        server_id = data.split("_")[1]
        delete_server_by_id(server_id)
        await callback_query.message.edit_text("🗑️ Server deleted.", reply_markup=server_list_keyboard())
        return

    if data.startswith("info_"):
        server_id = data.split("_")[1]
        server = get_server_by_id(server_id)
        if not server:
            await callback_query.message.edit_text("❌ Server not found.", reply_markup=server_list_keyboard())
            return

        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            key = paramiko.RSAKey.from_private_key_file(server['key_file'])
            ssh.connect(server['ip'], username=server['username'], pkey=key)

            stdin, stdout, stderr = ssh.exec_command("uname -a")
            os_info = stdout.read().decode().strip()

            stdin, stdout, stderr = ssh.exec_command("uptime -p")
            uptime = stdout.read().decode().strip()

            stdin, stdout, stderr = ssh.exec_command("free -m")
            ram = stdout.read().decode().strip()

            stdin, stdout, stderr = ssh.exec_command("df -h /")
            disk = stdout.read().decode().strip()

            ssh.close()

            info = f"<b>👤 User:</b> {server['username']}\n<b>🌐 IP:</b> {server['ip']}\n<b>🖥️ OS:</b> {os_info}\n<b>⏱️ Uptime:</b> {uptime}\n\n<pre>{ram}</pre>\n<pre>{disk}</pre>"
            await callback_query.message.edit_text(info, parse_mode='HTML', reply_markup=server_action_keyboard(server_id))
        except Exception as e:
            await callback_query.message.edit_text(f"❌ Error fetching server info:\n{e}", reply_markup=server_action_keyboard(server_id))
        return

    await callback_query.answer()


@dp.message_handler()
async def handle_messages(message: types.Message):
    user_id = message.from_user.id
    state = user_states.get(user_id)

    if not state:
        return await message.answer("❓ Please use /start to begin.")

    if state == 'adding_name':
        temp_server_data[user_id]['name'] = message.text
        user_states[user_id] = 'adding_username'
        return await message.answer("👤 Enter username:", reply_markup=cancel_back_keyboard())

    if state == 'adding_username':
        temp_server_data[user_id]['username'] = message.text
        user_states[user_id] = 'adding_ip'
        return await message.answer("🌐 Enter IP address:", reply_markup=cancel_back_keyboard())

    if state == 'adding_ip':
        temp_server_data[user_id]['ip'] = message.text
        user_states[user_id] = 'adding_key'
        return await message.answer("📂 Send SSH private key file:", reply_markup=cancel_back_keyboard())

    if state.startswith("changing_name_"):
        server_id = state.split("_")[2]
        update_server_name(server_id, message.text)
        user_states.pop(user_id, None)
        await message.answer("✅ Name updated.", reply_markup=server_list_keyboard())
        return

    if state.startswith("changing_username_"):
        server_id = state.split("_")[2]
        update_server_username(server_id, message.text)
        user_states.pop(user_id, None)
        await message.answer("✅ Username updated.", reply_markup=server_list_keyboard())
        return


@dp.message_handler(content_types=types.ContentType.DOCUMENT)
async def handle_document(message: types.Message):
    user_id = message.from_user.id
    if user_states.get(user_id) != 'adding_key':
        return

    doc = message.document
    file_path = f"ssh_keys/{doc.file_name}"
    await doc.download(destination_file=file_path)

    temp = temp_server_data[user_id]

    try:
        key = paramiko.RSAKey.from_private_key_file(file_path)
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(temp['ip'], username=temp['username'], pkey=key)
        ssh.close()
    except Exception as e:
        await message.answer(f"❌ SSH connection failed: {e}", reply_markup=cancel_back_keyboard())
        return

    temp['key_file'] = file_path
    add_server(temp)
    user_states.pop(user_id, None)
    temp_server_data.pop(user_id, None)
    await message.answer("✅ Server added successfully!", reply_markup=server_list_keyboard())


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)


