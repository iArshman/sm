import logging
import os
import io
import paramiko
import base64
from aiogram import Dispatcher, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_file_manager(dp: Dispatcher, bot, active_sessions, user_input):
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_") or c.data.startswith("file_") or c.data.startswith("select_") or c.data.startswith("zip_") or c.data.startswith("unzip_"))
    async def handle_file_actions(callback: types.CallbackQuery):
        try:
            data = callback.data.split("_")
            action = data[0]
            server_id = data[1]
            path_b64 = "_".join(data[2:]) if len(data) > 2 else ""
            current_path = base64.b64decode(path_b64).decode() if path_b64 else "/home/ubuntu"
            ssh = active_sessions.get(server_id)
            if not ssh:
                await callback.message.edit_text("❌ No active SSH session.")
                return

            uid = callback.from_user.id
            if action == "fm":
                if uid not in user_input:
                    user_input[uid] = {}
                user_input[uid]["select_mode"] = False
                user_input[uid]["selected_items"] = []
                await list_files(callback.message, server_id, current_path, bot, user_input)
            elif action == "file":
                file_name = base64.b64decode("_".join(data[2:])).decode()
                await show_file_options(callback, server_id, os.path.join(current_path, file_name), user_input)
            elif action == "select":
                await toggle_select_mode(callback, server_id, current_path, user_input)
            elif action == "zip":
                await zip_selected(callback, server_id, current_path, user_input)
            elif action == "unzip":
                zip_path = base64.b64decode("_".join(data[2:])).decode()
                await unzip_file(callback, server_id, zip_path, current_path, user_input)
            else:
                # Handle file-specific actions (download, delete, etc.)
                file_path = base64.b64decode("_".join(data[1:])).decode()
                if action == "download":
                    try:
                        sftp = ssh.open_sftp()
                        stat = sftp.stat(file_path)
                        if stat.st_size > 50 * 1024 * 1024:  # 50MB limit
                            await callback.message.edit_text("❌ File too large (>50MB) for Telegram.")
                            sftp.close()
                            return
                        with sftp.file(file_path, 'r') as remote_file:
                            file_content = remote_file.read()
                        sftp.close()
                        file_io = io.BytesIO(file_content)
                        file_io.name = os.path.basename(file_path)
                        await callback.message.edit_text("📥 Downloading...")
                        await bot.send_document(uid, file_io)
                    except (paramiko.SFTPError, IOError) as e:
                        logger.error(f"Download error for {file_path}: {e}")
                        await callback.message.edit_text(f"❌ Download failed: {str(e)}")
                elif action == "delete":
                    try:
                        sftp = ssh.open_sftp()
                        if os.path.basename(file_path).startswith(".") or file_path.endswith("/"):
                            sftp.rmdir(file_path)
                        else:
                            sftp.remove(file_path)
                        sftp.close()
                        await callback.message.edit_text("🗑 File deleted.")
                    except (paramiko.SFTPError, IOError) as e:
                        logger.error(f"Delete error for {file_path}: {e}")
                        await callback.message.edit_text(f"❌ Delete failed: {str(e)}")
                elif action == "upload":
                    user_input[uid] = {
                        "server_id": server_id,
                        "path": current_path,
                        "action": "upload"
                    }
                    await callback.message.edit_text("📤 Please send the file to upload.", reply_markup=back_button(f"fm_{server_id}_{base64.b64encode(current_path.encode()).decode()}"))
                elif action == "download_selected":
                    await download_selected(callback, server_id, user_input)
                elif action == "delete_selected":
                    await delete_selected(callback, server_id, user_input)
                await list_files(callback.message, server_id, current_path, bot, user_input)
        except Exception as e:
            logger.error(f"File action error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.message_handler(content_types=types.ContentType.DOCUMENT)
    async def handle_file_upload(message: types.Message, active_sessions=active_sessions, user_input=user_input):
        uid = message.from_user.id
        if uid not in user_input or user_input[uid].get("action") != "upload":
            return
        server_id = user_input[uid]["server_id"]
        current_path = user_input[uid]["path"]
        try:
            ssh = active_sessions.get(server_id)
            if not ssh:
                raise ValueError("No active SSH session")
            file = await bot.download_file_by_id(message.document.file_id)
            file_name = message.document.file_name
            file_path = os.path.join(current_path, file_name)
            sftp = ssh.open_sftp()
            try:
                sftp.stat(file_path)
                await message.answer("❌ File already exists. Please rename and try again.")
                sftp.close()
                return
            except (paramiko.SFTPError, IOError):
                pass
            with sftp.file(file_path, 'wb') as remote_file:
                remote_file.write(file.read())
            sftp.close()
            await message.answer("✅ File uploaded successfully!")
        except (paramiko.SFTPError, IOError) as e:
            logger.error(f"Upload error for {file_path}: {e}")
            await message.answer(f"❌ Upload failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected upload error: {e}")
            await message.answer(f"❌ Error uploading file: {str(e)}")
        finally:
            user_input.pop(uid, None)
            await list_files(message, server_id, current_path, bot, user_input)

async def list_files(message: types.Message, server_id: str, path: str, bot, user_input):
    try:
        ssh = active_sessions.get(server_id)
        if not ssh:
            await message.edit_text("❌ No active SSH session.")
            return
        stdin, stdout, stderr = ssh.exec_command(f'ls -1 "{path}"')
        ls_output = stdout.read().decode().strip()
        stderr_output = stderr.read().decode().strip()
        logger.debug(f"ls -1 output for {path}: {ls_output}")
        if stderr_output:
            logger.warning(f"ls error: {stderr_output}")
        files = [f for f in ls_output.splitlines() if not (f.startswith(".") or f.endswith("~") or f.endswith(".bak"))]
        uid = message.from_user.id
        select_mode = user_input.get(uid, {}).get("select_mode", False)
        selected_items = user_input.get(uid, {}).get("selected_items", [])
        kb = InlineKeyboardMarkup(row_width=2)
        for file in sorted(files):
            file_path = os.path.join(path, file)
            sftp = ssh.open_sftp()
            is_dir = sftp.lstat(file_path).st_mode & 0o40000
            sftp.close()
            prefix = "✅" if file_path in selected_items else "⬜"
            label = f"📁 {file}" if is_dir else f"📄 {file}"
            callback_data = f"file_{server_id}_{base64.b64encode(file_path.encode()).decode()}" if not select_mode else f"select_{server_id}_{base64.b64encode(file_path.encode()).decode()}"
            kb.add(InlineKeyboardButton(f"{prefix} {label}", callback_data=callback_data))
        kb.add(InlineKeyboardButton("⬆️ Parent", callback_data=f"fm_{server_id}_{base64.b64encode(os.path.dirname(path).encode()).decode()}"))
        kb.add(InlineKeyboardButton("📤 Upload File", callback_data=f"upload_{server_id}_{base64.b64encode(path.encode()).decode()}"))
        kb.add(InlineKeyboardButton(f"{'❌' if select_mode else '✅'} Select Items", callback_data=f"select_{server_id}_{base64.b64encode(path.encode()).decode()}"))
        if select_mode and selected_items:
            kb.add(
                InlineKeyboardButton("⬇️ Download Selected", callback_data=f"download_selected_{server_id}_{base64.b64encode(path.encode()).decode()}"),
                InlineKeyboardButton("🗑 Delete Selected", callback_data=f"delete_selected_{server_id}_{base64.b64encode(path.encode()).decode()}"),
                InlineKeyboardButton("📦 Zip Selected", callback_data=f"zip_{server_id}_{base64.b64encode(path.encode()).decode()}"),
            )
        await message.edit_text(f"📂 <b>{path}</b>", parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error(f"List files error: {e}")
        await message.edit_text(f"❌ Error listing files: {str(e)}")

async def show_file_options(callback: types.CallbackQuery, server_id: str, file_path: str, user_input):
    try:
        ssh = active_sessions.get(server_id)
        if not ssh:
            await callback.message.edit_text("❌ No active SSH session.")
            return
        sftp = ssh.open_sftp()
        is_dir = sftp.lstat(file_path).st_mode & 0o40000
        sftp.close()
        kb = InlineKeyboardMarkup(row_width=2)
        if is_dir:
            kb.add(InlineKeyboardButton("📂 Open", callback_data=f"fm_{server_id}_{base64.b64encode(file_path.encode()).decode()}"))
        else:
            kb.add(InlineKeyboardButton("⬇️ Download", callback_data=f"download_{server_id}_{base64.b64encode(file_path.encode()).decode()}"))
            if file_path.endswith(".zip"):
                kb.add(InlineKeyboardButton("📂 Unzip", callback_data=f"unzip_{server_id}_{base64.b64encode(file_path.encode()).decode()}"))
        kb.add(InlineKeyboardButton("🗑 Delete", callback_data=f"delete_{server_id}_{base64.b64encode(file_path.encode()).decode()}"))
        kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"fm_{server_id}_{base64.b64encode(os.path.dirname(file_path).encode()).decode()}"))
        await callback.message.edit_text(f"📄 <b>{os.path.basename(file_path)}</b>", parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error(f"Show file options error: {e}")
        await callback.message.edit_text(f"❌ Error: {str(e)}")

async def toggle_select_mode(callback: types.CallbackQuery, server_id: str, current_path: str, user_input):
    uid = callback.from_user.id
    if uid not in user_input:
        user_input[uid] = {}
    user_input[uid]["select_mode"] = not user_input[uid].get("select_mode", False)
    if user_input[uid]["select_mode"]:
        user_input[uid]["selected_items"] = []
        await callback.message.edit_text("✅ Selection mode enabled. Tap items to select.")
    else:
        user_input[uid]["selected_items"] = []
        await callback.message.edit_text("❌ Selection mode disabled.")
    await list_files(callback.message, server_id, current_path, bot, user_input)

async def download_selected(callback: types.CallbackQuery, server_id: str, user_input):
    uid = callback.from_user.id
    selected_items = user_input.get(uid, {}).get("selected_items", [])
    if not selected_items:
        await callback.message.edit_text("❌ No items selected.")
        return
    ssh = active_sessions.get(server_id)
    if not ssh:
        await callback.message.edit_text("❌ No active SSH session.")
        return
    for file_path in selected_items[:10]:  # Limit to 10 to avoid Telegram rate limits
        try:
            sftp = ssh.open_sftp()
            stat = sftp.stat(file_path)
            if stat.st_size > 50 * 1024 * 1024:
                await callback.message.answer(f"❌ {os.path.basename(file_path)} too large (>50MB).")
                sftp.close()
                continue
            with sftp.file(file_path, 'r') as remote_file:
                file_content = remote_file.read()
            sftp.close()
            file_io = io.BytesIO(file_content)
            file_io.name = os.path.basename(file_path)
            await bot.send_document(uid, file_io)
        except (paramiko.SFTPError, IOError) as e:
            logger.error(f"Download selected error for {file_path}: {e}")
            await callback.message.answer(f"❌ Failed to download {os.path.basename(file_path)}: {str(e)}")
    user_input[uid]["selected_items"] = []
    user_input[uid]["select_mode"] = False
    await callback.message.edit_text("✅ Download complete.")

async def delete_selected(callback: types.CallbackQuery, server_id: str, user_input):
    uid = callback.from_user.id
    selected_items = user_input.get(uid, {}).get("selected_items", [])
    if not selected_items:
        await callback.message.edit_text("❌ No items selected.")
        return
    ssh = active_sessions.get(server_id)
    if not ssh:
        await callback.message.edit_text("❌ No active SSH session.")
        return
    for file_path in selected_items:
        try:
            sftp = ssh.open_sftp()
            is_dir = sftp.lstat(file_path).st_mode & 0o40000
            if is_dir:
                sftp.rmdir(file_path)
            else:
                sftp.remove(file_path)
            sftp.close()
        except (paramiko.SFTPError, IOError) as e:
            logger.error(f"Delete selected error for {file_path}: {e}")
            await callback.message.answer(f"❌ Failed to delete {os.path.basename(file_path)}: {str(e)}")
    user_input[uid]["selected_items"] = []
    user_input[uid]["select_mode"] = False
    await callback.message.edit_text("✅ Selected items deleted.")

async def zip_selected(callback: types.CallbackQuery, server_id: str, current_path: str, user_input):
    uid = callback.from_user.id
    selected_items = user_input.get(uid, {}).get("selected_items", [])
    if not selected_items:
        await callback.message.edit_text("❌ No items selected.")
        return
    ssh = active_sessions.get(server_id)
    if not ssh:
        await callback.message.edit_text("❌ No active SSH session.")
        return
    try:
        # Check if zip is installed
        stdin, stdout, stderr = ssh.exec_command("command -v zip")
        zip_path = stdout.read().decode().strip()
        if not zip_path:
            await callback.message.edit_text("❌ 'zip' command not found on server. Install with: sudo apt-get install zip")
            return
        zip_name = f"archive_{int(datetime.now().timestamp())}.zip"
        zip_path = os.path.join(current_path, zip_name)
        files = [os.path.basename(f) for f in selected_items]
        cmd = f"cd {current_path} && zip -r {zip_name} {' '.join(files)}"
        stdin, stdout, stderr = ssh.exec_command(cmd)
        stderr_output = stderr.read().decode().strip()
        stdout_output = stdout.read().decode().strip()
        logger.debug(f"Zip command: {cmd}, Output: {stdout_output}, Error: {stderr_output}")
        if stderr_output:
            raise paramiko.SSHException(f"Zip error: {stderr_output}")
        await callback.message.edit_text(f"✅ Created {zip_name}.")
    except (paramiko.SSHException, paramiko.SFTPError) as e:
        logger.error(f"Zip error: {e}")
        await callback.message.edit_text(f"❌ Failed to create zip: {str(e)}")
    finally:
        user_input[uid]["selected_items"] = []
        user_input[uid]["select_mode"] = False
        await list_files(callback.message, server_id, current_path, bot, user_input)

async def unzip_file(callback: types.CallbackQuery, server_id: str, zip_path: str, current_path: str, user_input):
    ssh = active_sessions.get(server_id)
    if not ssh:
        await callback.message.edit_text("❌ No active SSH session.")
        return
    try:
        # Check if unzip is installed
        stdin, stdout, stderr = ssh.exec_command("command -v unzip")
        unzip_path = stdout.read().decode().strip()
        if not unzip_path:
            await callback.message.edit_text("❌ 'unzip' command not found on server. Install with: sudo apt-get install unzip")
            return
        cmd = f"cd {current_path} && unzip -o {os.path.basename(zip_path)}"
        stdin, stdout, stderr = ssh.exec_command(cmd)
        stderr_output = stderr.read().decode().strip()
        stdout_output = stdout.read().decode().strip()
        logger.debug(f"Unzip command: {cmd}, Output: {stdout_output}, Error: {stderr_output}")
        if stderr_output:
            raise paramiko.SSHException(f"Unzip error: {stderr_output}")
        await callback.message.edit_text(f"✅ Unzipped {os.path.basename(zip_path)}.")
    except (paramiko.SSHException, paramiko.SFTPError) as e:
        logger.error(f"Unzip error: {e}")
        await callback.message.edit_text(f"❌ Failed to unzip: {str(e)}")
    finally:
        await list_files(callback.message, server_id, current_path, bot, user_input)

from datetime import datetime
