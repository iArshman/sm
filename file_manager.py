import logging
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import paramiko
import re
from datetime import datetime

logger = logging.getLogger(__name__)

# Initialize user state for file manager (shared with main.py)
def init_file_manager(dp, bot, active_sessions, user_input):
    # --- HELPER: EXECUTE SSH COMMAND ---
    def execute_ssh_command(ssh, command):
        try:
            stdin, stdout, stderr = ssh.exec_command(command)
            stdout_data = stdout.read().decode().strip()
            stderr_data = stderr.read().decode().strip()
            if stderr_data:
                logger.warning(f"SSH command '{command}' error: {stderr_data}")
            return stdout_data, stderr_data
        except Exception as e:
            logger.error(f"SSH command '{command}' failed: {e}")
            raise

    # --- HELPER: PARSE LS OUTPUT ---
    def parse_ls_output(output):
        files = []
        for line in output.splitlines():
            # Example: -rw-r--r-- 1 ubuntu ubuntu 1234 Oct 10 12:34 file.txt
            #          drwxr-xr-x 2 ubuntu ubuntu 4096 Oct 10 12:34 dir
            match = re.match(r'^([drwx-]+)\s+\d+\s+\S+\s+\S+\s+(\d+)\s+(\w+\s+\d+\s+\d+:\d+)\s+(.+)$', line)
            if match:
                perms, size, mtime, name = match.groups()
                is_dir = perms.startswith('d')
                try:
                    mtime_dt = datetime.strptime(mtime, '%b %d %H:%M')
                    mtime_str = mtime_dt.strftime('%Y-%m-%d %H:%M')
                except ValueError:
                    mtime_str = mtime
                files.append({
                    'name': name,
                    'is_dir': is_dir,
                    'size': int(size),
                    'mtime': mtime_str
                })
        return files

    # --- HELPER: GET FILE LIST ---
    async def get_file_list(server_id, path, ssh):
        try:
            # Sanitize path to prevent command injection
            path = path.replace(';', '').replace('&', '').replace('|', '')
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
        # Add navigation buttons
        if path != '/':
            kb.add(InlineKeyboardButton("⬆️ Parent Directory", callback_data=f"fm_nav_{server_id}_.."))
        # Add file/directory buttons
        for f in sorted(files, key=lambda x: (not x['is_dir'], x['name'].lower())):
            icon = "📁" if f['is_dir'] else "📄"
            label = f"{icon} {f['name']} ({f['size']} B, {f['mtime']})"
            if f['is_dir']:
                cb_data = f"fm_nav_{server_id}_{f['name']}"
            else:
                cb_data = f"fm_file_{server_id}_{f['name']}"
            kb.add(InlineKeyboardButton(label, callback_data=cb_data))
        # Add action buttons
        kb.add(
            InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}"),
            InlineKeyboardButton("📤 Upload File", callback_data=f"fm_upload_{server_id}")
        )
        return kb

    # --- FILE MANAGER ENTRY ---
    @dp.callback_query_handler(lambda c: c.data.startswith("file_manager_"))
    async def file_manager_start(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session for this server.")
                return
            # Initialize user state
            user_input[callback.from_user.id] = {
                'server_id': server_id,
                'current_path': '/home/ubuntu',  # Default path
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
            parts = callback.data.split('_')
            server_id = parts[2]
            dir_name = '_'.join(parts[3:])  # Handle names with underscores
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
            # Normalize path
            new_path = '/' + '/'.join(p for p in new_path.split('/') if p) or '/'
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
            parts = callback.data.split('_')
            server_id = parts[2]
            file_name = '_'.join(parts[3:])  # Handle names with underscores
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
            kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"fm_refresh_{server_id}"))
            text = f"📄 File: {file_name}\nPath: {current_path}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"File actions error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error loading file actions.", reply_markup=back_button(f"server_{server_id}"))

    # --- DOWNLOAD FILE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_download_"))
    async def download_file(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            file_name = '_'.join(parts[3:])  # Handle names with underscores
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            current_path = user_state['current_path']
            file_path = f"{current_path.rstrip('/')}/{file_name}"
            ssh = active_sessions[server_id]
            sftp = ssh.open_sftp()
            with sftp.file(file_path, 'rb') as remote_file:
                await callback.message.edit_text(f"📥 Downloading {file_name}...")
                await bot.send_document(
                    callback.from_user.id,
                    types.InputFile(remote_file, filename=file_name),
                    caption=f"File from {current_path}"
                )
            sftp.close()
            # Refresh file list
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
            parts = callback.data.split('_')
            server_id = parts[2]
            file_name = '_'.join(parts[3:])  # Handle names with underscores
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
            parts = callback.data.split('_')
            server_id = parts[3]
            file_name = '_'.join(parts[4:])  # Handle names with underscores
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            current_path = user_state['current_path']
            file_path = f"{current_path.rstrip('/')}/{file_name}"
            # Sanitize file path
            file_path = file_path.replace('"', '').replace('`', '').replace('$', '')
            command = f'rm -f "{file_path}"'
            ssh = active_sessions[server_id]
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await callback.message.edit_text(f"❌ Error deleting file: {stderr_data}", reply_markup=back_button(f"server_{server_id}"))
                return
            # Refresh file list
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
            text = f"📤 Please send a file to upload to {user_state['current_path']}."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Upload file start error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error initiating file upload.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE FILE UPLOAD ---
    @dp.message_handler(content_types=types.ContentType.DOCUMENT, lambda m: m.from_user.id in user_input and user_input[m.from_user.id].get('mode') == 'file_upload')
    async def handle_file_upload(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            if server_id not in active_sessions:
                await message.answer("❌ No active SSH session.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            file_name = message.document.file_name
            file_path = f"{current_path.rstrip('/')}/{file_name}"
            # Sanitize file path
            file_path = file_path.replace('"', '').replace('`', '').replace('$', '')
            await message.answer(f"📤 Uploading {file_name} to {current_path}...")
            file = await bot.download_file_by_id(message.document.file_id)
            ssh = active_sessions[server_id]
            sftp = ssh.open_sftp()
            with sftp.file(file_path, 'wb') as remote_file:
                remote_file.write(file.read())
            sftp.close()
            # Refresh file list
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
            if 'mode' in user_state and user_state['mode'] == 'file_upload':
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
