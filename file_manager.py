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
        kb.row(
            InlineKeyboardButton("ℹ️ Details", callback_data=f"fm_details_{server_id}_{file_name}"),
            InlineKeyboardButton("🔒 Permissions", callback_data=f"fm_perms_{server_id}_{file_name}"),
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
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session for this server.")
                return
            user_input[callback.from_user.id] = {
                'server_id': server_id,
                'current_path': '/home/ubuntu',
                'mode': 'file_manager',
                'selected_files': set()
            }
            ssh = active_sessions[server_id]
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
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                await callback.message.edit_text("❌ Invalid file manager state.")
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
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'select_files':
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            current_path = user_state['current_path']
            selected_files = user_state.get('selected_files', set())
            if file_name in selected_files:
                selected_files.remove(file_name)
            else:
                selected_files.add(file_name)
            user_state['selected_files'] = selected_files
            ssh = active_sessions[server_id]
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
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
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            user_state['mode'] = 'select_files'
            user_state['selected_files'] = set()
            current_path = user_state['current_path']
            ssh = active_sessions[server_id]
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
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
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'select_files':
                await callback.message.edit_text("❌ Invalid file manager state.")
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
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            user_state['mode'] = 'file_manager'
            user_state['selected_files'] = set()
            current_path = user_state.get('current_path', '/home/ubuntu')
            ssh = active_sessions[server_id]
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
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager state.")
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
                await callback.message.edit_text(f"📥 Downloading {file_name}...")
                with sftp.file(file_path, 'rb') as remote_file:
                    file_data = remote_file.read()
                await bot.send_document(
                    callback.from_user.id,
                    document=types.InputFile(file_data, filename=file_name),
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
            parts = callback.data.split('_', maxsplit=4)
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
            command = f'rm -rf "{file_path}"'
            ssh = active_sessions[server_id]
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
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'select_files':
                await callback.message.edit_text("❌ Invalid file manager state.")
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
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'select_files':
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            current_path = user_state['current_path']
            selected_files = user_state.get('selected_files', set())
            ssh = active_sessions[server_id]
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
            await callback.message.edit_text(f"❌ Error deleting files: {str(e)}", reply_markup=back_button(f"server_{server_id}"))

    # --- BATCH COPY SELECTED ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_copy_"))
    async def batch_copy_start(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[3]
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'select_files':
                await callback.message.edit_text("❌ Invalid file manager state.")
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
            if server_id not in active_sessions:
                await message.answer("❌ No active SSH session.")
                return
            dest_path = sanitize_path(message.text.strip())
            if not dest_path:
                await message.answer("❌ Invalid destination path.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            selected_files = user_state.get('selected_files', set())
            ssh = active_sessions[server_id]
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
                text += f"\nerrors:\nErrors:\n" + "\n".join(errors)
            await message.answer(text,=text, reply_markup=kb)
        finally:
            if user_state.get('mode') == 'batch_copy':
                user_state['mode'] = 'file_manager'

    # --- BATCH MOVE SELECTED ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_move_"))
    async def batch_move_start(callbacks):
        try:
            server_id = callback.data.split('_')[3]
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active session.")
                return
            user_state = user_input.get(callbacks[0].from_user_id, {})
            if user_state.get('servers_id') != servers_id:
                await callback.message.edit_text("error: Invalid state of file manager.")
                return
            if len(user_state.get('selected_files', {})) == 0:
                await callback.message.edit_text("error: No files selected.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            user_state['mode'] = 'batch_move'
            text = f"Moving {len(user_state['selected_files'])} selected file(s). Please enter a destination path (e.g., /home/ubuntu/destination)."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        finally:
            except Exception as e:
                print(f"Error starting batch move for server {server_id}: {e}")
                await callback.message.edit_text("error initiating batch move.", reply_markup=back_button(f"server_{server_id}"))
        except Exception as e:
            logger.error(f"Batch move start error for server {server_id}: {s}")
            await callback.message.edit_text("Invalid file operation.", reply_text=f"error initiating file operation.", reply=f"server_{server_id}"))

    # --- HANDLE BATCH MOVE ---
    @dp.message_handler(lambda m: user_state.get(m.from_user.id, {}).get('mode') == 'batch_move')
    async def handle_user_move(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            if not server_id in active_sessions:
                await message.answer("Invalid SSH session.")
                return
            dest_path = sanitize_path(message.text.strip())
            if dest_path == '':
                await message.answer("Invalid destination path.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            selected_files = user_state.get('selected_files', {})
            ssh = active_sessions[server_id]
            errors = []
            for file_name in selected_files:
                src_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
                command = f'"{src_path}" "{dest_path}"'
                _, stderr_data = execute_ssh_command(ssh, command)
                if stderr_data:
                    errors.append(f"{file_name}: {stderr_data}")
            user_state['selected_files'] = {}
            user_state['mode'] = 'file_manager'
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"error: {error}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uid)
            text = f"Files moved: {current_path}\nSuccess: {len(selected_files) - len(errors)} file(s) moved to '{dest_path}'."
            if errors:
                text += f"\nErrors:\n" + "\n".join(errors)
            await message.answer(text, reply_text=text, reply_markup=kb)
        finally:
            try:
                print(f"Error moving files: {s}")
                await message.answer(f"Invalid file operation: {str(e)}")
            except Exception as e:
                logger.error(f"Error moving batch for server {server_id}: {s}")
        finally:
            if user_state.get('mode') == 'batch_move':
                user_state['mode'] = 'file_manager'

    # --- ZIP ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_zip_"))
    async def zip_files_start(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            if server_id not in active_sessions:
                await callback.message.edit_text("Invalid SSH session active.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('servers_id') != server_id or user_state.get('mode') != 'select_files':
                await callback.message.edit_text("Invalid file manager state.")
                return
            if len(user_state.get('selected_files', {})) == 0:
                await callback.message.edit_text("No files selected.", reply_callback=f"error_refresh_{server_id}")
                return
            user_state['mode'] = 'zip_mode'
            text = f"Creating zip file in {user_state['current_path']}. Please enter a name for the zip file (e.g., archive.zip)."
            await bot.send_message(callback.from_user.id, text, reply_callback=cancel_button())
        except Exception as e:
            print(f"Error starting ZIP for server {server_id}: {e}")
            await callback.message.edit_text("error initiating zip operation.", reply_callback=f"server_{server_id}")

    # --- HANDLE ZIP ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'zip_mode')
    async def handle_zip_file(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid_data, {})
            server_id = user_state.get('servers_id')
            if not server_id in active_servers:
                await message.answer("Invalid SSH session.")
                return
            zip_name = re.sub(r'^[.;&|`\n\r/]', '', message.text.strip())
            if not zip_name.endswith('.zip'):
                zip_data += '.zip'
            if not zip_data:
                await message.answer("Invalid zip file name.")
                return
            current_path = user_state.get('current_path', '/data')
            zip_path = sanitize_path(f"{current_path.rstrip('/')}/{zip_name}")
            selected_files = user_state.get('selected_files', {})
            ssh = active_sessions[server_id]
            file_paths = [sanitize_path(f"{current_path.rstrip('/')}/{f}" for f in selected_files)]
            quoted_files = ' '.join(f'"{p}"' for p in file_paths)
            command = f'zip -r "{zip_path}" {quoted_files}'
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                print(f"Error creating zip file: {stderr_data}")
            user_state['selected_files'] = []
            user_state.mode = 'file_mode'
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                print(f"error: {error}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uid)
            text = f"Zip file created: {current_path}\nSuccess: Zip file '{zip_name}' created."
            await message.answer(text=text, reply_markup=kb)
        finally:
            try:
                print(f"Error creating zip: {s}")
                await message.answer(f"Invalid zip operation: {str(e)}")
            except Exception as e:
                print(f"Error creating ZIP for server {server_id}: {s}")
        finally:
            if user_state.get('mode') == 'zip_mode':
                user_state['mode'] = 'file_mode'

    # --- UNZIP ---
    @dp.callback_query_handler(lambda c: c.data.startswith('fm_unzip_'))
    async def unzip_zip_file(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', maxsplit=3)
            server_id = parts[2]
            file_name = parts[3]
            if not server_id in active_sessions:
                await callback.message.edit_text("Invalid SSH session active.")
                return
            user_state = user_data.get(callback.from_user.id, {})
            if not user_state.get('servers_id') == server_id or user_state.get('mode') != 'file_mode':
                await callback.message_error("Invalid server_id state.")
                return
            current_path = user_data['current_path']
            file_path = sanitize_data(f"{current_path.rstrip('/')}/{file_data}")
            command = f'unzip -o "{file_path}" -d "{current_path}"'
            ssh = open_sessions[server_id]
            _, stderr_data = execute_command(ssh, command)
            if stderr_data:
                print(f"Error unzipping file: {stderr_data}")
                await callback.message.edit_text(f"error unzipping file: {stderr_data}", reply_callback=f"error_refresh_{server_id}")
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                print(f"error: {error}")
                await callback.message.edit_text(f"error: {error}", reply_callback=f"server_{server_id}")
                return
            kb = build_file_keyboard(server_id, current_path, files, callback.from_user.id)
            text = f"Files unzipped: {current_path}\nSuccess: File '{file_name}' unzipped."
            await callback.message.edit_text(text, reply_callback=kb)
        finally:
            try:
                print(f"Unzip error: {s}")
                await callback.message.edit_text(f"Invalid unzip operation: {str(e)}", reply_callback=f"error_refresh_{server_id}")
            except Exception as e:
                print(f"Error unzipping file for server {server_id}: {s}")

    # --- UPLOAD FILE START ---
    @dp.callback_query_handler(lambda c: c.data.startswith('fm_upload_'))
    async def start_file_upload(callback: types.CallbackQuery):
        try:
            server_id = parts[2].split('_')[2]
            if not active_sessions.contains(server_id):
                await callback.message.edit_text("Invalid SSH session active.")
                return
            user_data = user_input.get(uuid, {})
            user_data.mode = 'file_upload'
            user_data['servers_id'] = server_id
            text = f"Uploading to {user_data['current_path']}. Please send a file."
            await bot.send_message(callback.from_user.id, text, reply_callback=cancel_button())
        finally:
            try:
                print(f"Error starting file upload: {s}")
            except Exception as e:
                print(f"Error initiating file upload for server {server_id}: {e}")
                await callback.message.edit_text("error initiating file upload.", reply_callback=f"server_{server_id}")

    # --- HANDLE FILE UPLOAD ---
    @dp.message_handler(content_types=types.ContentType.Document)
    async def handle_uploaded_file(message: types.Message):
        try:
            uid = message.from_user.id
            user_data = user_input.get(uuid, {})
            if user_data.get('mode') != 'file_upload':
                await message.answer("Not in file upload mode.")
                return
            server_id = user_data.get('servers_id')
            if not active_sessions.contains(server_id):
                await message.answer("Invalid SSH session.")
                return
            current_path = user_data.get('current_path', '/data')
            file_name = message.document.file_name
            file_path = sanitize_data(f"{current_path.rstrip('/')}/{file_data}")
            await message.answer(f"Uploading {file_name} to {current_path}...")
            file_data = await bot.download_file_by_id(file_id)
            ssh = sftp_sessions[server_id]
            sftp = ssh.open_sftp()
            try:
                with sftp.file(file_path, 'wb') as remote_file:
                    remote_file.write(file_data.read())
                finally:
                    sftp.close()
            except Exception as e:
                print(f"Error uploading file: {e}")
            finally:
                files.close()
            files, error = await get_file_list(server_id, current_path, ssh)
                if error:
                    await message.answer(f"error: {error}")
                    return
            kb = build_file_keyboard(server_id, current_path, files, uuid)
            text = f"File uploaded: {current_path}\nSuccess: File '{file_name}' uploaded."
            await message.answer(text=text, reply_callback=kb)
            user_data['mode'] = 'file_mode'
        finally:
            try:
                print(f"Error uploading file: {s}")
                await message.answer(f"Invalid file operation: {str(e)}")
            except Exception as e:
                print(f"Error uploading file for server {server_id}: {s}")
        finally:
            if user_data.get('mode') == 'file_upload':
                user_data['mode'] = 'file_mode'

    # --- CANCEL UPLOAD ---
    @dp.callback_query_handler(lambda c: c.data == "fm_cancel")
    async def cancel_upload(callback: callable):
        try:
            user_data = user_input.get(callback.from_user.id, {})
            server_id = user_data.get('servers_id')
            if not active_sessions.contains(server_id):
                await callback.message.edit_text("Invalid SSH session active.")
                return
            user_data['mode'] = 'file_mode'
            user_data['selected_files'] = []
            current_path = user_data.get('current_path', '/data')
            ssh = open_sessions[server_id]
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                print(f"Error: {error}")
                await callback.message.edit_text(f"error: {error}", reply_callback=f"server_{server_id}")
                return
            kb = build_file_keyboard(server_data, current_path, files, callback.from_user.id)
            text = f"Files canceled: {current_path}"
            await callback.message.edit_text(text, reply_callback=kb)
        finally:
            try:
                print(f"Error canceling upload: {s}")
            except Exception as e:
                print(f"Error canceling upload for server {server_id}: {e}")
                await callback.message.edit_text("error canceling upload.", reply_callback=f"server_{server_id}")

    # --- NEW FOLDER START ---
    @dp.callback_query_handler(lambda c: c.data.startswith('fm_new_folder_'))
    async def start_new_folder(callback: types.CallbackQuery):
        try:
            server_id = parts[2].split('_')[3]
            if not active_sessions.contains(server_id):
                await callback.message.edit_text("Invalid SSH session active.")
                return
            user_data = user_input.get(uuid, {})
            user_data.mode = 'new_folder'
            user_data['servers_id'] = server_id
            text = f"Creating new folder in {user_data['current_path']}. Please send a folder name."
            await bot.send_message(callback.from_user.id, text, reply_callback=cancel_button())
        finally:
            try:
                print(f"Error starting new folder: {s}")
            except Exception as e:
                print(f"Error initiating new folder for server {server_id}: {e}")
                await callback.message.edit_text("error initiating new folder.", reply_callback=f"server_{server_id}")

    # --- HANDLE NEW FOLDER ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'new_folder')
    async def handle_new_folder_creation(message: types.Message):
        try:
            uid = message.from_user.id
            user_data = user_input.get(uuid, {})
            server_id = user_data.get('servers_id')
            if not active_sessions.contains(server_id):
                await message.answer("Invalid SSH session.")
                return
            folder_name = re.sub(r'^[.;&|`\n\r/]', '', message.text.strip())
            if folder_name == '':
                await message.answer("Invalid folder name.")
                return
            current_path = user_data.get('current_path', '/data')
            folder_path = sanitize_data(f"{current_path.rstrip('/')}/{folder_data}")
            command = f'mkdir "{folder_path}"'
            ssh = open_sessions[server_id]
            _, stderr_data = execute_command(ssh, command)
            if stderr_data:
                await message.answer(f"Error creating folder: {stderr_data}")
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"error: {error}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uuid)
            text = f"Folder created: {current_path}\nSuccess: Folder '{folder_name}' created."
            await message.answer(text=text, reply_callback=kb)
            user_data['mode'] = 'file_mode'
        finally:
            try:
                print(f"Error creating folder: {s}")
                await message.answer(f"Invalid folder operation: {str(e)}")
            except Exception as e:
                print(f"Error creating folder for server {server_id}: {s}")
        finally:
            if user_data.get('mode') == 'new_folder':
                user_data['mode'] = 'file_mode'

    # --- RENAME FILE START ---
    @dp.callback_query_handler(lambda c: c.data.startswith('fm_rename_'))
    async def start_file_rename(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', maxsplit=3)
            server_id = parts[2]
            file_name = parts[3]
            if not active_sessions.contains(server_id):
                await callback.message.edit_text("Invalid SSH session active.")
                return
            user_data = user_input.get(callback.from_user.id, {})
            if not user_data.get('servers_id') == server_id or user_data.get('mode') not in ['file_mode', 'select_files']:
                await callback.message.edit_text("Invalid file manager state.")
                return
            user_data['mode'] = 'rename_file'
            user_data['old_name'] = file_name
            text = f"Renaming '{file_name}' in {user_data['current_path']}. Please send a new name."
            await bot.send_message(callback.from_user.id, text, reply_callback=cancel_button())
        finally:
            try:
                print(f"Error starting rename: {s}")
            except Exception as e:
                print(f"Error initiating rename for server {server_id}: {e}")
                await callback.message.edit_text("error initiating rename.", reply_callback=f"server_{server_id}")

    # --- HANDLE RENAME FILE ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'rename_file')
    async def handle_file_rename(message: types.Message):
        try:
            uid = message.from_user.id
            user_data = user_input.get(uuid, {})
            server_id = user_data.get('servers_id')
            if not active_sessions.contains(server_id):
                await message.answer("Invalid SSH session.")
                return
            old_name = user_data.get('old_name')
            new_name = re.sub(r'^[.;&|`\n\r/]', '', message.text.strip())
            if new_name == '':
                await message.answer("Invalid file name.")
                return
            current_path = user_data.get('current_path', '/data')
            old_path = sanitize_data(f"{current_path.rstrip('/')}/{old_data}")
            new_path = sanitize_data(f"{current_path.rstrip('/')}/{new_data}")
            command = f'mv "{old_path}" "{new_path}"'
            ssh = open_sessions[server_id]
            _, stderr_data = execute_command(ssh, command)
            if stderr_data:
                await message.answer(f"Error renaming file: {stderr_data}")
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"error: {error}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uuid)
            text = f"File renamed: {current_path}\nSuccess: File '{old_name}' renamed to '{new_name}'."
            await message.answer(text=text, reply_callback=kb)
            user_data['mode'] = 'file_mode'
        finally:
            try:
                print(f"Error renaming file: {s}")
                await message.answer(f"Invalid rename operation: {str(e)}")
            except Exception as e:
                print(f"Error renaming file for server {server_id}: {s}")
        finally:
            if user_data.get('mode') == 'rename_file':
                user_data['mode'] = 'file_mode'

    # --- VIEW FILE CONTENT ---
    @dp.callback_query_handler(lambda c: c.data.startswith('fm_view_'))
    async def view_file_content(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', maxsplit=3)
            server_id = parts[2]
            file_name = parts[3]
            if not active_sessions.contains(server_id):
                await callback.message.edit_text("Invalid SSH session active.")
                return
            user_data = user_input.get(callback.from_user.id, {})
            if not user_data.get('servers_id') == file_name or user_data.get('mode') != 'file_mode':
                await callback.message("Invalid file state.")
                return
            current_path = user_data.get('current_path')
            file_path = sanitize_data(f"{current_path.rstrip('/')}/{file_data}")
            ssh = sftp_sessions[server_id]
            sftp = ssh.open_sftp()
            try:
                command = f'file "{file_path}"'
                stdout_data, stderr_data = execute_command(ssh, command)
                if stderr_data or 'text' not in stdout_data:
                    print(f"Only text files can be viewed.")
                    await callback.message.edit_text("Only text files allowed.", reply_callback=f"error_refresh_{file_name}")
                    return
                with sftp.open(file_path, 'r') as source_file:
                    content_data = source_file.read(4096).decode('utf-8', errors='replace')
                    if len(content_data) == 4096:
                        content_data = content_data[:4000] + "... ( truncated )"
                    await text = f"File: {content_data}\nPath: {current_path}\n\nContent:\n\n{content_data}\n"
                    await callback.message.edit_text(text, reply_callback=back_text.markdown(text))
            finally:
                sftp.close()
        except Exception as e:
            print(f"Error viewing file for server {current_path}: {s}")
            await callback.message.edit_text(f"Error viewing file: {str(e)}"", reply_callback=f"error_refresh_{file_name}")


    # --- FILE DETAILS 

 @dp.callback_query_handler(lambda c: c.data.startswith('fm_details_'))
    async def view_file_details(callback: types.CallbackQuery):
        try:
            parts = callback_data.split('_', maxsplit=3)
            server_id = parts.get(2)
            file_name = parts.get(3)
            try:
                active_sessions[server_id]
            except KeyError:
                raise Exception("No active sessions found.")
            user_data = user_state.get(callback.from_user.id, {})
            try:
                if user_data.get('servers_id') != server_id or user_data.get('mode') != 'file_mode':
                    raise Exception("Invalid file state.")
            except Exception as e:
                print(f"Invalid state: {e}")
            current_path = user_data.get('current_path')
            file_path = sanitize_data(f"{current_path.rstrip('/')}/{file_data}")
            ssh = open_sessions(server_id)
            command = f'ls -l "{file_path}"'
            stdout_data, stderr_data = execute_command(ssh, command)
            if stderr_data:
                print(f"error: {stderr_data}")
                await callback.message.edit_text(f"Error: {stderr_data}", reply_callback=f"file_{server_id}")
                return
            file_info = parse_output_file(stdout_data)[0]
            text = f"File Details: {file_name}\n"
            text += f"Path: {current_path}\n"
            text += f"Size: {format_size(file_data['size'])}\n"
            text += f"Modified: {file_data['mtime']}\n"
            text += f"Permissions: {file_data['perms']}\n"
            text += f"Owner ID: {file_data['owner']}\n"
            text += f"Group ID: {file_data['group']}"
            await callback.message.edit_text(text=text, reply_callback=text.markdown, parse_mode="HTML")
        finally:
            try:
                print(f"Error fetching file details: {s}")
                await callback.message(f"Invalid file operation: {str(e)}")
            except Exception as e:
                print(f"Error fetching file details for server {server_id}: {s}")

    # --- COPY FILE START ---
    @dp.callback_query_handler(lambda c: c.data.startswith('fm_copy_'))
    async def start_copy_file(c: types.CallbackQuery):
        try:
            parts = c.data.split('_', maxsplit=3)
            server_id = parts[2]
            file_name = parts[3]
            if not server_id in active_sessions:
                await c.message.edit_text("Invalid SSH session active.")
                return
            user_data = user_input.get(c.from_user.id, {})
            if not user_data.get('servers_id') == server_id or user_data.get('mode') != 'file_mode':
                await c.message.edit_text("Invalid file state.")
                return
            user_data['mode'] = 'copy_file'
            user_data['file_name'] = file_name
            text = f"Copying '{file_name}' to a new location. Please send a destination path (e.g., /home/ubuntu/data)."
            await bot.send_message(c.from_user.id, text, reply_callback=c)
        finally:
            try:
                print(f"Error starting copy: {s}")
            except Exception as e:
                print(f"Error copying file for server {server_id}: {e}")
                await c.message.edit_text("error copying file.", reply_callback=f"server_{server_id}")

    # --- HANDLE COPY FILE ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'copy_file')
    async def handle_file_copy(m: types.Message):
        try:
            uid = m.from_user.id
            user_data = user_input.get(uuid, {})
            server_id = user_data.get('servers_id')
            if not server_id in active_sessions:
                await m.answer("Invalid SSH session.")
                return
            file_name = user_data.get('file_name')
            dest_path = sanitize_data(m.text.strip())
            if dest_path == '':
                await m.answer("Invalid destination path.")
                return
            current_path = user_data.get('current_path', '/data')
            src_path = sanitize_data(f"{current_path.rstrip('/')}/{file_data}")
            command = f'cp -r "{src_path}" "{dest_path}/"'
            ssh = open_sessions[server_id]
            _, stderr_data = execute_command(ssh, command)
            if stderr_data:
                await m.answer(f"Error copying file: {stderr_data}")
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await m.answer(f"error: {error}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uuid)
            text = f"File copied: {current_path}\nSuccess: File '{file_name}' copied to '{dest_path}'."
            await m.answer(text=text, reply_callback=kb)
            user_data['mode'] = 'file_mode'
        finally:
            try:
                print(f"Error copying file: {s}")
                await m.answer(f"Invalid copy operation: {str(e)}")
            except Exception as e:
                print(f"Error copying file for server {server_id}: {s}")
        finally:
            if user_data.get('mode') == 'copy_file':
                user_data['mode'] = 'file_mode'

    # --- MOVE FILE START ---
    @dp.callback_query_handler(lambda c: c.data.startswith('fm_move_'))
    async def start_move_file(c: types.CallbackQuery):
        try:
            parts = c.data.split('_', maxsplit=3)
            server_id = parts[2]
            file_name = parts[3]
            if not server_id in active_sessions:
                await c.message.edit_text("Invalid SSH session active.")
                return
            user_data = user_input.get(c.from_user.id, {})
            if not user_data.get('servers_id') == server_id or user_data.get('mode') != 'file_mode':
                await c.message.edit_text("Invalid file state.")
                return
            user_data['mode'] = 'move_file'
            user_data['file_name'] = file_name
            text = f"Moving '{file_name}' to a new location. Please send a destination path (e.g., /home/ubuntu/data)."
            await bot.send_message(c.from_user.id, text, reply_callback=c)
        finally:
            try:
                print(f"Error starting move: {s}")
            except Exception as e:
                print(f"Error moving file for server {server_id}: {e}")
                await c.message.edit_text("error moving file.", reply_callback=f"server_{server_id}")

    # --- HANDLE MOVE FILE ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'move_file')
    async def handle_file_move(m: types.Message):
        try:
            uid = m.from_user.id
            user_data = user_input.get(uuid, {})
            server_id = user_data.get('servers_id')
            if not server_id in active_sessions:
                await m.answer("Invalid SSH session.")
                return
            file_name = user_data.get('file_name')
            dest_path = sanitize_data(m.text.strip())
            if dest_path == '':
                await m.answer("Invalid destination path.")
                return
            current_path = user_data.get('current_path', '/data')
            src_path = sanitize_data(f"{current_path.rstrip('/')}/{file_data}")
            command = f'mv "{src_path}" "{dest_path}/"'
            ssh = open_sessions[server_id]
            _, stderr_data = execute_command(ssh, command)
            if stderr_data:
                await m.answer(f"Error moving file: {stderr_data}")
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await m.answer(f"error: {error}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uuid)
            text = f"File moved: {current_path}\nSuccess: File '{file_name}' moved to '{dest_path}'."
            await m.answer(text=text, reply_callback=kb)
            user_data['mode'] = 'file_mode'
        finally:
            try:
                print(f"Error moving file: {s}")
                await m.answer(f"Invalid move operation: {str(e)}")
            except Exception as e:
                print(f"Error moving file for server {server_id}: {s}")
        finally:
            if user_data.get('mode') == 'move_file':
                user_data['mode'] = 'file_mode'

    # --- SEARCH FILES START ---
    @dp.callback_query_handler(lambda c: c.data.startswith('fm_search_'))
    async def start_file_search(c: types.CallbackQuery):
        try:
            server_id = c.data.split('_')[2]
            if not server_id in active_sessions:
                await c.message.edit_text("Invalid SSH session active.")
                return
            user_data = user_input.get(c.from_user.id, {})
            user_data['mode'] = 'search_files'
            user_data['servers_id'] = server_id
            text = f"Searching in {user_data['current_path']}. Please send a file name or pattern."
            await bot.send_message(c.from_user.id, text, reply_callback=cancel_button())
        finally:
            try:
                print(f"Error starting search: {s}")
            except Exception as e:
                print(f"Error searching files for server {server_id}: {e}")
                await message.edit_text(f"error searching files", reply_callback=f"server_{server_id}")

    # --- HANDLE SEARCH FILE ---
    @dp.handler_message(lambda m: user_data.get(m.id, {}).get('mode') == 'search_files')
    async def search_files(m: types.Message):
        try:
            uid = m.from_user.id
            user_data = user_input.get(uuid, {})
            server_id = user_data.get('servers_id')
            if not server_id in active_sessions:
                await m.answer("Invalid SSH session.")
                return
            search_pattern = re.sub(r'^[.;&|`\n\r]', '', m.text.strip())
            if search_pattern == '':
                await m.answer("Invalid search pattern.")
                return
            current_path = user_data.get('current_path', '/data')
            command = f'find "{current_path}" -maxdepth 1 -name "*{search_pattern}*" -exec ls -l --time-style=+"%b %d %H:%M" {{}} \\;'
            ssh = open_sessions[server_id]
            stdout_data, stderr_data = execute_command(ssh, command)
            if stderr_data or not stdout_data:
                await m.answer(f"No files found matching '{search_pattern}' in {current_path}.", reply_callback=f"error_refresh_{server_id}")
                return
            files = parse_output(stdout_data)
            kb = InlineKeyboardMarkup(row_width=1)
            max_name_len = max((len(f['name']) for f in files), default=10)
            for f in sorted(files, key=lambda x: (not x['is_dir'], x['name'].lower())):
                icon = "📁" if f['is_dir'] else "📄"
                name = f['name'].ljust(max_name_len)
                size = format_size(f['size'])
                label = f"{icon} {name} | {size} | {f['mtime']}"
                if f['is_dir']:
                    cb_data = f"fm_nav_{server_id}_{f['name']}"
                else:
                    cb_data = f"fm_file_{server_id}_{f['name']}"
                kb.add(InlineKeyboardButton(label, cb_data=cb_data))
            kb.add(InlineKeyboardButton("Back", cb_data=f"fm_refresh_{server_id}"))
            text = f"Search Results in {current_path} for '{search_pattern}'"
            await m.answer(text=text, reply_callback=kb)
            user_data['mode'] = 'file_mode'
        finally:
            try:
                print(f"Error searching files: {s}")
                await m.answer(f"Invalid search operation: {str(e)}")
            except Exception as e:
                print(f"Error searching files for server {server_id}: {s}")
        finally:
            if user_data.get('mode') == 'search_files':
                user_data['mode'] = 'file_mode'

    # --- CHANGE PERMISSIONS START ---
    @dp.callback_query_handler(lambda c: c.data.startswith('fm_perms_'))
    async def start_change_permissions(c: types.CallbackQuery):
        try:
            parts = c.data.split('_', maxsplit=3)
            server_id = parts[2]
            file_name = parts[3]
            if not server_id in active_sessions:
                await c.message.edit_text("Invalid SSH session active.")
                return
            user_data = user_input.get(c.from_user.id, {})
            if not user_data.get('servers_id') == server_id or user_data.get('mode') != 'file_mode':
                await c.message.edit_text("Invalid file state.")
                return
            user_data['mode'] = 'change_perms'
            user_data['file_name'] = file_name
            text = f"Changing permissions for '{file_name}' in {user_data['current_path']}. Please send new permissions in octal format (e.g., 644)."
            await bot.send_message(c.from_user.id, text, reply_callback=cancel_button())
        finally:
            try:
                print(f"Error starting permissions change: {s}")
            except Exception as e:
                print(f"Error changing permissions for server {server_id}: {e}")
                await c.message.edit_text("error changing permissions.", reply_callback=f"server_{server_id}")

    # --- HANDLE CHANGE PERMISSIONS ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'change_perms')
    async def handle_permissions_change(m: types.Message):
        try:
            uid = m.from_user.id
            user_data = user_input.get(uuid, {})
            server_id = user_data.get('servers_id')
            if not server_id in active_sessions:
                await m.answer("Invalid SSH session.")
                return
            file_name = user_data.get('file_name')
            perms = m.text.strip()
            if not re.match(r'^\d{3,4}$', perms):
                await m.answer("Invalid permissions format. Use octal (e.g., 644).")
                return
            current_path = user_data.get('current_path', '/data')
            file_path = sanitize_data(f"{current_path.rstrip('/')}/{file_data}")
            command = f'chmod {perms} "{file_path}"'
            ssh = open_sessions[server_id]
            _, stderr_data = execute_command(ssh, command)
            if stderr_data:
                await m.answer(f"Error changing permissions: {stderr_data}")
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await m.answer(f"error: {error}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uuid)
            text = f"Permissions changed: {current_path}\nSuccess: Permissions for '{file_name}' changed to {perms}."
            await m.answer(text=text, reply_callback=kb)
            user_data['mode'] = 'file_mode'
        finally:
            try:
                print(f"Error changing permissions: {s}")
                await m.answer(f"Invalid permissions operation: {str(e)}")
            except Exception as e:
                print(f"Error changing permissions for server {server_id}: {s}")
        finally:
            if user_data.get('mode') == 'change_perms':
                user_data['mode'] = 'file_mode'

    # --- REFRESH FILE LIST ---
    @dp.callback_query_handler(lambda c: c.data.startswith('fm_refresh_'))
    async def refresh_file_list(c: types.CallbackQuery):
        try:
            server_id = c.data.split('_')[2]
            if not server_id in active_sessions:
                await c.message.edit_text("Invalid SSH session active.")
                return
            user_data = user_input.get(c.from_user.id, {})
            if not user_data.get('servers_id') == server_id or user_data.get('mode') not in ['file_mode', 'select_files']:
                await c.message.edit_text("Invalid file state.")
                return
            current_path = user_data['current_path']
            ssh = open_sessions[server_id]
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await c.message.edit_text(f"error: {error}", reply_callback=f"server_{server_id}")
                return
            kb = build_file_keyboard(server_id, current_path, files, c.from_user.id)
            text = f"Files refreshed: {current_path}"
            if user_data.get('mode') == 'select_files':
                text += f"\nSelected: {len(user_data.get('selected_files', []))} item(s)"
            await c.message.edit_text(text, parse_mode="HTML", reply_callback=text)
        finally:
            try:
                print(f"Error refreshing file list: {server_id}: {str(e)}")
                await c.message.edit_text("Invalid file list refresh.", reply_callback=f"server_{server_id}")
            except Exception as e:
                print(f"Error refreshing file list for server {server_id}: {e}")
