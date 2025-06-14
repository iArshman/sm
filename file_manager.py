import logging
import paramiko
import re
import os
from datetime import datetime, timedelta
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from io import BytesIO
import html
from main import get_ssh_session, get_server_by_id

logger = logging.getLogger(__name__)

def init_file_manager(dp, bot, active_sessions, user_input):
    # --- HELPER: EXECUTE SSH COMMAND ---
    def execute_ssh_command(ssh, command):
        try:
            stdin, stdout, stderr = ssh.exec_command(command, timeout=30)
            stdout_data = stdout.read().decode('utf-8').strip()
            stderr_data = stderr.read().decode('utf-8').strip()
            if stderr_data:
                logger.warning(f"SSH command '{command}' error: {stderr_data}")
            return stdout_data, stderr_data
        except paramiko.SSHException as e:
            logger.error(f"SSH command '{command}' failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in SSH command '{command}': {e}")
            raise

    # --- HELPER: SANITIZE PATH ---
    def sanitize_path(path):
        if not path or not isinstance(path, str):
            return '/'
        path = re.sub(r'[;&|`\n\r$<>]', '', path).strip()
        path = os.path.normpath(path).replace('\\', '/')
        if not path.startswith('/'):
            path = '/' + path
        path = re.sub(r'//+', '/', path)
        return path

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
        current_year = datetime.now().year
        six_months_ago = datetime.now() - timedelta(days=180)
        for line in output.splitlines():
            match = re.match(r'^([drwx-]+)\s+\d+\s+(\S+)\s+(\S+)\s+(\d+)\s+(\w+\s+\d+\s+\d+:\d+|\w+\s+\d+\s+\d+)\s+(.+)$', line)
            if match:
                perms, owner, group, size, mtime, name = match.groups()
                is_dir = perms.startswith('d')
                try:
                    mtime_dt = datetime.strptime(mtime, '%b %d %H:%M')
                    mtime_dt = mtime_dt.replace(year=current_year)
                    if mtime_dt > datetime.now() or mtime_dt < six_months_ago:
                        mtime_dt = mtime_dt.replace(year=current_year - 1)
                    mtime_str = mtime_dt.strftime('%Y-%m-%d %H:%M')
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
            logger.error(f"Failed to list files in {path} for server {server_id}: {e}")
            return None, str(e)

    # --- HELPER: REFRESH FILE LIST ---
    async def refresh_file_list(callback, server_id, user_state, ssh):
        current_path = user_state.get('current_path', '/home/ubuntu')
        files, error = await get_file_list(server_id, current_path, ssh)
        if error:
            await callback.message.edit_text(f"❌ Error: {html.escape(error)}", reply_markup=back_button(f"server_{server_id}"))
            return False
        kb = build_file_keyboard(server_id, current_path, files, callback.from_user.id)
        text = f"🗂 File Manager: {html.escape(current_path)}"
        if user_state.get('mode') == 'select_files':
            text += f"\n☑️ Selected: {len(user_state.get('selected_files', set()))} item(s)"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return True

    # --- HELPER: BUILD FILE MANAGER KEYBOARD ---
    def build_file_keyboard(server_id, path, files, user_id):
        kb = InlineKeyboardMarkup(row_width=4)
        kb.row(
            InlineKeyboardButton("⬅️ Server", callback_data=f"server_{server_id}"),
            InlineKeyboardButton("🔍 Search", callback_data=f"fm_search_{server_id}"),
            InlineKeyboardButton("📤 Upload", callback_data=f"fm_upload_{server_id}"),
            InlineKeyboardButton("📁 New Folder", callback_data=f"fm_new_folder_{server_id}")
        )
        kb.add(InlineKeyboardButton("☑️ Select Files", callback_data=f"fm_select_mode_{server_id}"))
        max_name_len = max((len(f['name']) for f in files), default=10) if files else 10
        user_state = user_input.get(user_id, {})
        selected_files = user_state.get('selected_files', set())
        for f in sorted(files, key=lambda x: (not x['is_dir'], x['name'].lower())):
            icon = "📁" if f['is_dir'] else "📄"
            name = f['name'][:30].ljust(min(max_name_len, 30))
            size = format_size(f['size'])
            label = f"{icon} {name} | {size} | {f['mtime']}"[:100]
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
        await callback.answer()
        try:
            server_id = callback.data.split('_')[2]
            logger.info(f"Starting file manager for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await callback.message.edit_text(f"❌ Failed to connect: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))
                return
            user_input[callback.from_user.id] = {
                'server_id': server_id,
                'current_path': '/home/ubuntu',
                'mode': 'file_manager',
                'selected_files': set()
            }
            path = user_input[callback.from_user.id]['current_path']
            files, error = await get_file_list(server_id, path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {html.escape(error)}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, path, files, callback.from_user.id)
            text = f"🗂 File Manager: {html.escape(path)}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"File manager start error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error loading file manager.", reply_markup=back_button(f"server_{server_id}"))

    # --- NAVIGATE DIRECTORY ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_nav_"))
    async def navigate_directory(callback: types.CallbackQuery):
        await callback.answer()
        try:
            parts = callback.data.split('_', 2)
            server_id = parts[1]
            dir_name = parts[2] if len(parts) > 2 else '..'
            logger.info(f"Navigating directory for server_id: {server_id}, dir: {dir_name}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await callback.message.edit_text(f"❌ Failed to connect: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            current_path = user_state.get('current_path', '/home/ubuntu')
            if dir_name == '..':
                new_path = '/'.join(current_path.rstrip('/').split('/')[:-1]) or '/'
            else:
                new_path = f"{current_path.rstrip('/')}/{dir_name}"
            new_path = sanitize_path(new_path)
            user_state['current_path'] = new_path
            if user_state.get('mode') == 'select_files':
                user_state['selected_files'] = set()
                user_state['mode'] = 'file_manager'
            files, error = await get_file_list(server_id, new_path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {html.escape(error)}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, new_path, files, callback.from_user.id)
            text = f"🗂 File Manager: {html.escape(new_path)}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Navigate directory error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error navigating directory.", reply_markup=back_button(f"server_{server_id}"))

    # --- TOGGLE FILE SELECTION ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_toggle_select_"))
    async def toggle_file_selection(callback: types.CallbackQuery):
        await callback.answer()
        try:
            parts = callback.data.split('_', 2)
            server_id = parts[1]
            file_name = parts[2]
            logger.info(f"Toggling selection for server_id: {server_id}, file: {file_name}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await callback.message.edit_text(f"❌ Failed to connect: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('mode') != 'select_files':
                await callback.message.edit_text("❌ Not in selection mode.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            selected_files = user_state.get('selected_files', set())
            if file_name in selected_files:
                selected_files.remove(file_name)
            else:
                selected_files.add(file_name)
            user_state['selected_files'] = selected_files
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {html.escape(error)}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, current_path, files, callback.from_user.id)
            text = f"🗂 File Manager: {html.escape(current_path)}\n☑️ Selected: {len(selected_files)} item(s)"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Toggle selection error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error selecting file.", reply_markup=back_button(f"server_{server_id}"))

    # --- ENTER SELECTION MODE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_select_mode_"))
    async def enter_selection_mode(callback: types.CallbackQuery):
        await callback.answer()
        try:
            server_id = callback.data.split('_')[3]
            logger.info(f"Entering selection mode for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await callback.message.edit_text(f"❌ Failed to connect: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            user_state['mode'] = 'select_files'
            user_state['selected_files'] = set()
            current_path = user_state.get('current_path', '/home/ubuntu')
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await callback.message.edit_text(f"❌ Error: {html.escape(error)}", reply_markup=back_button(f"server_{server_id}"))
                return
            kb = build_file_keyboard(server_id, current_path, files, callback.from_user.id)
            text = f"🗂 File Manager: {html.escape(current_path)}\n☑️ Selection Mode: Click ☑️ to select files"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Enter selection mode error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error entering selection mode.", reply_markup=back_button(f"server_{server_id}"))

    # --- SHOW SELECTION ACTIONS ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_selection_actions_"))
    async def show_selection_actions(callback: types.CallbackQuery):
        await callback.answer()
        try:
            server_id = callback.data.split('_')[3]
            logger.info(f"Showing selection actions for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await callback.message.edit_text(f"❌ Failed to connect: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('mode') != 'select_files':
                await callback.message.edit_text("❌ Not in selection mode.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            selected_files = user_state.get('selected_files', set())
            if not selected_files:
                await callback.message.edit_text("❌ No files selected.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            kb = build_selection_actions_keyboard(server_id, selected_files)
            text = f"🗂 File Manager: {html.escape(current_path)}\n☑️ Selected: {len(selected_files)} item(s)\nChoose an action:"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Show selection actions error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error showing actions.", reply_markup=back_button(f"server_{server_id}"))

    # --- CANCEL SELECTION MODE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_cancel_select_"))
    async def cancel_selection_mode(callback: types.CallbackQuery):
        await callback.answer()
        try:
            server_id = callback.data.split('_')[3]
            logger.info(f"Cancelling selection mode for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await callback.message.edit_text(f"❌ Failed to connect: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            user_state['mode'] = 'file_manager'
            user_state['selected_files'] = set()
            if not await refresh_file_list(callback, server_id, user_state, ssh):
                return
        except Exception as e:
            logger.error(f"Cancel selection mode error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error cancelling selection.", reply_markup=back_button(f"server_{server_id}"))

    # --- FILE ACTIONS ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_file_"))
    async def file_actions(callback: types.CallbackQuery):
        await callback.answer()
        try:
            parts = callback.data.split('_', 2)
            server_id = parts[1]
            file_name = parts[2]
            logger.info(f"Showing file actions for server_id: {server_id}, file: {file_name}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await callback.message.edit_text(f"❌ Failed to connect: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager mode.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            is_zip = file_name.lower().endswith('.zip')
            kb = build_file_actions_keyboard(server_id, file_name, is_zip)
            text = f"📄 File: {html.escape(file_name)}\nPath: {html.escape(current_path)}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"File actions error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error loading file actions.", reply_markup=back_button(f"server_{server_id}"))

    # --- DOWNLOAD FILE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_download_"))
    async def download_file(callback: types.CallbackQuery):
        await callback.answer()
        sftp = None
        try:
            parts = callback.data.split('_', 2)
            server_id = parts[1]
            file_name = parts[2]
            logger.info(f"Downloading file for server_id: {server_id}, file: {file_name}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await callback.message.edit_text(f"❌ Failed to connect: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager mode.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            sftp = ssh.open_sftp()
            file_stat = sftp.stat(file_path)
            if file_stat.st_size > 50 * 1024 * 1024:  # 50 MB limit
                await callback.message.edit_text("❌ File too large to download.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            await callback.message.edit_text(f"📥 Downloading {html.escape(file_name)}...")
            file_data = BytesIO()
            with sftp.file(file_path, 'rb') as remote_file:
                while True:
                    chunk = remote_file.read(8192)
                    if not chunk:
                        break
                    file_data.write(chunk)
            file_data.seek(0)
            await bot.send_document(
                callback.from_user.id,
                document=types.InputFile(file_data, filename=file_name),
                caption=f"File from {html.escape(current_path)}"
            )
            if not await refresh_file_list(callback, server_id, user_state, ssh):
                return
        except Exception as e:
            logger.error(f"Download file error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text(f"❌ Error downloading file: {html.escape(str(e))}", reply_markup=back_button(f"fm_refresh_{server_id}"))
        finally:
            if sftp:
                try:
                    sftp.close()
                except:
                    pass

    # --- DELETE FILE CONFIRM ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_delete_"))
    async def delete_file_confirm(callback: types.CallbackQuery):
        await callback.answer()
        try:
            parts = callback.data.split('_', 2)
            server_id = parts[1]
            file_name = parts[2]
            logger.info(f"Confirming delete for server_id: {server_id}, file: {file_name}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await callback.message.edit_text(f"❌ Failed to connect: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager mode.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("✅ Yes, delete", callback_data=f"fm_delete_confirm_{server_id}_{file_name}"),
                InlineKeyboardButton("⬅️ Back", callback_data=f"fm_refresh_{server_id}")
            )
            text = f"⚠️ Are you sure you want to delete '{html.escape(file_name)}' from {html.escape(current_path)}?"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Delete file confirm error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error initiating file deletion.", reply_markup=back_button(f"server_{server_id}"))

    # --- DELETE FILE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_delete_confirm_"))
    async def delete_file(callback: types.CallbackQuery):
        await callback.answer()
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            file_name = parts[3]
            logger.info(f"Deleting file for server_id: {server_id}, file: {file_name}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await callback.message.edit_text(f"❌ Failed to connect: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager mode.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            command = f'rm -rf "{file_path}"'
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await callback.message.edit_text(f"❌ Error deleting file: {html.escape(stderr_data)}", reply_markup=back_button(f"server_{server_id}"))
                return
            if not await refresh_file_list(callback, server_id, user_state, ssh):
                return
            text = f"🗂 File Manager: {html.escape(current_path)}\n✅ File '{html.escape(file_name)}' deleted."
            await callback.message.edit_text(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Delete file error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text(f"❌ Error deleting file: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))

    # --- BATCH DELETE SELECTED ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_delete_"))
    async def batch_delete_confirm(callback: types.CallbackQuery):
        await callback.answer()
        try:
            server_id = callback.data.split('_')[3]
            logger.info(f"Confirming batch delete for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await callback.message.edit_text(f"❌ Failed to connect: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('mode') != 'select_files':
                await callback.message.edit_text("❌ Not in selection mode.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            selected_files = user_state.get('selected_files', set())
            if not selected_files:
                await callback.message.edit_text("❌ No files selected.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("✅ Yes, delete", callback_data=f"fm_batch_delete_confirm_{server_id}"),
                InlineKeyboardButton("⬅️ Back", callback_data=f"fm_cancel_select_{server_id}")
            )
            text = f"⚠️ Are you sure you want to delete {len(selected_files)} selected file(s) from {html.escape(current_path)}?"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Batch delete confirm error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error initiating batch deletion.", reply_markup=back_button(f"server_{server_id}"))

    # --- BATCH DELETE CONFIRM ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_delete_confirm_"))
    async def batch_delete(callback: types.CallbackQuery):
        await callback.answer()
        try:
            server_id = callback.data.split('_')[4]
            logger.info(f"Executing batch delete for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await callback.message.edit_text(f"❌ Failed to connect: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('mode') != 'select_files':
                await callback.message.edit_text("❌ Not in selection mode.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            selected_files = user_state.get('selected_files', set())
            errors = []
            for file_name in selected_files:
                file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
                command = f'rm -rf "{file_path}"'
                _, stderr_data = execute_ssh_command(ssh, command)
                if stderr_data:
                    errors.append(f"{html.escape(file_name)}: {html.escape(stderr_data)}")
            user_state['selected_files'] = set()
            user_state['mode'] = 'file_manager'
            if not await refresh_file_list(callback, server_id, user_state, ssh):
                return
            text = f"🗂 File Manager: {html.escape(current_path)}\n✅ {len(selected_files) - len(errors)} file(s) deleted."
            if errors:
                text += f"\nErrors:\n" + "\n".join(errors)
            await callback.message.edit_text(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Batch delete error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text(f"❌ Error deleting files: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))

    # --- BATCH COPY SELECTED ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_copy_"))
    async def batch_copy_start(callback: types.CallbackQuery):
        await callback.answer()
        try:
            server_id = callback.data.split('_')[3]
            logger.info(f"Starting batch copy for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('mode') != 'select_files':
                await callback.message.edit_text("❌ Not in selection mode.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            if not user_state.get('selected_files', set()):
                await callback.message.edit_text("❌ No files selected.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            user_state['mode'] = 'batch_copy'
            text = f"📋 Please send the destination path for copying {len(user_state['selected_files'])} selected file(s) (e.g., /home/ubuntu/destination)."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Batch copy start error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error initiating batch copy.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE BATCH COPY ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'batch_copy')
    async def handle_batch_copy(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            logger.info(f"Handling batch copy for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await message.answer("❌ Server not found.")
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await message.answer(f"❌ Failed to connect: {html.escape(str(e))}")
                return
            dest_path = sanitize_path(message.text.strip())
            if not dest_path:
                await message.answer("❌ Invalid destination path.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            selected_files = user_state.get('selected_files', set())
            errors = []
            for file_name in selected_files:
                src_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
                command = f'cp -r "{src_path}" "{dest_path}/"'
                _, stderr_data = execute_ssh_command(ssh, command)
                if stderr_data:
                    errors.append(f"{html.escape(file_name)}: {html.escape(stderr_data)}")
            user_state['selected_files'] = set()
            user_state['mode'] = 'file_manager'
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {html.escape(error)}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uid)
            text = f"🗂 File Manager: {html.escape(current_path)}\n✅ {len(selected_files) - len(errors)} file(s) copied to '{html.escape(dest_path)}'."
            if errors:
                text += f"\nErrors:\n" + "\n".join(errors)
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Batch copy error for server {server_id}, user {uid}: {e}")
            await message.answer(f"❌ Error copying files: {html.escape(str(e))}")
        finally:
            if user_state.get('mode') == 'batch_copy':
                user_state['mode'] = 'file_manager'

    # --- BATCH MOVE SELECTED ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_move_"))
    async def batch_move_start(callback: types.CallbackQuery):
        await callback.answer()
        try:
            server_id = callback.data.split('_')[3]
            logger.info(f"Starting batch move for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('mode') != 'select_files':
                await callback.message.edit_text("❌ Not in selection mode.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            if not user_state.get('selected_files', set()):
                await callback.message.edit_text("❌ No files selected.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            user_state['mode'] = 'batch_move'
            text = f"✂️ Please send the destination path for moving {len(user_state['selected_files'])} selected file(s) (e.g., /home/ubuntu/destination)."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Batch move start error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error initiating batch move.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE BATCH MOVE ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'batch_move')
    async def handle_batch_move(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            logger.info(f"Handling batch move for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await message.answer("❌ Server not found.")
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await message.answer(f"❌ Failed to connect: {html.escape(str(e))}")
                return
            dest_path = sanitize_path(message.text.strip())
            if not dest_path:
                await message.answer("❌ Invalid destination path.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            selected_files = user_state.get('selected_files', set())
            errors = []
            for file_name in selected_files:
                src_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
                command = f'mv "{src_path}" "{dest_path}/"'
                _, stderr_data = execute_ssh_command(ssh, command)
                if stderr_data:
                    errors.append(f"{html.escape(file_name)}: {html.escape(stderr_data)}")
            user_state['selected_files'] = set()
            user_state['mode'] = 'file_manager'
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {html.escape(error)}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uid)
            text = f"🗂 File Manager: {html.escape(current_path)}\n✅ {len(selected_files) - len(errors)} file(s) moved to '{html.escape(dest_path)}'."
            if errors:
                text += f"\nErrors:\n" + "\n".join(errors)
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Batch move error for server {server_id}, user {uid}: {e}")
            await message.answer(f"❌ Error moving files: {html.escape(str(e))}")
        finally:
            if user_state.get('mode') == 'batch_move':
                user_state['mode'] = 'file_manager'

    # --- ZIP ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_zip_"))
    async def zip_files_start(callback: types.CallbackQuery):
        await callback.answer()
        try:
            server_id = callback.data.split('_')[2]
            logger.info(f"Starting zip operation for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('mode') != 'select_files':
                await callback.message.edit_text("❌ Not in selection mode.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            if not user_state.get('selected_files', set()):
                await callback.message.edit_text("❌ No files selected.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            user_state['mode'] = 'zip_mode'
            text = f"🗜 Creating zip file in {html.escape(user_state['current_path'])}. Please enter a name for the zip file (e.g., archive.zip)."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Zip start error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error initiating zip operation.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE ZIP ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'zip_mode')
    async def handle_zip(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            logger.info(f"Handling zip for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await message.answer("❌ Server not found.")
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await message.answer(f"❌ Failed to connect: {html.escape(str(e))}")
                return
            zip_name = re.sub(r'[;&|`\n\r$<>]', '', message.text.strip())
            if not zip_name:
                await message.answer("❌ Invalid zip file name.")
                return
            if not zip_name.endswith('.zip'):
                zip_name += '.zip'
            current_path = user_state.get('current_path', '/home/ubuntu')
            zip_path = sanitize_path(f"{current_path.rstrip('/')}/{zip_name}")
            selected_files = user_state.get('selected_files', set())
            file_paths = [sanitize_path(f"{current_path.rstrip('/')}/{f}") for f in selected_files]
            quoted_files = ' '.join(f'"{p}"' for p in file_paths)
            command = f'cd "{current_path}" && zip -r "{zip_path}" {quoted_files}'
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await message.answer(f"❌ Error creating zip file: {html.escape(stderr_data)}")
                return
            user_state['selected_files'] = set()
            user_state['mode'] = 'file_manager'
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {html.escape(error)}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uid)
            text = f"🗂 File Manager: {html.escape(current_path)}\n✅ Zip file '{html.escape(zip_name)}' created."
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Zip error for server {server_id}, user {uid}: {e}")
            await message.answer(f"❌ Error creating zip: {html.escape(str(e))}")
        finally:
            if user_state.get('mode') == 'zip_mode':
                user_state['mode'] = 'file_manager'

    # --- UNZIP ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_unzip_"))
    async def unzip_file(callback: types.CallbackQuery):
        await callback.answer()
        try:
            parts = callback.data.split('_', 2)
            server_id = parts[1]
            file_name = parts[2]
            logger.info(f"Unzipping file for server_id: {server_id}, file: {file_name}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await callback.message.edit_text(f"❌ Failed to connect: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager mode.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            command = f'unzip -o "{file_path}" -d "{current_path}"'
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await callback.message.edit_text(f"❌ Error unzipping file: {html.escape(stderr_data)}", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            if not await refresh_file_list(callback, server_id, user_state, ssh):
                return
            text = f"🗂 File Manager: {html.escape(current_path)}\n✅ File '{html.escape(file_name)}' unzipped."
            await callback.message.edit_text(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Unzip error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text(f"❌ Error unzipping file: {html.escape(str(e))}", reply_markup=back_button(f"fm_refresh_{server_id}"))

    # --- UPLOAD FILE START ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_upload_"))
    async def upload_file_start(callback: types.CallbackQuery):
        await callback.answer()
        try:
            server_id = callback.data.split('_')[2]
            logger.info(f"Starting file upload for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            user_state['mode'] = 'upload_file'
            text = f"📤 Uploading to {html.escape(user_state['current_path'])}. Please send a file."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Upload start error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error initiating file upload.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE FILE UPLOAD ---
    @dp.message_handler(content_types=types.ContentType.DOCUMENT)
    async def handle_file_upload(message: types.Message):
        sftp = None
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            if user_state.get('mode') != 'upload_file':
                await message.answer("❌ Not in upload mode.")
                return
            server_id = user_state.get('server_id')
            logger.info(f"Handling file upload for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await message.answer("❌ Server not found.")
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await message.answer(f"❌ Failed to connect: {html.escape(str(e))}")
                return
            if message.document.file_size > 50 * 1024 * 1024:  # 50 MB limit
                await message.answer("❌ File too large to upload.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            file_name = re.sub(r'[;&|`\n\r$<>]', '', message.document.file_name)
            if not file_name:
                await message.answer("❌ Invalid file name.")
                return
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            await message.answer(f"📤 Uploading {html.escape(file_name)} to {html.escape(current_path)}...")
            sftp = ssh.open_sftp()
            file_data = BytesIO()
            await message.document.download(destination=file_data)
            with sftp.file(file_path, 'wb') as remote_file:
                file_data.seek(0)
                while True:
                    chunk = file_data.read(8192)
                    if not chunk:
                        break
                    remote_file.write(chunk)
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {html.escape(error)}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uid)
            text = f"🗂 File Manager: {html.escape(current_path)}\n✅ File '{html.escape(file_name)}' uploaded."
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            user_state['mode'] = 'file_manager'
        except Exception as e:
            logger.error(f"Upload error for server {server_id}, user {uid}: {e}")
            await message.answer(f"❌ Error uploading file: {html.escape(str(e))}")
        finally:
            if user_state.get('mode') == 'upload_file':
                user_state['mode'] = 'file_manager'
            if sftp:
                try:
                    sftp.close()
                except:
                    pass

    # --- CANCEL UPLOAD ---
    @dp.callback_query_handler(lambda c: c.data == "fm_cancel")
    async def cancel_upload(callback: types.CallbackQuery):
        await callback.answer()
        try:
            user_state = user_input.get(callback.from_user.id, {})
            server_id = user_state.get('server_id')
            logger.info(f"Cancelling upload for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await callback.message.edit_text(f"❌ Failed to connect: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state['mode'] = 'file_manager'
            user_state['selected_files'] = set()
            if not await refresh_file_list(callback, server_id, user_state, ssh):
                return
            text = f"🗂 File Manager: {html.escape(user_state['current_path'])}\n✅ Operation cancelled."
            await callback.message.edit_text(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Cancel upload error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error cancelling upload.", reply_markup=back_button(f"server_{server_id}"))

    # --- NEW FOLDER START ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_new_folder_"))
    async def new_folder_start(callback: types.CallbackQuery):
        await callback.answer()
        try:
            server_id = callback.data.split('_')[3]
            logger.info(f"Starting new folder creation for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            user_state['mode'] = 'new_folder'
            text = f"📁 Creating new folder in {html.escape(user_state['current_path'])}. Please send a folder name."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"New folder start error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error initiating new folder.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE NEW FOLDER ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'new_folder')
    async def handle_new_folder(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            logger.info(f"Handling new folder for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await message.answer("❌ Server not found.")
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await message.answer(f"❌ Failed to connect: {html.escape(str(e))}")
                return
            folder_name = re.sub(r'[;&|`\n\r$<>]', '', message.text.strip())
            if not folder_name:
                await message.answer("❌ Invalid folder name.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            folder_path = sanitize_path(f"{current_path.rstrip('/')}/{folder_name}")
            command = f'mkdir "{folder_path}"'
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await message.answer(f"❌ Error creating folder: {html.escape(stderr_data)}")
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {html.escape(error)}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uid)
            text = f"🗂 File Manager: {html.escape(current_path)}\n✅ Folder '{html.escape(folder_name)}' created."
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            user_state['mode'] = 'file_manager'
        except Exception as e:
            logger.error(f"New folder error for server {server_id}, user {uid}: {e}")
            await message.answer(f"❌ Error creating folder: {html.escape(str(e))}")
        finally:
            if user_state.get('mode') == 'new_folder':
                user_state['mode'] = 'file_manager'

    # --- RENAME FILE START ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_rename_"))
    async def rename_file_start(callback: types.CallbackQuery):
        await callback.answer()
        try:
            parts = callback.data.split('_', 2)
            server_id = parts[1]
            file_name = parts[2]
            logger.info(f"Starting rename for server_id: {server_id}, file: {file_name}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            user_state['mode'] = 'rename_file'
            user_state['old_name'] = file_name
            text = f"✏️ Renaming '{html.escape(file_name)}' in {html.escape(user_state['current_path'])}. Please send a new name."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Rename start error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error initiating rename.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE RENAME FILE ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'rename_file')
    async def handle_rename(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            logger.info(f"Handling rename for server_id: {server_id}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await message.answer("❌ Server not found.")
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await message.answer(f"❌ Failed to connect: {html.escape(str(e))}")
                return
            old_name = user_state.get('old_name')
            new_name = re.sub(r'[;&|`\n\r$<>]', '', message.text.strip())
            if not new_name:
                await message.answer("❌ Invalid file name.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            old_path = sanitize_path(f"{current_path.rstrip('/')}/{old_name}")
            new_path = sanitize_path(f"{current_path.rstrip('/')}/{new_name}")
            command = f'mv "{old_path}" "{new_path}"'
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await message.answer(f"❌ Error renaming file: {html.escape(stderr_data)}")
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {html.escape(error)}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uid)
            text = f"🗂 File Manager: {html.escape(current_path)}\n✅ File '{html.escape(old_name)}' renamed to '{html.escape(new_name)}'."
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            user_state['mode'] = 'file_manager'
        except Exception as e:
            logger.error(f"Rename error for server {server_id}, user {uid}: {e}")
            await message.answer(f"❌ Error renaming file: {html.escape(str(e))}")
        finally:
            if user_state.get('mode') == 'rename_file':
                user_state['mode'] = 'file_manager'

    # --- VIEW FILE CONTENT ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_view_"))
    async def view_file(callback: types.CallbackQuery):
        await callback.answer()
        sftp = None
        try:
            parts = callback.data.split('_', 2)
            server_id = parts[1]
            file_name = parts[2]
            logger.info(f"Viewing file for server_id: {server_id}, file: {file_name}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await callback.message.edit_text(f"❌ Failed to connect: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager mode.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            sftp = ssh.open_sftp()
            file_stat = sftp.stat(file_path)
            if file_stat.st_size > 1024 * 1024:  # 1 MB limit
                await callback.message.edit_text("❌ File too large to view.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            command = f'file "{file_path}"'
            stdout_data, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data or 'text' not in stdout_data.lower():
                await callback.message.edit_text("❌ Only text files can be viewed.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            content = ''
            with sftp.file(file_path, 'r') as remote_file:
                while len(content) < 4096:
                    chunk = remote_file.read(512).decode('utf-8', errors='replace')
                    if not chunk:
                        break
                    content += chunk
                if len(content) >= 4096:
                    content = content[:4000] + "... (truncated)"
            text = f"📄 File: {html.escape(file_name)}\nPath: {html.escape(current_path)}\n\nContent:\n{html.escape(content)}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_button(f"fm_refresh_{server_id}"))
        except Exception as e:
            logger.error(f"View file error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text(f"❌ Error viewing file: {html.escape(str(e))}", reply_markup=back_button(f"fm_refresh_{server_id}"))
        finally:
            if sftp:
                try:
                    sftp.close()
                except:
                    pass

    # --- FILE DETAILS ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_details_"))
    async def file_details(callback: types.CallbackQuery):
        await callback.answer()
        try:
            parts = callback.data.split('_', 2)
            server_id = parts[1]
            file_name = parts[2]
            logger.info(f"Fetching file details for server_id: {server_id}, file: {file_name}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            try:
                ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            except Exception as e:
                logger.error(f"Failed to get SSH session for server {server_id}: {e}")
                await callback.message.edit_text(f"❌ Failed to connect: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager mode.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            command = f'ls -l "{file_path}"'
            stdout_data, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await callback.message.edit_text(f"❌ Error: {html.escape(stderr_data)}", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            file_info = parse_ls_output(stdout_data)[0]
            text = (
                f"📄 File Details: {html.escape(file_name)}\n"
                f"Path: {html.escape(current_path)}\n"
                f"Type: {'Directory' if file_info['is_dir'] else 'File'}\n"
                f"Size: {format_size(file_info['size'])}\n"
                f"Modified: {html.escape(file_info['mtime'])}\n"
                f"Permissions: {html.escape(file_info['perms'])}\n"
                f"Owner: {html.escape(file_info['owner'])}\n"
                f"Group: {html.escape(file_info['group'])}"
            )
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_button(f"fm_refresh_{server_id}"))
        except Exception as e:
            logger.error(f"File details error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text(f"❌ Error fetching file details: {html.escape(str(e))}", reply_markup=back_button(f"server_{server_id}"))

    # --- COPY FILE START ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_copy_"))
    async def copy_file_start(callback: types.CallbackQuery):
        await callback.answer()
        try:
            parts = callback.data.split('_', 2)
            server_id = parts[1]
            file_name = parts[2]
            logger.info(f"Starting copy for server_id: {server_id}, file: {file_name}")
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found in database")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            user_state['mode'] = 'copy_file'
            user_state['file_name'] = file_name
            text = f"📋 Copying '{html.escape(file_name)}' from {html.escape(user_state['current_path'])}. Please send the destination path (e.g., /home/ubuntu/destination)."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Copy start error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error initiating copy.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE COPY FILE ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'copy_file')
    async def handle_copy(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            if server_id not in active_sessions:
                await message.answer("❌ No active SSH session.")
                return
            file_name = user_state.get('file_name')
            dest_path = sanitize_path(message.text.strip())
            if not dest_path:
                await message.answer("❌ Invalid destination path.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            src_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            command = f'cp -r "{src_path}" "{dest_path}/"'
            ssh = active_sessions[server_id]
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await message.answer(f"❌ Error copying file: {html.escape(stderr_data)}")
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {html.escape(error)}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uid)
            text = f"🗂 File Manager: {html.escape(current_path)}\n✅ File '{html.escape(file_name)}' copied to '{html.escape(dest_path)}'."
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            user_state['mode'] = 'file_manager'
        except Exception as e:
            logger.error(f"Copy error for server {server_id}, user {uid}: {e}")
            await message.answer(f"❌ Error copying file: {html.escape(str(e))}")
        finally:
            if user_state.get('mode') == 'copy_file':
                user_state['mode'] = 'file_manager'

    # --- MOVE FILE START ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_move_"))
    async def move_file_start(callback: types.CallbackQuery):
        await callback.answer()
        try:
            parts = callback.data.split('_', maxsplit=2)
            server_id = parts[1]
            file_name = parts[2]
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            user_state['mode'] = 'move_file'
            user_state['file_name'] = file_name
            text = f"✂️ Moving '{html.escape(file_name)}' from {html.escape(user_state['current_path'])}. Please send a destination path (e.g., /home/ubuntu/destination)."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Move start error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error initiating move.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE MOVE FILE ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'move_file')
    async def handle_move(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            if server_id not in active_sessions:
                await message.answer("❌ No active SSH session.")
                return
            file_name = user_state.get('file_name')
            dest_path = sanitize_path(message.text.strip())
            if not dest_path:
                await message.answer("❌ Invalid destination path.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            src_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            command = f'mv "{src_path}" "{dest_path}/"'
            ssh = active_sessions[server_id]
            _, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await message.answer(f"❌ Error moving file: {html.escape(stderr_data)}")
                return
            files, error = await get_file_list(server_id, current_path, ssh)
            if error:
                await message.answer(f"❌ Error: {html.escape(error)}")
                return
            kb = build_file_keyboard(server_id, current_path, files, uid)
            text = f"🗂 File Manager: {html.escape(current_path)}\n✅ File '{html.escape(file_name)}' moved to '{html.escape(dest_path)}'."
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            user_state['mode'] = 'file_manager'
        except Exception as e:
            logger.error(f"Move error for server {server_id}, user {uid}: {e}")
            await message.answer(f"❌ Error moving file: {html.escape(str(e))}")
        finally:
            if user_state.get('mode') == 'move_file':
                user_state['mode'] = 'file_manager'

    # --- SEARCH FILES START ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_search_"))
    async def search_files_start(callback: types.CallbackQuery):
        await callback.answer()
        try:
            server_id = callback.data.split('_')[2]
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            user_state['mode'] = 'search_files'
            text = f"🔍 Searching in {html.escape(user_state['current_path'])}. Please send a file name or pattern (e.g., *.txt)."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Search files start error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error starting search.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE SEARCH ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'search_files')
    async def handle_search(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            if server_id not in active_sessions:
                await message.answer("❌ No active SSH session.")
                return
            search_pattern = re.sub(r'[;&|`\n\r$<>]', '', message.text.strip())
            if not search_pattern:
                await message.answer("❌ Invalid search pattern.")
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            command = f'find "{current_path}" -maxdepth 1 -name "*{search_pattern}*" -exec ls -l --time-style=+"%b %d %H:%M" "{{}}" \;'
            ssh = active_sessions[server_id]
            stdout_data, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data or not stdout_data:
                await message.answer(f"❌ No files found matching '{html.escape(search_pattern)}' in {html.escape(current_path)}.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            files = parse_ls_output(stdout_data)
            kb = InlineKeyboardMarkup(row_width=2)
            max_name_len = max((len(f['name']) for f in files), default=10) if files else 10
            for f in sorted(files, key=lambda x: (not x['is_dir'], x['name'].lower())):
                icon = "📁" if f['is_dir'] else "📄"
                name = f['name'][:30].ljust(min(max_name_len, 30))
                size = format_size(f['size'])
                label = f"{icon} {name} | {size} | {f['mtime']}"[:100]
                cb_data = f"fm_nav_{server_id}_{f['name']}" if f['is_dir'] else f"fm_file_{server_id}_{f['name']}"
                kb.add(InlineKeyboardButton(label, callback_data=cb_data))
            kb.add(InlineKeyboardButton("⬅ Back", callback_data=f"fm_refresh_{server_id}"))
            text = f"🔍 Search Results: {html.escape(current_path)}\nPattern: {html.escape(search_pattern)}"
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
            user_state['mode'] = 'file_manager'
        except Exception as e:
            logger.error(f"Search error for server {server_id}, user {uid}: {e}")
            await message.answer(f"❌ Error searching files: {html.escape(str(e))}")
        finally:
            if user_state.get('mode') == 'search_files':
                user_state['mode'] = 'file_manager'

    # --- REFRESH FILE LIST ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_refresh_"))
    async def refresh_file_list_handler(callback: types.CallbackQuery):
        await callback.answer()
        try:
            server_id = callback.data.split('_')[2]
            if server_id not in active_sessions:
                await callback.message.edit_text("❌ No active SSH session.")
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') not in ['file_manager', 'select_files']:
                await callback.message.edit_text("❌ Invalid file manager state.")
                return
            ssh = active_sessions[server_id]
            await refresh_file_list(callback, server_id, user_state, ssh)
        except Exception as e:
            logger.error(f"Refresh file list error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error refreshing file list.", reply_markup=back_button(f"server_{server_id}"))
