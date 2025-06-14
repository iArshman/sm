import logging
import paramiko
import re
import os
from datetime import datetime
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from io import BytesIO

logger = logging.getLogger(__name__)

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
        path = re.sub(r'[;&|`\n\r]', '', path)
        path = os.path.normpath(path).replace('\\', '/')
        if path.startswith('/'):
            return path
        return '/' + path

    # --- HELPER: FORMAT FILE SIZE ---
    def format_size(size_bytes):
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 ** 2:
            return f"{size_bytes / 1024:.2f} KB"
        elif size_bytes < 1024 ** 3:
            return f"{size_bytes / (1024 ** 2):.2f} MB"
        else:
            return f"{size_bytes / (1024 ** 3):.2f} GB"

    # --- HELPER: PARSE LS OUTPUT ---
    def parse_ls_output(output):
        files = []
        for line in output.splitlines():
            match = re.match(r'^([drwx-]+)\s+\d+\s+(\S+)\s+(\S+)\s+(\d+)\s+(\w+\s+\d+\s+\d+:\d+|\w+\s+\d+\s+\d+)\s+(.+)$', line)
            if match:
                perms, owner, group, size, mtime, name = match.groups()
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
                    'perms': perms,
                    'owner': owner,
                    'group': group
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
    def build_file_keyboard(server_id, path, files, user_id):
        kb = InlineKeyboardMarkup(row_width=4)
        # Top action buttons
        kb.row(
            InlineKeyboardButton("⬅️ Server", callback_data=f"server_{server_id}"),
            InlineKeyboardButton("🔍 Search", callback_data=f"fm_search_{server_id}"),
            InlineKeyboardButton("📤 Upload", callback_data=f"fm_upload_{server_id}"),
            InlineKeyboardButton("📁 New Folder", callback_data=f"fm_new_folder_{server_id}")
        )
        kb.add(InlineKeyboardButton("☑️ Select Files", callback_data=f"fm_select_mode_{server_id}"))
        # File/directory buttons
        max_name_len = max((len(f['name']) for f in files), default=10)
        user_state = user_input.get(user_id, {})
        selected_files = user_state.get('selected_files', set())
        for f in sorted(files, key=lambda x: (not x['is_dir'], x['name'].lower())):
            icon = "📁" if f['is_dir'] else "📄"
            name = f['name'].ljust(max_name_len)
            size = format_size(f['size'])
            label = f"{icon} {name} | {size} | {f['mtime']}"
            if user_state.get('mode') == 'select_files':
                select_label = "✅" if f['name'] in selected_files else "☑️"
                select_cb = f"fm_toggle_select_{server_id}_{f['name']}"
                kb.row(
                    InlineKeyboardButton(select_label, callback_data=select_cb),
                    InlineKeyboardButton(label, callback_data=select_cb)
                )
            else:
                cb_data = f"fm_nav_{server_id}_{f['name']}" if f['is_dir'] else f"fm_file_{server_id}_{f['name']}"
                kb.add(InlineKeyboardButton(label, callback_data=cb_data))
        # Bottom navigation and selection action
        if path != '/':
            kb.add(InlineKeyboardButton("⬆️ Parent Directory", callback_data=f"fm_nav_{server_id}_.."))
        if selected_files and user_state.get('mode') == 'select_files':
            kb.add(InlineKeyboardButton(f"Selected: {len(selected_files)} Action", callback_data=f"fm_selection_actions_{server_id}"))
        return kb

    # --- HELPER: BUILD SELECTION ACTIONS KEYBOARD ---
    def build_selection_actions_keyboard(server_id, selected_files):
        kb = InlineKeyboardMarkup(row_width=3)
        kb.row(
            InlineKeyboardButton("📋 Copy", callback_data=f"fm_batch_copy_{server_id}"),
            InlineKeyboardButton("✂️ Move", callback_data=f"fm_batch_move_{server_id}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"fm_batch_delete_{server_id}")
        )
        if len(selected_files) > 1:
            kb.add(InlineKeyboardButton("🗜 Zip", callback_data=f"fm_zip_{server_id}"))
        if len(selected_files) == 1:
            kb.add(InlineKeyboardButton("✏️ Rename", callback_data=f"fm_rename_{server_id}_{list(selected_files)[0]}"))
        kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"fm_cancel_select_{server_id}"))
        return kb

    # --- HELPER: BUILD FILE ACTIONS KEYBOARD ---
    def build_file_actions_keyboard(server_id, file_name, is_zip=False):
        kb = InlineKeyboardMarkup(row_width=3)
        kb.row(
            InlineKeyboardButton("📥 Download", callback_data=f"fm_download_{server_id}_{file_name}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"fm_delete_{server_id}_{file_name}"),
            InlineKeyboardButton("✏️ Rename", callback_data=f"fm_rename_{server_id}_{file_name}")
        )
        kb.row(
            InlineKeyboardButton("👁️ View", callback_data=f"fm_view_{server_id}_{file_name}"),
            InlineKeyboardButton("📋 Copy", callback_data=f"fm_copy_{server_id}_{file_name}"),
            InlineKeyboardButton("✂️ Move", callback_data=f"fm_move_{server_id}_{file_name}")
        )
        kb.add(
            InlineKeyboardButton("ℹ️ Details", callback_data=f"fm_details_{server_id}_{file_name}"),
            InlineKeyboardButton("⬅️ Back", callback_data=f"fm_refresh_{server_id}")
        )
        if is_zip:
            kb.insert(InlineKeyboardButton("📂 Unzip", callback_data=f"fm_unzip_{server_id}_{file_name}"))
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
            logger.debug(f"Starting file manager for server_id: {server_id}, active_sessions: {list(active_sessions.keys())}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_input[callback.from_user.id] = {
                'server_id': server_id,
                'current_path': '/home/ubuntu',
                'mode': 'file_manager',
                'selected_files': set()
            }
            ssh = active_sessions[server_id]
            try:
                ssh.exec_command('whoami')  # Test SSH connection
            except Exception as e:
                logger.error(f"SSH connection test failed for server_id {server_id}: {e}")
                del active_sessions[server_id]
                await callback.message.edit_text("❌ SSH session expired. Please reconnect.", reply_markup=back_button(f"server_{server_id}"))
                return
            path = user_input[callback.from_user.id]['current_path']
            files, error = await get_file_list(server_id, path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, path, files, callback.from_user.id)
            text = f"🗂 File Manager: {path}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"File manager start error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error loading file manager.", reply_markup=back_button(f"server_{server_id}"))

    # --- NAVIGATE DIRECTORY ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_nav_"))
    async def navigate_directory(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', maxsplit=3)
            server_id = parts[2]
            dir_name = parts[3] if len(parts) > 3 else '..'
            logger.debug(f"Navigating directory for server_id: {server_id}, dir: {dir_name}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                logger.warning(f"Server ID mismatch: user_state server_id={user_state.get('server_id')}, callback server_id={server_id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            current_path = user_state['current_path']
            if dir_name == '..':
                new_path = '/'.join(current_path.rstrip('/').split('/')[:-1]) or '/'
            else:
                new_path = f"{current_path.rstrip('/')}/{dir_name}"
            new_path = sanitize_path(new_path)
            user_state['current_path'] = new_path
            if user_state.get('mode') == 'select_files':
                user_state['selected_files'] = set()
                user_state['mode'] = 'file_manager'
            ssh = active_sessions[server_id]
            try:
                ssh.exec_command('whoami')  # Test SSH connection
            except Exception as e:
                logger.error(f"SSH connection test failed for server_id {server_id}: {e}")
                del active_sessions[server_id]
                await callback.message.edit_text("❌ SSH session expired. Please reconnect.", reply_markup=back_button(f"server_{server_id}"))
                return
            files, error = await get_file_list(server_id, new_path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, new_path, files, callback.from_user.id)
            text = f"🗂 File Manager: {new_path}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Navigate directory error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error navigating directory.", reply_markup=back_button(f"server_{server_id}"))

    # --- TOGGLE FILE SELECTION ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_toggle_select_"))
    async def toggle_file_selection(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', maxsplit=3)
            server_id = parts[2]
            file_name = parts[3]
            logger.debug(f"Toggling selection for server_id: {server_id}, file: {file_name}, active_sessions: {list(active_sessions.keys())}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'select_files':
                logger.warning(f"Invalid state: server_id={user_state.get('server_id')}, mode={user_state.get('mode')}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            current_path = user_state['current_path']
            selected_files = user_state.get('selected_files', set())
            if file_name in selected_files:
                selected_files.remove(file_name)
            else:
                selected_files.add(file_name)
            user_state['selected_files'] = selected_files
            ssh = active_sessions[server_id]
            try:
                ssh.exec_command('whoami')  # Test SSH connection
            except Exception as e:
                logger.error(f"SSH connection test failed for server_id {server_id}: {e}")
                del active_sessions[server_id]
                await callback.message.edit_text("❌ SSH session expired. Please reconnect.", reply_markup=back_button(f"server_{server_id}"))
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                logger.error(f"Failed to list files: {error}")
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, current_path, files, callback.from_user.id)
            text = f"🗂 File Manager: {current_path}\n☑️ Selected: {len(selected_files)} item(s)"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Toggle selection error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error selecting file.", reply_markup=back_button(f"server_{server_id}"))

    # --- ENTER SELECTION MODE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_select_mode_"))
    async def enter_selection_mode(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[3]
            logger.debug(f"Entering selection mode for server_id: {server_id}, active_sessions: {list(active_sessions.keys())}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                logger.warning(f"Server ID mismatch: user_state server_id={user_state.get('server_id')}, callback server_id={server_id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state['mode'] = 'select_files'
            user_state['selected_files'] = set()
            current_path = user_state['current_path']
            ssh = active_sessions[server_id]
            try:
                ssh.exec_command('whoami')  # Test SSH connection
            except Exception as e:
                logger.error(f"SSH connection test failed for server_id {server_id}: {e}")
                del active_sessions[server_id]
                await callback.message.edit_text("❌ SSH session expired. Please reconnect.", reply_markup=back_button(f"server_{server_id}"))
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                logger.error(f"Failed to list files: {error}")
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, current_path, files, callback.from_user.id)
            text = f"🗂 File Manager: {current_path}\n☑️ Selection Mode: Click ☑️ to select files"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Enter selection mode error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error entering selection mode.", reply_markup=back_button(f"server_{server_id}"))

    # --- SHOW SELECTION ACTIONS ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_selection_actions_"))
    async def show_selection_actions(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[3]
            logger.debug(f"Showing selection actions for server_id: {server_id}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'select_files':
                logger.warning(f"Invalid state: server_id={user_state.get('server_id')}, mode={user_state.get('mode')}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            selected_files = user_state.get('selected_files', set())
            if not selected_files:
                await callback.message.edit_text("❌ No files selected.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            current_path = user_state['current_path']
            kb = build_selection_actions_keyboard(server_id, selected_files)
            text = f"🗂 File Manager: {current_path}\n☑️ Selected: {len(selected_files)} item(s)\nChoose an action:"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Show selection actions error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error showing actions.", reply_markup=back_button(f"server_{server_id}"))

    # --- CANCEL SELECTION MODE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_cancel_select_"))
    async def cancel_selection_mode(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[3]
            logger.debug(f"Canceling selection mode for server_id: {server_id}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                logger.warning(f"Server ID mismatch: user_state server_id={user_state.get('server_id')}, callback server_id={server_id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state['mode'] = 'file_manager'
            user_state['selected_files'] = set()
            current_path = user_state.get('current_path', '/home/ubuntu')
            ssh = active_sessions[server_id]
            try:
                ssh.exec_command('whoami')  # Test SSH connection
            except Exception as e:
                logger.error(f"SSH connection test failed for server_id {server_id}: {e}")
                del active_sessions[server_id]
                await callback.message.edit_text("❌ SSH session expired. Please reconnect.", reply_markup=back_button(f"server_{server_id}"))
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, current_path, files, callback.from_user.id)
            text = f"🗂 File Manager: {current_path}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Cancel selection mode error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error cancelling selection.", reply_markup=back_button(f"server_{server_id}"))

    # --- FILE ACTIONS ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_file_"))
    async def file_actions(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', maxsplit=3)
            server_id = parts[2]
            file_name = parts[3]
            logger.debug(f"Showing file actions for server_id: {server_id}, file: {file_name}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                logger.warning(f"Invalid state: server_id={user_state.get('server_id')}, mode={user_state.get('mode')}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            current_path = user_state['current_path']
            is_zip = file_name.lower().endswith('.zip')
            kb = build_file_actions_keyboard(server_id, file_name, is_zip)
            text = f"📄 File: {file_name}\nPath: {current_path}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"File actions error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error loading file actions.", reply_markup=back_button(f"server_{server_id}"))

    # --- DOWNLOAD FILE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_download_"))
    async def download_file(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', maxsplit=3)
            server_id = parts[2]
            file_name = parts[3]
            logger.debug(f"Downloading file for server_id: {server_id}, file: {file_name}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                logger.warning(f"Invalid state: server_id={user_state.get('server_id')}, mode={user_state.get('mode')}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            current_path = user_state['current_path']
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            ssh = active_sessions[server_id]
            sftp = ssh.open_sftp()
            try:
                await callback.message.edit_text(f"📥 Downloading {file_name}...")
                with sftp.file(file_path, 'rb') as remote_file:
                    file_data = remote_file.read()
                await bot.send_document(
                    callback.from_user.id,
                    document=types.InputFile(BytesIO(file_data), filename=file_name),
                    caption=f"File from {current_path}"
                )
            finally:
                sftp.close()
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, current_path, files, callback.from_user.id)
            text = f"🗂 File Manager: {current_path}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Download file error for server {server_id}: {e}")
            await callback.message.edit_text(f"❌ Error downloading file: {str(e)}", reply_markup=back_button(f"fm_refresh_{server_id}"))

    # --- DELETE FILE CONFIRM ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_delete_"))
    async def delete_file_confirm(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', maxsplit=3)
            server_id = parts[2]
            file_name = parts[3]
            logger.debug(f"Confirming delete for server_id: {server_id}, file: {file_name}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                logger.warning(f"Invalid state: server_id={user_state.get('server_id')}, mode={user_state.get('mode')}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
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
            parts = callback.data.split('_', maxsplit=4)
            server_id = parts[3]
            file_name = parts[4]
            logger.debug(f"Deleting file for server_id: {server_id}, file: {file_name}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                logger.warning(f"Invalid state: server_id={user_state.get('server_id')}, mode={user_state.get('mode')}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            current_path = user_state['current_path']
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            command = f'rm -rf "{file_path}"'
            ssh = active_sessions[server_id]
            try:
                ssh.exec_command('whoami')  # Test SSH connection
            except Exception as e:
                logger.error(f"SSH connection test failed for server_id {server_id}: {e}")
                del active_sessions[server_id]
                await callback.message.edit_text("❌ SSH session expired. Please reconnect.", reply_markup=back_button(f"server_{server_id}"))
                return
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await callback.message.edit_text(f"❌ Error deleting file: {stderr_data}", reply_markup=back_button(f"server_{server_id}"))
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, current_path, files, callback.from_user.id)
            text = f"🗂 File Manager: {current_path}\n✅ File '{file_name}' deleted."
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Delete file error for server {server_id}: {e}")
            await callback.message.edit_text(f"❌ Error deleting file: {str(e)}", reply_markup=back_button(f"server_{server_id}"))

    # --- BATCH DELETE SELECTED ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_delete_"))
    async def batch_delete_confirm(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[3]
            logger.debug(f"Confirming batch delete for server_id: {server_id}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'select_files':
                logger.warning(f"Invalid state: server_id={user_state.get('server_id')}, mode={user_state.get('mode')}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            selected_files = user_state.get('selected_files', set())
            if not selected_files:
                await callback.message.edit_text("❌ No files selected.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            current_path = user_state['current_path']
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("✅ Yes, delete", callback_data=f"fm_batch_delete_confirm_{server_id}"),
                InlineKeyboardButton("⬅️ Back", callback_data=f"fm_cancel_select_{server_id}")
            )
            text = f"⚠️ Are you sure you want to delete {len(selected_files)} selected file(s) from {current_path}?"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Batch delete confirm error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error initiating batch deletion.", reply_markup=back_button(f"server_{server_id}"))

    # --- BATCH DELETE CONFIRM ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_delete_confirm_"))
    async def batch_delete(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[4]
            logger.debug(f"Executing batch delete for server_id: {server_id}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'select_files':
                logger.warning(f"Invalid state: server_id={user_state.get('server_id')}, mode={user_state.get('mode')}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            current_path = user_state['current_path']
            selected_files = user_state.get('selected_files', set())
            ssh = active_sessions[server_id]
            try:
                ssh.exec_command('whoami')  # Test SSH connection
            except Exception as e:
                logger.error(f"SSH connection test failed for server_id {server_id}: {e}")
                del active_sessions[server_id]
                await callback.message.edit_text("❌ SSH session expired. Please reconnect.", reply_markup=back_button(f"server_{server_id}"))
                return
            errors = []
            for file_name in selected_files:
                file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
                command = f'rm -rf "{file_path}"'
                _, stderr_data = execute_ssh_command(ssh, command)
                if stderr_data:
                    errors.append(f"{file_name}: {stderr_data}")
            user_state['selected_files'] = set()
            user_state['mode'] = 'file_manager'
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, current_path, files, callback.from_user.id)
            text = f"🗂 File Manager: {current_path}\n✅ {len(selected_files) - len(errors)} file(s) deleted."
            if errors:
                text += f"\nErrors:\n" + "\n".join(errors)
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Batch delete error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error deleting files: {str(e)}", reply_markup=back_button(f"server_{server_id}"))

    # --- BATCH COPY SELECTED ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_copy_"))
    async def batch_copy_start(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[3]
            logger.debug(f"Starting batch copy for server_id: {server_id}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'select_files':
                logger.warning(f"Invalid state: server_id={user_state.get('server_id')}, mode={user_state.get('mode')}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            if not user_state.get('selected_files', set()):
                await callback.message.edit_text("❌ No files selected.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            user_state['mode'] = 'batch_copy'
            text = f"📋 Please send the destination path for copying {len(user_state['selected_files'])} selected file(s) (e.g., /home/ubuntu/destination)."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Batch copy start error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error initiating batch copy.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE BATCH COPY ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'batch_copy')
    async def handle_batch_copy(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            logger.debug(f"Handling batch copy for server_id: {server_id}, user: {uid}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await message.answer("❌ No active SSH session.")
                return
            dest_path = sanitize_path(message.text.strip())
            if not dest_path:
                await message.answer("❌ Invalid destination path.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            selected_files = user_state.get('selected_files', set())
            ssh = active_sessions[server_id]
            try:
                ssh.exec_command('whoami')  # Test SSH connection
            except Exception as e:
                logger.error(f"SSH connection test failed for server_id {server_id}: {e}")
                del active_sessions[server_id]
                await message.answer("❌ SSH session expired. Please reconnect.")
                return
            errors = []
            for file_name in selected_files:
                src_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
                command = f'cp -r "{src_path}" "{dest_path}/"'
                _, stderr_data = execute_ssh_command(ssh, command)
                if stderr_data:
                    errors.append(f"{file_name}: {stderr_data}")
            user_state['selected_files'] = set()
            user_state['mode'] = 'file_manager'
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {error}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uid)
            text = f"🗂 File Manager: {current_path}\n✅ {len(selected_files) - len(errors)} file(s) copied to '{dest_path}'."
            if errors:
                text += f"\nErrors:\n" + "\n".join(errors)
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Batch copy error for server {server_id}: {e}")
            await message.answer(f"❌ Error copying files: {str(e)}")
        finally:
            if user_state.get('mode') == 'batch_copy':
                user_state['mode'] = 'file_manager'

    # --- BATCH MOVE SELECTED ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_move_"))
    async def batch_move_start(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[3]
            logger.debug(f"Starting batch move for server_id: {server_id}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'select_files':
                logger.warning(f"Invalid state: server_id={user_state.get('server_id')}, mode={user_state.get('mode')}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            if not user_state.get('selected_files', set()):
                await callback.message.edit_text("❌ No files selected.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            user_state['mode'] = 'batch_move'
            text = f"✂️ Please send the destination path for moving {len(user_state['selected_files'])} selected file(s) (e.g., /home/ubuntu/destination)."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Batch move start error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error initiating batch move.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE BATCH MOVE ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'batch_move')
    async def handle_batch_move(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            logger.debug(f"Handling batch move for server_id: {server_id}, user: {uid}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await message.answer("❌ No active SSH session.")
                return
            dest_path = sanitize_path(message.text.strip())
            if not dest_path:
                await message.answer("❌ Invalid destination path.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            selected_files = user_state.get('selected_files', set())
            ssh = active_sessions[server_id]
            try:
                ssh.exec_command('whoami')  # Test SSH connection
            except Exception as e:
                logger.error(f"SSH connection test failed for server_id {server_id}: {e}")
                del active_sessions[server_id]
                await message.answer("❌ SSH session expired. Please reconnect.")
                return
            errors = []
            for file_name in selected_files:
                src_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
                command = f'mv "{src_path}" "{dest_path}/"'
                _, stderr_data = execute_ssh_command(ssh, command)
                if stderr_data:
                    errors.append(f"{file_name}: {stderr_data}")
            user_state['selected_files'] = set()
            user_state['mode'] = 'file_manager'
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {error}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uid)
            text = f"🗂 File Manager: {current_path}\n✅ {len(selected_files) - len(errors)} file(s) moved to '{dest_path}'."
            if errors:
                text += f"\nErrors:\n" + "\n".join(errors)
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Batch move error for server {server_id}: {e}")
            await message.answer(f"❌ Error moving files: {str(e)}")
        finally:
            if user_state.get('mode') == 'batch_move':
                user_state['mode'] = 'file_manager'

    # --- ZIP ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_zip_"))
    async def zip_files_start(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            logger.debug(f"Starting zip for server_id: {server_id}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'select_files':
                logger.warning(f"Invalid state: server_id={user_state.get('server_id')}, mode={user_state.get('mode')}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            if not user_state.get('selected_files', set()):
                await callback.message.edit_text("❌ No files selected.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            user_state['mode'] = 'zip_mode'
            text = f"🗜 Creating zip file in {user_state['current_path']}. Please enter a name for the zip file (e.g., archive.zip)."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Zip start error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error initiating zip operation.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE ZIP ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'zip_mode')
    async def handle_zip(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            logger.debug(f"Handling zip for server_id: {server_id}, user: {uid}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await message.answer("❌ No active SSH session.")
                return
            zip_name = re.sub(r'[;&|`\n\r]', '', message.text.strip())
            if not zip_name.endswith('.zip'):
                zip_name += '.zip'
            if not zip_name:
                await message.answer("❌ Invalid zip file name.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            zip_path = sanitize_path(f"{current_path.rstrip('/')}/{zip_name}")
            selected_files = user_state.get('selected_files', set())
            ssh = active_sessions[server_id]
            try:
                ssh.exec_command('whoami')  # Test SSH connection
            except Exception as e:
                logger.error(f"SSH connection test failed for server_id {server_id}: {e}")
                del active_sessions[server_id]
                await message.answer("❌ SSH session expired. Please reconnect.")
                return
            file_paths = [sanitize_path(f"{current_path.rstrip('/')}/{f}") for f in selected_files]
            quoted_files = ' '.join(f'"{p}"' for p in file_paths)
            command = f'zip -r "{zip_path}" {quoted_files}'
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await message.answer(f"❌ Error creating zip file: {stderr_data}")
                return
            user_state['selected_files'] = set()
            user_state['mode'] = 'file_manager'
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {error}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uid)
            text = f"🗂 File Manager: {current_path}\n✅ Zip file '{zip_name}' created."
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Zip error for server {server_id}: {e}")
            await message.answer(f"❌ Error creating zip: {str(e)}")
        finally:
            if user_state.get('mode') == 'zip_mode':
                user_state['mode'] = 'file_manager'

    # --- UNZIP ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_unzip_"))
    async def unzip_file(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', maxsplit=3)
            server_id = parts[2]
            file_name = parts[3]
            logger.debug(f"Unzipping file for server_id: {server_id}, file: {file_name}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                logger.warning(f"Invalid state: server_id={user_state.get('server_id')}, mode={user_state.get('mode')}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            current_path = user_state['current_path']
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            command = f'unzip -o "{file_path}" -d "{current_path}"'
            ssh = active_sessions[server_id]
            try:
                ssh.exec_command('whoami')  # Test SSH connection
            except Exception as e:
                logger.error(f"SSH connection test failed for server_id {server_id}: {e}")
                del active_sessions[server_id]
                await callback.message.edit_text("❌ SSH session expired. Please reconnect.", reply_markup=back_button(f"server_{server_id}"))
                return
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await callback.message.edit_text(f"❌ Error unzipping file: {stderr_data}", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, current_path, files, callback.from_user.id)
            text = f"🗂 File Manager: {current_path}\n✅ File '{file_name}' unzipped."
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Unzip error for server {server_id}: {e}")
            await callback.message.edit_text(f"❌ Error unzipping file: {str(e)}", reply_markup=back_button(f"fm_refresh_{server_id}"))

    # --- UPLOAD FILE START ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_upload_"))
    async def upload_file_start(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            logger.debug(f"Starting upload for server_id: {server_id}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                logger.warning(f"Server ID mismatch: user_state server_id={user_state.get('server_id')}, callback server_id={server_id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state['mode'] = 'upload_file'
            text = f"📤 Uploading to {user_state['current_path']}. Please send a file."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Upload start error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error initiating file upload.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE FILE UPLOAD ---
    @dp.message_handler(content_types=types.ContentType.DOCUMENT)
    async def handle_file_upload(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            if user_state.get('mode') != 'upload_file':
                await message.answer("❌ Not in upload mode.")
                return
            server_id = user_state.get('server_id')
            logger.debug(f"Handling upload for server_id: {server_id}, user: {uid}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await message.answer("❌ No active SSH session.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            file_name = message.document.file_name
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            await message.answer(f"📤 Uploading {file_name} to {current_path}...")
            file_data = BytesIO()
            await message.document.download(destination=file_data)
            ssh = active_sessions[server_id]
            try:
                ssh.exec_command('whoami')  # Test SSH connection
            except Exception as e:
                logger.error(f"SSH connection test failed for server_id {server_id}: {e}")
                del active_sessions[server_id]
                await message.answer("❌ SSH session expired. Please reconnect.")
                return
            sftp = ssh.open_sftp()
            try:
                with sftp.file(file_path, 'wb') as remote_file:
                    remote_file.write(file_data.getvalue())
            finally:
                sftp.close()
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {error}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uid)
            text = f"🗂 File Manager: {current_path}\n✅ File '{file_name}' uploaded."
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            user_state['mode'] = 'file_manager'
        except Exception as e:
            logger.error(f"Upload error for server {server_id}: {e}")
            await message.answer(f"❌ Error uploading file: {str(e)}")
        finally:
            if user_state.get('mode') == 'upload_file':
                user_state['mode'] = 'file_manager'

    # --- CANCEL UPLOAD ---
    @dp.callback_query_handler(lambda c: c.data == "fm_cancel")
    async def cancel_upload(callback: types.CallbackQuery):
        try:
            user_state = user_input.get(callback.from_user.id, {})
            server_id = user_state.get('server_id')
            logger.debug(f"Canceling upload for server_id: {server_id}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state['mode'] = 'file_manager'
            user_state['selected_files'] = set()
            current_path = user_state.get('current_path', '/home/ubuntu')
            ssh = active_sessions[server_id]
            try:
                ssh.exec_command('whoami')  # Test SSH connection
            except Exception as e:
                logger.error(f"SSH connection test failed for server_id {server_id}: {e}")
                del active_sessions[server_id]
                await callback.message.edit_text("❌ SSH session expired. Please reconnect.", reply_markup=back_button(f"server_{server_id}"))
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, current_path, files, callback.from_user.id)
            text = f"🗂 File Manager: {current_path}\n✅ Operation cancelled."
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Cancel upload error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error cancelling upload.", reply_markup=back_button(f"server_{server_id}"))

    # --- NEW FOLDER START ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_new_folder_"))
    async def new_folder_start(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[3]
            logger.debug(f"Starting new folder for server_id: {server_id}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                logger.warning(f"Server ID mismatch: user_state server_id={user_state.get('server_id')}, callback server_id={server_id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state['mode'] = 'new_folder'
            text = f"📁 Creating new folder in {user_state['current_path']}. Please send a folder name."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"New folder start error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error initiating new folder.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE NEW FOLDER ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'new_folder')
    async def handle_new_folder(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            logger.debug(f"Handling new folder for server_id: {server_id}, user: {uid}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await message.answer("❌ No active SSH session.")
                return
            folder_name = re.sub(r'[;&|`\n\r]', '', message.text.strip())
            if not folder_name:
                await message.answer("❌ Invalid folder name.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            folder_path = sanitize_path(f"{current_path.rstrip('/')}/{folder_name}")
            command = f'mkdir "{folder_path}"'
            ssh = active_sessions[server_id]
            try:
                ssh.exec_command('whoami')  # Test SSH connection
            except Exception as e:
                logger.error(f"SSH connection test failed for server_id {server_id}: {e}")
                del active_sessions[server_id]
                await message.answer("❌ SSH session expired. Please reconnect.")
                return
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await message.answer(f"❌ Error creating folder: {stderr_data}")
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {error}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uid)
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
            parts = callback.data.split('_', maxsplit=3)
            server_id = parts[2]
            file_name = parts[3]
            logger.debug(f"Starting rename for server_id: {server_id}, file: {file_name}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') not in ['file_manager', 'select_files']:
                logger.warning(f"Invalid state: server_id={user_state.get('server_id')}, mode={user_state.get('mode')}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state['mode'] = 'rename_file'
            user_state['old_name'] = file_name
            text = f"✏️ Renaming '{file_name}' in {user_state['current_path']}. Please send a new name."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Rename start error for server {server_id}: {e}")
            await callback.message.edit_text("❌ Error initiating rename.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE RENAME FILE ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'rename_file')
    async def handle_rename(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            logger.debug(f"Handling rename for server_id: {server_id}, user: {uid}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await message.answer("❌ No active SSH session.")
                return
            old_name = user_state.get('old_name')
            new_name = re.sub(r'[;&|`\n\r]', '', message.text.strip())
            if not new_name:
                await message.answer("❌ Invalid file name.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            old_path = sanitize_path(f"{current_path.rstrip('/')}/{old_name}")
            new_path = sanitize_path(f"{current_path.rstrip('/')}/{new_name}")
            command = f'mv "{old_path}" "{new_path}"'
            ssh = active_sessions[server_id]
            try:
                ssh.exec_command('whoami')  # Test SSH connection
            except Exception as e:
                logger.error(f"SSH connection test failed for server_id {server_id}: {e}")
                del active_sessions[server_id]
                await message.answer("❌ SSH session expired. Please reconnect.")
                return
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await message.answer(f"❌ Error renaming file: {stderr_data}")
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {error}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uid)
            text = f"🗂 File Manager: {current_path}\n✅ File '{old_name}' renamed to '{new_name}'."
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            user_state['mode'] = 'file_manager'
        except Exception as e:
            logger.error(f"Rename error for server {server_id}: {e}")
            await message.answer(f"❌ Error renaming file: {str(e)}")
        finally:
            if user_state.get('mode') == 'rename_file':
                user_state['mode'] = 'file_manager'

    # --- VIEW FILE CONTENT ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_view_"))
    async def view_file(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', maxsplit=3)
            server_id = parts[2]
            file_name = parts[3]
            logger.debug(f"Viewing file for server_id: {server_id}, file: {file_name}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                logger.warning(f"Invalid state: server_id={user_state.get('server_id')}, mode={user_state.get('mode')}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            current_path = user_state['current_path']
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            ssh = active_sessions[server_id]
            sftp = ssh.open_sftp()
            try:
                command = f'file "{file_path}"'
                stdout_data, stderr_data = execute_ssh_command(ssh, command)
                if stderr_data or 'text' not in stdout_data.lower():
                    await callback.message.edit_text("❌ Only text files can be viewed.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                    return
                with sftp.file(file_path, 'r') as remote_file:
                    content = remote_file.read(4000).decode('utf-8', errors='replace')
                    # Escape HTML characters
                    content = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    if len(content.encode('utf-8')) >= 4000:
                        content = content[:3900] + "... (truncated)"
                text = f"📄 File: {file_name}\nPath: {current_path}\n\n<pre>{content}</pre>"
                await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_button(f"fm_refresh_{server_id}"))
            finally:
                sftp.close()
        except Exception as e:
            logger.error(f"View file error for server {server_id}: {e}")
            await callback.message.edit_text(f"❌ Error viewing file: {str(e)}", reply_markup=back_button(f"fm_refresh_{server_id}"))

    # --- FILE DETAILS ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_details_"))
    async def file_details(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', maxsplit=3)
            server_id = parts[2]
            file_name = parts[3]
            logger.debug(f"Fetching details for server_id: {server_id}, file: {file_name}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                logger.warning(f"Invalid state: server_id={user_state.get('server_id')}, mode={user_state.get('mode')}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            current_path = user_state['current_path']
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            ssh = active_sessions[server_id]
            try:
                ssh.exec_command('whoami')  # Test SSH connection
            except Exception as e:
                logger.error(f"SSH connection test failed for server_id {server_id}: {e}")
                del active_sessions[server_id]
                await callback.message.edit_text("❌ SSH session expired. Please reconnect.", reply_markup=back_button(f"server_{server_id}"))
                return
            command = f'ls -l "{file_path}"'
            stdout_data, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await callback.message.edit_text(f"❌ Error: {stderr_data}", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            file_info = parse_ls_output(stdout_data)[0]
            text = f"📄 File Details: {file_name}\n"
            text += f"Path: {current_path}\n"
            text += f"Size: {format_size(file_info['size'])}\n"
            text += f"Modified: {file_info['mtime']}\n"
            text += f"Permissions: {file_info['perms']}\n"
            text += f"Owner: {file_info['owner']}\n"
            text += f"Group: {file_info['group']}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_button(f"fm_refresh_{server_id}"))
        except Exception as e:
            logger.error(f"File details error for server {server_id}: {e}")
            await callback.message.edit_text(f"❌ Error fetching file details: {str(e)}", reply_markup=back_button(f"fm_refresh_{server_id}"))

    # --- COPY FILE START ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_copy_"))
    async def copy_file_start(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', maxsplit=3)
            server_id = parts[2]
            file_name = parts[3]
            logger.debug(f"Starting copy for server_id: {server_id}, file: {file_name}")
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server_id: {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                logger.warning(f"Invalid state: server_id={user_state.get('server_id')}, mode={user_state.get('mode')}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state['mode'] = 'copy_file'
            user_state['file_name'] = file_name
            text = f"📋 Copying '{file_name}' from {user_state['current_path']}. Please send a destination path (e.g., /home/ubuntu/destination)."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Copy start error for server {server_id}: {e}")
