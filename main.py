import asyncio
import os
import platform
import paramiko
import socket
import time
import psutil
import distro

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           InputFile, Message)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.default import DefaultBotProperties

from config import BOT_TOKEN, ADMIN_IDS
from db import (add_server, delete_server_by_id, get_servers,
                update_server_name, update_server_username, get_server_by_id)

bot = Bot(
    token=BOT_TOKEN,
    session=AiohttpSession(),
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

user_sessions = {}

# UTILITIES

async def try_ssh_connection(username, ip, key_path):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(ip, username=username, key_filename=key_path, timeout=10)
        client.close()
        return True
    except Exception as e:
        return False

async def get_server_stats(username, ip, key_path):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(ip, username=username, key_filename=key_path, timeout=10)
        stdin, stdout, stderr = client.exec_command("uptime -p")
        uptime = stdout.read().decode().strip()

        stdin, stdout, stderr = client.exec_command("free -m")
        mem_lines = stdout.read().decode().splitlines()
        total_mem = mem_lines[1].split()[1]
        used_mem = mem_lines[1].split()[2]

        stdin, stdout, stderr = client.exec_command("df -h /")
        disk_lines = stdout.read().decode().splitlines()
        disk_info = disk_lines[1].split()
        total_disk, used_disk = disk_info[1], disk_info[2]

        stdin, stdout, stderr = client.exec_command("nproc")
        cpu_cores = stdout.read().decode().strip()

        stdin, stdout, stderr = client.exec_command("uname -o")
        os_info = stdout.read().decode().strip()

        client.close()
        return {
            "uptime": uptime,
            "total_ram": total_mem,
            "used_ram": used_mem,
            "total_disk": total_disk,
            "used_disk": used_disk,
            "cpu_cores": cpu_cores,
            "os": os_info
        }
    except Exception as e:
        return None

# UI HELPERS

def main_menu():
    kb = InlineKeyboardBuilder()
    servers = get_servers()
    for server in servers:
        kb.button(text=f"🖥 {server['name']}", callback_data=f"server:{server['_id']}")
    kb.button(text="➕ Add Server", callback_data="add_server")
    kb.adjust(2)
    return kb.as_markup()

def server_menu(server_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📁 File Manager", callback_data=f"file:{server_id}"),
         InlineKeyboardButton(text="🤖 Bot Manager", callback_data=f"bot:{server_id}")],
        [InlineKeyboardButton(text="📊 Server Info", callback_data=f"info:{server_id}"),
         InlineKeyboardButton(text="✏️ Edit", callback_data=f"edit:{server_id}")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="back")]
    ])

def edit_menu(server_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Edit Name", callback_data=f"edit_name:{server_id}"),
         InlineKeyboardButton(text="👤 Edit Username", callback_data=f"edit_user:{server_id}")],
        [InlineKeyboardButton(text="🗑 Delete Server", callback_data=f"delete:{server_id}")],
        [InlineKeyboardButton(text="🔙 Back", callback_data=f"server:{server_id}")]
    ])

# HANDLERS

@dp.message()
async def handle_start(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return await message.answer("⛔ Unauthorized")
    await message.answer("<b>🔧 Server Manager</b>", reply_markup=main_menu())

@dp.callback_query()
async def handle_callback(query: types.CallbackQuery):
    data = query.data
    user_id = query.from_user.id

    if data == "back":
        await query.message.edit_text("<b>🔧 Server Manager</b>", reply_markup=main_menu())

    elif data == "add_server":
        user_sessions[user_id] = {"step": "name"}
        await query.message.edit_text("🖥 Enter server name:\n/cancel to stop")

    elif data.startswith("server:"):
        server_id = data.split(":")[1]
        server = get_server_by_id(server_id)
        if not server:
            return await query.message.edit_text("❌ Server not found", reply_markup=main_menu())
        await query.message.edit_text(f"<b>🖥 {server['name']} Menu</b>", reply_markup=server_menu(server_id))

    elif data.startswith("edit:"):
        server_id = data.split(":")[1]
        await query.message.edit_text("<b>✏️ Edit Server</b>", reply_markup=edit_menu(server_id))

    elif data.startswith("edit_name:"):
        server_id = data.split(":")[1]
        user_sessions[user_id] = {"step": "edit_name", "server_id": server_id}
        await query.message.edit_text("✏️ Enter new name:\n/cancel to stop")

    elif data.startswith("edit_user:"):
        server_id = data.split(":")[1]
        user_sessions[user_id] = {"step": "edit_user", "server_id": server_id}
        await query.message.edit_text("👤 Enter new username:\n/cancel to stop")

    elif data.startswith("delete:"):
        server_id = data.split(":")[1]
        delete_server_by_id(server_id)
        await query.message.edit_text("✅ Server deleted", reply_markup=main_menu())

    elif data.startswith("info:"):
        server_id = data.split(":")[1]
        server = get_server_by_id(server_id)
        if not server:
            return await query.message.edit_text("❌ Server not found", reply_markup=main_menu())

        stats = await get_server_stats(server['username'], server['ip'], server['key_path'])
        if not stats:
            return await query.message.edit_text("❌ Unable to fetch server stats", reply_markup=server_menu(server_id))

        text = (
            f"<b>🖥 Server Info</b>\n"
            f"👤 User: <code>{server['username']}</code>\n"
            f"🌐 IP: <code>{server['ip']}</code>\n"
            f"🖥 OS: <code>{stats['os']}</code>\n"
            f"⏱ Uptime: <code>{stats['uptime']}</code>\n"
            f"💾 RAM: <code>{stats['used_ram']} / {stats['total_ram']} MB</code>\n"
            f"🗄 Disk: <code>{stats['used_disk']} / {stats['total_disk']}</code>\n"
            f"🧠 CPU Cores: <code>{stats['cpu_cores']}</code>"
        )
        await query.message.edit_text(text, reply_markup=server_menu(server_id))

    elif data.startswith("file:"):
        await query.message.edit_text("📁 File Manager\nComing soon...")
    elif data.startswith("bot:"):
        await query.message.edit_text("🤖 Bot Manager\nComing soon...")

@dp.message(lambda m: m.text.lower() == "/cancel")
async def handle_cancel(message: Message):
    user_sessions.pop(message.from_user.id, None)
    await message.answer("❌ Cancelled", reply_markup=main_menu())

@dp.message()
async def handle_input(message: Message):
    user_id = message.from_user.id
    if user_id not in user_sessions:
        return await message.answer("❌ No active input", reply_markup=main_menu())

    session = user_sessions[user_id]
    step = session["step"]

    if step == "name":
        session["name"] = message.text
        session["step"] = "username"
        return await message.answer("👤 Enter SSH username:")

    elif step == "username":
        session["username"] = message.text
        session["step"] = "ip"
        return await message.answer("🌐 Enter server IP:")

    elif step == "ip":
        session["ip"] = message.text
        session["step"] = "key"
        return await message.answer("📎 Send SSH key file (.pem or .ppk):")

    elif step == "edit_name":
        update_server_name(session["server_id"], message.text)
        user_sessions.pop(user_id)
        return await message.answer("✅ Name updated", reply_markup=main_menu())

    elif step == "edit_user":
        update_server_username(session["server_id"], message.text)
        user_sessions.pop(user_id)
        return await message.answer("✅ Username updated", reply_markup=main_menu())

@dp.message(lambda m: m.document and user_sessions.get(m.from_user.id, {}).get("step") == "key")
async def handle_key_file(message: Message):
    user_id = message.from_user.id
    session = user_sessions[user_id]
    key_file = message.document
    path = f"keys/{key_file.file_name}"
    await message.bot.download(file=key_file.file_id, destination=path)

    ok = await try_ssh_connection(session['username'], session['ip'], path)
    if not ok:
        os.remove(path)
        return await message.answer("❌ SSH connection failed. Please try again.", reply_markup=main_menu())

    add_server({
        "name": session['name'],
        "username": session['username'],
        "ip": session['ip'],
        "key_path": path
    })
    user_sessions.pop(user_id)
    await message.answer("✅ Server added successfully", reply_markup=main_menu())

if __name__ == '__main__':
    asyncio.run(dp.start_polling(bot))
