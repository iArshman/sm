import logging
import paramiko
import re
import os
from datetime import datetime
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# Initialize user state for file manager
def init_file_manager(dp, bot, active_sessions, user_input):
    # --- HELPER: EXECUTE SSH COMMAND ---
    def execute_ssh_command(ssh, command):
        try:
            stdin, stdout, stderr = ssh.exec_command(command)
            stdout_data = stdout.read().decode('utf-8').strip()
            stderr_data = stderr.read().decode('utf-8').strip()
            if stderr_data:
                logger.warning(f"SSH command '{command}' error: {stderr_data}")
            return stdout_data, stderr_data
        except Exception as e:
            logger.error(f"SSH command '{command}' failed: {e}")
            raise

    # --- HELPER: SANITIZE PATH ---
    def sanitize_path(path):
        # Remove dangerous characters and normalize path
        path = re.sub(r'[;&|`\n\r]', '', path)
        path = os.path.normpath(path).replace('\\', '/')
        if path.startswith('/'):
            return path
        return '/' + path

    # --- HELPER: PARSE LS OUTPUT ---
    def parse_ls_output(output):
        files = []
        for line in output.splitlines():
            # Example: -rw-r--r-- 1 ubuntu ubuntu 1234 Oct 10 12:34 file.txt
            #          drwxr-xr-x 2 ubuntu ubuntu 4096 Oct 10 12:34 dir
            match = re.match(r'^([drwx-]+)\s+\d+\s+\S+\s+\S+\s+(\d+)\s+(\w+\s+\d+\s+\d+:\d+|\w+\s+\d+\s+\d+)\s+(.+)$', line)
            if match:
                perms, size, mtime, name = match.groups()
                is_dir = perms.startswith('d')
                try:
                    mtime_dt = datetime.strptime(mtime, '%b %d %H:%M')
                    mtime_str = mtime_dt.replace(year=datetime.now().year).strftime('%Y-%m-%d %H:%M')
                except ValueError:
                    try:
                        mtime_dt = datetime.strptime(mtime, '%b %d %Y')
                        mtime_str = mtime_dt.strftime('%Y-%m-%d')
                    except ValueError:
                        mtime_str = mtime
                files.append({
                    'name': name.strip(),
                    'is_dir': is_dir,
                    'size': int(size),
                    'mtime': mtime_str,
                    'perms': perms
                })
        return files

    # --- HELPER: GET FILE LIST ---
    async def get_file_list(server_id, path, ssh):
        try:
            path = sanitize_path(path)
            command = f'ls -l --time-style=+"%b %d %H:%M" "{path}"'
            stdout_data, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data and "No such file or directory" in stderr_data:
                return None, f"Directory '{path}' not found"
            files = parse_ls_output(stdout_data)
            return files, None
        except Exception as e:
            logger.error(f"Failed to list files in {path}: {e}")
            return None, str(e)

    # --- HELPER: BUILD FILE MANAGER KEYBOARD ---
    def build_file_keyboard(server_id, path, files):
        kb = InlineKeyboardMarkup(row_width=1)
        # Navigation buttons
        if path != '/':
            kb.add(InlineKeyboardButton("⬆️ Parent Directory", callback_data=f"fm_nav_{server_id}_.."))
        # File/directory buttons
        for f in sorted(files, key=lambda x: (not x['is_dir'], x['name'].lower())):
            icon = "📁" if f['is_dir'] else "📄"
            label = f"{icon} {f['name']} ({f['size']} B, {f['mtime']}, {f['perms']})"
            if f['is_dir']:
                cb_data = f"fm_nav_{server_id}_{f['name']}"
            else:
                cb_data = f"fm_file_{server_id}_{f['name']}"
            kb.add(InlineKeyboardButton(label, callback_data=cb_data))
        # Action buttons
        kb.add(
            InlineKeyboardButton("📤 Upload File", callback_data=f"fm_upload_{server_id}"),
            InlineKeyboardButton("📁 New Folder", callback_data=f"fm_new_folder_{server_id}")
        )
        kb.add(InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}"))
        return kb

    # --- HELPER: BACK BUTTON ---
    def back_button(callback_data):
        return InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Back", callback_data=callback_data))

    # --- HELPER: CANCEL BUTTON ---
    def cancel_button():
        return InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Cancel", callback_data="fm_cancel"))

    # --- FILE MANAGER ENTRY ---
    @dp.callback_query_handler(lambda c: c.data.startswith("file_manager_"))
    async def file_manager_start(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session for this server.")
                return
            user_input[callback.from_user.id] = {
                'server_id': server_id,
                'current_path': '/home/ubuntu',
                'mode': 'file_manager'
            }
            ssh = active_sessions[server_id]
            path = user_input[callback.from_user.id]['current_path']
            files, error = await get_file_list(server_id, path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, path, files)
            text = f"🗂 File Manager: {path}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"File manager start error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error loading file manager.", reply_markup=back_button(f"server_{server_id}"))

    # --- NAVIGATE DIRECTORY ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_nav_"))
    async def navigate_directory(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            dir_name = parts[3] if len(parts) > 3 else '..'
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            current_path = user_state['current_path']
            if dir_name == '..':
                new_path = '/'.join(current_path.rstrip('/').split('/')[:-1]) or '/'
            else:
                new_path = f"{current_path.rstrip('/')}/{dir_name}"
            new_path = sanitize_path(new_path)
            user_state['current_path'] = new_path
            ssh = active_sessions[server_id]
            files, error = await get_file_list(server_id, new_path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, new_path, files)
            text = f"🗂 File Manager: {new_path}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Navigate directory error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error navigating directory.", reply_markup=back_button(f"server_{server_id}"))

    # --- FILE ACTIONS ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_file_"))
    async def file_actions(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            file_name = parts[3]
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            current_path = user_state['current_path']
            file_path = f"{current_path.rstrip('/')}/{file_name}"
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("📥 Download", callback_data=f"fm_download_{server_id}_{file_name}"),
                InlineKeyboardButton("🗑 Delete", callback_data=f"fm_delete_{server_id}_{file_name}")
            )
            kb.add(
                InlineKeyboardButton("✏️ Rename", callback_data=f"fm_rename_{server_id}_{file_name}"),
                InlineKeyboardButton("👁️ View", callback_data=f"fm_view_{server_id}_{file_name}")
            )
            kb.add(
                InlineKeyboardButton("🔒 Permissions", callback_data=f"fm_perms_{server_id}_{file_name}"),
                InlineKeyboardButton("⬅️ Back", callback_data=f"fm_refresh_{server_id}")
            )
            text = f"📄 File: {file_name}\nPath: {current_path}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"File actions error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error loading file actions.", reply_markup=back_button(f"server_{server_id}"))

    # --- DOWNLOAD FILE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_download_"))
    async def download_file(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            file_name = parts[3]
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            current_path = user_state['current_path']
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            ssh = active_sessions[server_id]
            sftp = ssh.open_sftp()
            try:
                stat = sftp.stat(file_path)
                if stat.st_size > 10 * 1024 * 1024:  # 10MB limit
                    await callback.message.edit_text("❌ File too large to download (>10MB).", reply_markup=back_button(f"server_{server_id}"))
                    return
                with sftp.file(file_path, 'rb') as remote_file:
                    await callback.message.edit_text(f"📥 Downloading {file_name}...")
                    await bot.send_document(
                        callback.from_user.id,
                        types.InputFile(remote_file, filename=file_name),
                        caption=f"File from {current_path}"
                    )
            finally:
                sftp.close()
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, current_path, files)
            text = f"🗂 File Manager: {current_path}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Download file error for server {server_id}: {e}")
            await callback.message.edit_text(f"❌ Error downloading file: {str(e)}", reply_markup=back_button(f"server_{server_id}"))

    # --- DELETE FILE CONFIRM ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_delete_"))
    async def delete_file_confirm(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            file_name = parts[3]
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            current_path = user_state['current_path']
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("✅ Yes, delete", callback_data=f"fm_delete_confirm_{server_id}_{file_name}"),
                InlineKeyboardButton("⬅️ Back", callback_data=f"fm_refresh_{server_id}")
            )
            text = f"⚠️ Are you sure you want to delete '{file_name}' from {current_path}?"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Delete file confirm error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error initiating file deletion.", reply_markup=back_button(f"server_{server_id}"))

    # --- DELETE FILE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_delete_confirm_"))
    async def delete_file(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 4)
            server_id = parts[3]
            file_name = parts[4]
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            current_path = user_state['current_path']
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            command = f'rm -f "{file_path}"'
            ssh = active_sessions[server_id]
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await callback.message.edit_text(f"❌ Error deleting file: {stderr_data}", reply_markup=back_button(f"server_{server_id}"))
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, current_path, files)
            text = f"🗂 File Manager: {current_path}\n✅ File '{file_name}' deleted."
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Delete file error for server {server_id}: {e}")
            await callback.message.edit_text(f"❌ Error deleting file: {str(e)}", reply_markup=back_button(f"server_{server_id}"))

    # --- UPLOAD FILE START ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_upload_"))
    async def upload_file_start(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            user_state['mode'] = 'file_upload'
            user_state['server_id'] = server_id
            text = f"📤 Please send a file to upload to {user_state['current_path']} (max 10MB)."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Upload file start error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error initiating file upload.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE FILE UPLOAD ---
    @dp.message_handler(content_types=types.ContentType.DOCUMENT)
    async def handle_file_upload(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            if user_state.get('mode') != 'file_upload':
                await message.answer("❌ Not in file upload mode.")
                return
            server_id = user_state.get('server_id')
            if server_id not in active_sessions:
                await message.answer("❌ No active SSH session.")
                return
            if message.document.file_size > 10 * 1024 * 1024:  # 10MB limit
                await message.answer("❌ File too large to upload (>10MB).")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            file_name = message.document.file_name
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            await message.answer(f"📤 Uploading {file_name} to {current_path}...")
            file = await bot.download_file_by_id(message.document.file_id)
            ssh = active_sessions[server_id]
            sftp = ssh.open_sftp()
            try:
                with sftp.file(file_path, 'wb') as remote_file:
                    remote_file.write(file.read())
            finally:
                sftp.close()
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {error}")
                return
            kb = build_file_keyboard(server_id, current_path, files)
            text = f"🗂 File Manager: {current_path}\n✅ File '{file_name}' uploaded."
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            user_state['mode'] = 'file_manager'
        except Exception as e:
            logger.error(f"File upload error for server {server_id}: {e}")
            await message.answer(f"❌ Error uploading file: {str(e)}")
        finally:
            if user_state.get('mode') == 'file_upload':
                user_state['mode'] = 'file_manager'

    # --- CANCEL UPLOAD ---
    @dp.callback_query_handler(lambda c: c.data == "fm_cancel")
    async def cancel_upload(callback: types.CallbackQuery):
        try:
            user_state = user_input.get(callback.from_user.id, {})
            server_id = user_state.get('server_id')
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state['mode'] = 'file_manager'
            current_path = user_state.get('current_path', '/home/ubuntu')
            ssh = active_sessions[server_id]
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, current_path, files)
            text = f"🗂 File Manager: {current_path}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Cancel upload error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error cancelling upload.", reply_markup=back_button(f"server_{server_id}"))

    # --- NEW FOLDER START ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_new_folder_"))
    async def new_folder_start(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[3]
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            user_state['mode'] = 'new_folder'
            user_state['server_id'] = server_id
            text = f"📁 Please send the name for the new folder in {user_state['current_path']}."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"New folder start error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error initiating new folder creation.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE NEW FOLDER ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'new_folder')
    async def handle_new_folder(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            if server_id not in active_sessions:
                await message.answer("❌ No active SSH session.")
                return
            folder_name = re.sub(r'[;&|`\n\r/]', '', message.text.strip())
            if not folder_name:
                await message.answer("❌ Invalid folder name.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            folder_path = sanitize_path(f"{current_path.rstrip('/')}/{folder_name}")
            command = f'mkdir "{folder_path}"'
            ssh = active_sessions[server_id]
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await message.answer(f"❌ Error creating folder: {stderr_data}")
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {error}")
                return
            kb = build_file_keyboard(server_id, current_path, files)
            text = f"🗂 File Manager: {current_path}\n✅ Folder '{folder_name}' created."
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            user_state['mode'] = 'file_manager'
        except Exception as e:
            logger.error(f"New folder error for server {server_id}: {e}")
            await message.answer(f"❌ Error creating folder: {str(e)}")
        finally:
            if user_state.get('mode') == 'new_folder':
                user_state['mode'] = 'file_manager'

    # --- RENAME FILE START ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_rename_"))
    async def rename_file_start(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            file_name = parts[3]
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            user_state['mode'] = 'rename_file'
            user_state['old_name'] = file_name
            text = f"✏️ Please send the new name for '{file_name}' in {user_state['current_path']}."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Rename file start error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error initiating file rename.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE RENAME FILE ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'rename_file')
    async def handle_rename_file(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            if server_id not in active_sessions:
                await message.answer("❌ No active SSH session.")
                return
            old_name = user_state.get('old_name')
            new_name = re.sub(r'[;&|`\n\r/]', '', message.text.strip())
            if not new_name:
                await message.answer("❌ Invalid file name.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            old_path = sanitize_path(f"{current_path.rstrip('/')}/{old_name}")
            new_path = sanitize_path(f"{current_path.rstrip('/')}/{new_name}")
            command = f'mv "{old_path}" "{new_path}"'
            ssh = active_sessions[server_id]
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await message.answer(f"❌ Error renaming file: {stderr_data}")
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {error}")
                return
            kb = build_file_keyboard(server_id, current_path, files)
            text = f"🗂 File Manager: {current_path}\n✅ File '{old_name}' renamed to '{new_name}'."
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            user_state['mode'] = 'file_manager'
        except Exception as e:
            logger.error(f"Rename file error for server {server_id}: {e}")
            await message.answer(f"❌ Error renaming file: {str(e)}")
        finally:
            if user_state.get('mode') == 'rename_file':
                user_state['mode'] = 'file_manager'

    # --- VIEW FILE CONTENT ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_view_"))
    async def view_file_content(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            file_name = parts[3]
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            current_path = user_state['current_path']
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            ssh = active_sessions[server_id]
            sftp = ssh.open_sftp()
            try:
                stat = sftp.stat(file_path)
                if stat.st_size > 1024 * 1024:  # 1MB limit for viewing
                    await callback.message.edit_text("❌ File too large to view (>1MB).", reply_markup=back_button(f"fm_refresh_{server_id}"))
                    return
                # Check if file is likely text
                command = f'file "{file_path}"'
                stdout_data, stderr_data = execute_ssh_command(ssh, command)
                if stderr_data or 'text' not in stdout_data.lower():
                    await callback.message.edit_text("❌ Can only view text files.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                    return
                with sftp.file(file_path, 'r') as remote_file:
                    content = remote_file.read(4096).decode('utf-8', errors='replace')  # Read first 4KB
                    if len(content) == 4096:
                        content = content[:4000] + "... (truncated)"
                text = f"📄 File: {file_name}\nPath: {current_path}\n\nContent:\n```\n{content}\n```"
                await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=back_button(f"fm_refresh_{server_id}"))
            finally:
                sftp.close()
        except Exception as e:
            logger.error(f"View file error for server {server_id}: {e}")
            await callback.message.edit_text(f"❌ Error viewing file: {str(e)}", reply_markup=back_button(f"server_{server_id}"))

    # --- CHANGE PERMISSIONS START ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_perms_"))
    async def change_permissions_start(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            file_name = parts[3]
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            user_state['mode'] = 'change_perms'
            user_state['file_name'] = file_name
            text = f"🔒 Please send the new permissions for '{file_name}' in octal format (e.g., 644) in {user_state['current_path']}."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Change permissions start error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error initiating permissions change.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE CHANGE PERMISSIONS ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'change_perms')
    async def handle_change_permissions(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            if server_id not in active_sessions:
                await message.answer("❌ No active SSH session.")
                return
            file_name = user_state.get('file_name')
            perms = message.text.strip()
            if not re.match(r'^\d{3,4}$', perms):
                await message.answer("❌ Invalid permissions format. Use octal (e.g., 644).")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            command = f'chmod {perms} "{file_path}"'
            ssh = active_sessions[server_id]
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await message.answer(f"❌ Error changing permissions: {stderr_data}")
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {error}")
                return
            kb = build_file_keyboard(server_id, current_path, files)
            text = f"🗂 File Manager: {current_path}\n✅ Permissions for '{file_name}' changed to {perms}."
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            user_state['mode'] = 'file_manager'
        except Exception as e:
            logger.error(f"Change permissions error for server {server_id}: {e}")
            await message.answer(f"❌ Error changing permissions: {str(e)}")
        finally:
            if user_state.get('mode') == 'change_perms':
                user_state['mode'] = 'file_manager'

    # --- REFRESH FILE LIST ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_refresh_"))
    async def refresh_file_list(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            current_path = user_state['current_path']
            ssh = active_sessions[server_id]
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, current_path, files)
            text = f"🗂 File Manager: {current_path}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Refresh file list error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error refreshing file list.", reply_markup=back_button(f"server_{server_id}"))
