import logging
import paramiko
import re
import os
from datetime import datetime, timedelta
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from io import BytesIO
import html

logger = logging.getLogger(__name__)

def init_file_manager(dp, bot, active_sessions, user_input):
    # --- HELPER: SANITIZE PATH ---
    def sanitize_path(path):
        if not path or not isinstance(path, str):
            return '/home/ubuntu'
        path = re.sub(r'[;&|`\n\r$<>]', '', path).strip()
        path = os.path.normpath(path).replace('\\', '/')
        if not path.startswith('/'):
            path = '/home/ubuntu/' + path
        return re.sub(r'//+', '/', path)

    # --- HELPER: FORMAT FILE SIZE ---
    def format_size(size_bytes):
        if not isinstance(size_bytes, (int, float)) or size_bytes < 0:
            return "Unknown"
        units = ["B", "KB", "MB", "GB"]
        size = float(size_bytes)
        unit_idx = 0
        while size >= 1024 and unit_idx < len(units) - 1:
            size /= 1024
            unit_idx += 1
        return f"{size:.2f} {units[unit_idx]}"

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
                    mtime_dt = datetime.strptime(mtime, '%b %d %H:%M').replace(year=current_year)
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

    # --- HELPER: GET FILE LIST ---
    async def get_file_list(server_id, path):
        from main import get_ssh_session, get_server_by_id  # Delayed import
        try:
            server = await get_server_by_id(server_id)
            if not server:
                return None, "Server not found"
            ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            path = sanitize_path(path)
            command = f'ls -l --time-style=+"%b %d %H:%M" "{path}"'
            stdout_data, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data and "No such file or directory" in stderr_data:
                return None, f"Directory '{path}' not found"
            return parse_ls_output(stdout_data), None
        except Exception as e:
            logger.error(f"Failed to list files in {path} for server {server_id}: {e}")
            return None, str(e)

    # --- HELPER: REFRESH FILE LIST ---
    async def refresh_file_list(callback, server_id, user_state):
        current_path = user_state.get('current_path', '/home/ubuntu')
        files, error = await get_file_list(server_id, current_path)
        if error:
            await callback.message.edit_text(f"❌ Error: {html.escape(error)}", reply_markup=back_button(f"server_{server_id}"))
            return False
        kb = build_file_keyboard(server_id, current_path, files, callback.from_user.id)
        text = f"🗂 File Manager: {html.escape(current_path)}"
        if user_state.get('mode') == 'select_files':
            text += f"\n☑️ Selected: {len(user_state.get('selected_files', set()))} item(s)"
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        return True

    # --- HELPER: PARSE CALLBACK DATA ---
    def parse_callback_data(data):
        parts = data.split('_', maxsplit=3)
        if len(parts) < 2:
            return None, None, None
        action = parts[0]
        server_id = parts[1]
        rest = parts[2] if len(parts) > 2 else ''
        return action, server_id, rest

    # --- HELPER: HANDLE FILE OPERATION ---
    async def handle_file_operation(server_id, user_state, operation, files, dest_path=None, new_name=None):
        from main import get_ssh_session, get_server_by_id  # Delayed import
        try:
            server = await get_server_by_id(server_id)
            if not server:
                return None, "Server not found"
            ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            current_path = user_state.get('current_path', '/home/ubuntu')
            errors = []
            commands = []
            
            for file_name in files:
                src_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
                if operation == 'delete':
                    commands.append(f'rm -rf "{src_path}"')
                elif operation == 'copy':
                    if not dest_path:
                        return None, "Destination path required"
                    commands.append(f'cp -r "{src_path}" "{sanitize_path(dest_path)}/"')
                elif operation == 'move':
                    if not dest_path:
                        return None, "Destination path required"
                    commands.append(f'mv "{src_path}" "{sanitize_path(dest_path)}/"')
                elif operation == 'rename':
                    if not new_name:
                        return None, "New name required"
                    new_path = sanitize_path(f"{current_path.rstrip('/')}/{new_name}")
                    commands.append(f'mv "{src_path}" "{new_path}"')
                elif operation == 'zip':
                    if not new_name:
                        return None, "Zip name required"
                    zip_path = sanitize_path(f"{current_path.rstrip('/')}/{new_name}")
                    quoted_files = ' '.join(f'"{sanitize_path(os.path.join(current_path.rstrip("/"), f))}"' for f in files)
                    commands.append(f'cd "{current_path}" && zip -r "{zip_path}" {quoted_files}')
                elif operation == 'unzip':
                    commands.append(f'unzip -o "{src_path}" -d "{current_path}"')
                elif operation == 'mkdir':
                    commands.append(f'mkdir "{src_path}"')

            for command in commands:
                _, stderr_data = execute_ssh_command(ssh, command)
                if stderr_data:
                    errors.append(stderr_data)

            return None, "\n".join(errors) if errors else None
        except Exception as e:
            logger.error(f"File operation '{operation}' error for server {server_id}: {e}")
            return None, str(e)

    # --- HELPER: BUILD FILE KEYBOARD ---
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
            _, server_id, _ = parse_callback_data(callback.data)
            from main import get_ssh_session, get_server_by_id  # Delayed import
            # Check if session exists, otherwise attempt to create it
            if server_id not in active_sessions:
                server = await get_server_by_id(server_id)
                if not server:
                    logger.error(f"Server {server_id} not found")
                    await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                    return
                try:
                    ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
                    active_sessions[server_id] = ssh  # Store the new session
                except Exception as e:
                    logger.error(f"Failed to establish SSH session for server {server_id}: {e}")
                    await callback.message.edit_text("❌ Failed to establish SSH session.", reply_markup=back_button(f"server_{server_id}"))
                    return
            user_input[callback.from_user.id] = {
                'server_id': server_id,
                'current_path': '/home/ubuntu',
                'mode': 'file_manager',
                'selected_files': set()
            }
            await refresh_file_list(callback, server_id, user_input[callback.from_user.id])
        except Exception as e:
            logger.error(f"File manager start error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error loading file manager.", reply_markup=back_button(f"server_{server_id}"))

    # --- NAVIGATE DIRECTORY ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_nav_"))
    async def navigate_directory(callback: types.CallbackQuery):
        await callback.answer()
        try:
            _, server_id, dir_name = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            current_path = user_state.get('current_path', '/home/ubuntu')
            new_path = '/'.join(current_path.rstrip('/').split('/')[:-1]) or '/' if dir_name == '..' else f"{current_path.rstrip('/')}/{dir_name}"
            user_state['current_path'] = sanitize_path(new_path)
            if user_state.get('mode') == 'select_files':
                user_state['selected_files'] = set()
                user_state['mode'] = 'file_manager'
            await refresh_file_list(callback, server_id, user_state)
        except Exception as e:
            logger.error(f"Navigate directory error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error navigating directory.", reply_markup=back_button(f"server_{server_id}"))

    # --- TOGGLE FILE SELECTION ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_toggle_select_"))
    async def toggle_file_selection(callback: types.CallbackQuery):
        await callback.answer()
        try:
            _, server_id, file_name = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'select_files':
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            selected_files = user_state.get('selected_files', set())
            if file_name in selected_files:
                selected_files.remove(file_name)
            else:
                selected_files.add(file_name)
            user_state['selected_files'] = selected_files
            await refresh_file_list(callback, server_id, user_state)
        except Exception as e:
            logger.error(f"Toggle selection error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error selecting file.", reply_markup=back_button(f"server_{server_id}"))

    # --- ENTER SELECTION MODE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_select_mode_"))
    async def enter_selection_mode(callback: types.CallbackQuery):
        await callback.answer()
        try:
            _, server_id, _ = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state['mode'] = 'select_files'
            user_state['selected_files'] = set()
            await refresh_file_list(callback, server_id, user_state)
        except Exception as e:
            logger.error(f"Enter selection mode error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error entering selection mode.", reply_markup=back_button(f"server_{server_id}"))

    # --- SHOW SELECTION ACTIONS ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_selection_actions_"))
    async def show_selection_actions(callback: types.CallbackQuery):
        await callback.answer()
        try:
            _, server_id, _ = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'select_files':
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            selected_files = user_state.get('selected_files', set())
            if not selected_files:
                await callback.message.edit_text("❌ No files selected.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            kb = build_selection_actions_keyboard(server_id, selected_files)
            text = f"🗂 File Manager: {html.escape(user_state['current_path'])}\n☑️ Selected: {len(selected_files)} item(s)\nChoose an action:"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.error(f"Show selection actions error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error showing actions.", reply_markup=back_button(f"server_{server_id}"))

    # --- CANCEL SELECTION MODE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_cancel_select_"))
    async def cancel_selection_mode(callback: types.CallbackQuery):
        await callback.answer()
        try:
            _, server_id, _ = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state['mode'] = 'file_manager'
            user_state['selected_files'] = set()
            await refresh_file_list(callback, server_id, user_state)
        except Exception as e:
            logger.error(f"Cancel selection mode error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error cancelling selection.", reply_markup=back_button(f"server_{server_id}"))

    # --- FILE ACTIONS ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_file_"))
    async def file_actions(callback: types.CallbackQuery):
        await callback.answer()
        try:
            _, server_id, file_name = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            is_zip = file_name.lower().endswith('.zip')
            kb = build_file_actions_keyboard(server_id, file_name, is_zip)
            text = f"📄 File: {html.escape(file_name)}\nPath: {html.escape(user_state['current_path'])}"
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
            _, server_id, file_name = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            current_path = user_state['current_path']
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            from main import get_ssh_session, get_server_by_id  # Delayed import
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            sftp = ssh.open_sftp()
            file_stat = sftp.stat(file_path)
            if file_stat.st_size > 50 * 1024 * 1024:
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
            await refresh_file_list(callback, server_id, user_state)
        except Exception as e:
            logger.error(f"Download file error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text(f"❌ Error downloading file: {html.escape(str(e))}", reply_markup=back_button(f"fm_refresh_{server_id}"))
        finally:
            if sftp:
                try:
                    sftp.close()
                except:
                    logger.warning(f"Failed to close SFTP session for server {server_id}")

    # --- DELETE FILE/BATCH DELETE ---
    @dp.callback_query_handler(lambda c: c.data.startswith(("fm_delete_", "fm_batch_delete_")))
    async def delete_file_confirm(callback: types.CallbackQuery):
        await callback.answer()
        try:
            action, server_id, file_name = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            files = [file_name] if action == 'fm_delete' else user_state.get('selected_files', set())
            if not files:
                await callback.message.edit_text("❌ No files selected.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            current_path = user_state['current_path']
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("✅ Yes, delete", callback_data=f"fm_delete_confirm_{server_id}_{len(files)}"),
                InlineKeyboardButton("⬅️ Back", callback_data=f"fm_refresh_{server_id}" if action == 'fm_delete' else f"fm_cancel_select_{server_id}")
            )
            text = f"⚠️ Are you sure you want to delete {len(files)} file(s) from {html.escape(current_path)}?"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
            user_state['pending_operation'] = {'type': 'delete', 'files': files}
        except Exception as e:
            logger.error(f"Delete confirm error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error initiating deletion.", reply_markup=back_button(f"server_{server_id}"))

    # --- DELETE CONFIRM ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_delete_confirm_"))
    async def delete_file(callback: types.CallbackQuery):
        await callback.answer()
        try:
            _, server_id, _ = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            pending_op = user_state.get('pending_operation', {})
            if pending_op.get('type') != 'delete':
                logger.error(f"Invalid pending operation for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid operation state.", reply_markup=back_button(f"server_{server_id}"))
                return
            files = pending_op.get('files', [])
            _, error = await handle_file_operation(server_id, user_state, 'delete', files)
            user_state['selected_files'] = set()
            user_state['mode'] = 'file_manager'
            user_state.pop('pending_operation', None)
            if error:
                await callback.message.edit_text(f"❌ Error deleting file(s): {html.escape(error)}", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            await refresh_file_list(callback, server_id, user_state)
            text = f"🗂 File Manager: {html.escape(user_state['current_path'])}\n✅ {len(files)} file(s) deleted."
            await callback.message.edit_text(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Delete error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text(f"❌ Error deleting file(s): {html.escape(str(e))}", reply_markup=back_button(f"fm_refresh_{server_id}"))

    # --- COPY/BATCH COPY START ---
    @dp.callback_query_handler(lambda c: c.data.startswith(("fm_copy_", "fm_batch_copy_")))
    async def copy_start(callback: types.CallbackQuery):
        await callback.answer()
        try:
            action, server_id, file_name = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            files = [file_name] if action == 'fm_copy' else user_state.get('selected_files', set())
            if not files:
                await callback.message.edit_text("❌ No files selected.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            user_state['mode'] = 'copy'
            user_state['pending_operation'] = {'type': 'copy', 'files': files}
            text = f"📋 Please send the destination path for copying {len(files)} file(s) (e.g., /home/ubuntu/destination)."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Copy start error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error initiating copy.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE COPY ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'copy')
    async def handle_copy(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await message.answer("❌ No active SSH session.")
                return
            pending_op = user_state.get('pending_operation', {})
            if pending_op.get('type') != 'copy':
                logger.error(f"Invalid pending operation for user {uid}")
                await message.answer("❌ Invalid operation state.")
                return
            dest_path = sanitize_path(message.text.strip())
            if not dest_path:
                await message.answer("❌ Invalid destination path.")
                return
            files = pending_op.get('files', [])
            _, error = await handle_file_operation(server_id, user_state, 'copy', files, dest_path)
            user_state['selected_files'] = set()
            user_state['mode'] = 'file_manager'
            user_state.pop('pending_operation', None)
            if error:
                await message.answer(f"❌ Error copying file(s): {html.escape(error)}")
                return
            await refresh_file_list(message, server_id, user_state)
            text = f"🗂 File Manager: {html.escape(user_state['current_path'])}\n✅ {len(files)} file(s) copied to '{html.escape(dest_path)}'."
            await message.answer(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Copy error for server {server_id}, user {uid}: {e}")
            await message.answer(f"❌ Error copying file(s): {html.escape(str(e))}")
        finally:
            user_state.pop('mode', None)

    # --- MOVE/BATCH MOVE START ---
    @dp.callback_query_handler(lambda c: c.data.startswith(("fm_move_", "fm_batch_move_")))
    async def move_start(callback: types.CallbackQuery):
        await callback.answer()
        try:
            action, server_id, file_name = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            files = [file_name] if action == 'fm_move' else user_state.get('selected_files', set())
            if not files:
                await callback.message.edit_text("❌ No files selected.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            user_state['mode'] = 'move'
            user_state['pending_operation'] = {'type': 'move', 'files': files}
            text = f"✂️ Please send the destination path for moving {len(files)} file(s) (e.g., /home/ubuntu/destination)."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Move start error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error initiating move.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE MOVE ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'move')
    async def handle_move(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await message.answer("❌ No active SSH session.")
                return
            pending_op = user_state.get('pending_operation', {})
            if pending_op.get('type') != 'move':
                logger.error(f"Invalid pending operation for user {uid}")
                await message.answer("❌ Invalid operation state.")
                return
            dest_path = sanitize_path(message.text.strip())
            if not dest_path:
                await message.answer("❌ Invalid destination path.")
                return
            files = pending_op.get('files', [])
            _, error = await handle_file_operation(server_id, user_state, 'move', files, dest_path)
            user_state['selected_files'] = set()
            user_state['mode'] = 'file_manager'
            user_state.pop('pending_operation', None)
            if error:
                await message.answer(f"❌ Error moving file(s): {html.escape(error)}")
                return
            await refresh_file_list(message, server_id, user_state)
            text = f"🗂 File Manager: {html.escape(user_state['current_path'])}\n✅ {len(files)} file(s) moved to '{html.escape(dest_path)}'."
            await message.answer(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Move error for server {server_id}, user {uid}: {e}")
            await message.answer(f"❌ Error moving file(s): {html.escape(str(e))}")
        finally:
            user_state.pop('mode', None)

    # --- ZIP START ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_zip_"))
    async def zip_files_start(callback: types.CallbackQuery):
        await callback.answer()
        try:
            _, server_id, _ = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'select_files':
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            if not user_state.get('selected_files', set()):
                await callback.message.edit_text("❌ No files selected.", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            user_state['mode'] = 'zip'
            user_state['pending_operation'] = {'type': 'zip', 'files': user_state.get('selected_files', set())}
            text = f"🗜 Creating zip file in {html.escape(user_state['current_path'])}. Please enter a name for the zip file (e.g., archive.zip)."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Zip start error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error initiating zip.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE ZIP ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'zip')
    async def handle_zip(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await message.answer("❌ No active SSH session.")
                return
            pending_op = user_state.get('pending_operation', {})
            if pending_op.get('type') != 'zip':
                logger.error(f"Invalid pending operation for user {uid}")
                await message.answer("❌ Invalid operation state.")
                return
            zip_name = re.sub(r'[;&|`\n\r$<>]', '', message.text.strip())
            if not zip_name:
                await message.answer("❌ Invalid zip file name.")
                return
            if not zip_name.endswith('.zip'):
                zip_name += '.zip'
            files = pending_op.get('files', [])
            _, error = await handle_file_operation(server_id, user_state, 'zip', files, new_name=zip_name)
            user_state['selected_files'] = set()
            user_state['mode'] = 'file_manager'
            user_state.pop('pending_operation', None)
            if error:
                await message.answer(f"❌ Error creating zip: {html.escape(error)}")
                return
            await refresh_file_list(message, server_id, user_state)
            text = f"🗂 File Manager: {html.escape(user_state['current_path'])}\n✅ Zip file '{html.escape(zip_name)}' created."
            await message.answer(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Zip error for server {server_id}, user {uid}: {e}")
            await message.answer(f"❌ Error creating zip: {html.escape(str(e))}")
        finally:
            user_state.pop('mode', None)

    # --- UNZIP ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_unzip_"))
    async def unzip_file(callback: types.CallbackQuery):
        await callback.answer()
        try:
            _, server_id, file_name = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            _, error = await handle_file_operation(server_id, user_state, 'unzip', [file_name])
            if error:
                await callback.message.edit_text(f"❌ Error unzipping file: {html.escape(error)}", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            await refresh_file_list(callback, server_id, user_state)
            text = f"🗂 File Manager: {html.escape(user_state['current_path'])}\n✅ File '{html.escape(file_name)}' unzipped."
            await callback.message.edit_text(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Unzip error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text(f"❌ Error unzipping file: {html.escape(str(e))}", reply_markup=back_button(f"fm_refresh_{server_id}"))

    # --- UPLOAD FILE START ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_upload_"))
    async def upload_file_start(callback: types.CallbackQuery):
        await callback.answer()
        try:
            _, server_id, _ = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state['mode'] = 'upload'
            text = f"📤 Uploading to {html.escape(user_state['current_path'])}. Please send a file."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Upload start error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error initiating upload.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE FILE UPLOAD ---
    @dp.message_handler(content_types=types.ContentType.DOCUMENT)
    async def handle_file_upload(message: types.Message):
        sftp = None
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            if user_state.get('mode') != 'upload':
                await message.answer("❌ Not in upload mode.")
                return
            server_id = user_state.get('server_id')
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await message.answer("❌ No active SSH session.")
                return
            if message.document.file_size > 50 * 1024 * 1024:
                await message.answer("❌ File too large to upload.")
                return
            file_name = re.sub(r'[;&|`\n\r$<>]', '', message.document.file_name)
            if not file_name:
                await message.answer("❌ Invalid file name.")
                return
            current_path = user_state['current_path']
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            from main import get_ssh_session, get_server_by_id  # Delayed import
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found")
                await message.answer("❌ Server not found.")
                return
            ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            sftp = ssh.open_sftp()
            await message.answer(f"📤 Uploading {html.escape(file_name)} to {html.escape(current_path)}...")
            file_data = BytesIO()
            await message.document.download(destination=file_data)
            with sftp.file(file_path, 'wb') as remote_file:
                file_data.seek(0)
                while True:
                    chunk = file_data.read(8192)
                    if not chunk:
                        break
                    remote_file.write(chunk)
            user_state['mode'] = 'file_manager'
            await refresh_file_list(message, server_id, user_state)
            text = f"🗂 File Manager: {html.escape(current_path)}\n✅ File '{html.escape(file_name)}' uploaded."
            await message.answer(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Upload error for server {server_id}, user {uid}: {e}")
            await message.answer(f"❌ Error uploading file: {html.escape(str(e))}")
        finally:
            if sftp:
                try:
                    sftp.close()
                except:
                    logger.warning(f"Failed to close SFTP session for server {server_id}")
            user_state.pop('mode', None)

    # --- CANCEL OPERATION ---
    @dp.callback_query_handler(lambda c: c.data == "fm_cancel")
    async def cancel_operation(callback: types.CallbackQuery):
        await callback.answer()
        try:
            user_state = user_input.get(callback.from_user.id, {})
            server_id = user_state.get('server_id')
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state['mode'] = 'file_manager'
            user_state['selected_files'] = set()
            user_state.pop('pending_operation', None)
            await refresh_file_list(callback, server_id, user_state)
            text = f"🗂 File Manager: {html.escape(user_state['current_path'])}\n✅ Operation cancelled."
            await callback.message.edit_text(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Cancel operation error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error cancelling operation.", reply_markup=back_button(f"server_{server_id}"))

    # --- NEW FOLDER START ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_new_folder_"))
    async def new_folder_start(callback: types.CallbackQuery):
        await callback.answer()
        try:
            _, server_id, _ = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
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
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await message.answer("❌ No active SSH session.")
                return
            folder_name = re.sub(r'[;&|`\n\r$<>]', '', message.text.strip())
            if not folder_name:
                await message.answer("❌ Invalid folder name.")
                return
            _, error = await handle_file_operation(server_id, user_state, 'mkdir', [folder_name])
            user_state['mode'] = 'file_manager'
            if error:
                await message.answer(f"❌ Error creating folder: {html.escape(error)}")
                return
            await refresh_file_list(message, server_id, user_state)
            text = f"🗂 File Manager: {html.escape(user_state['current_path'])}\n✅ Folder '{html.escape(folder_name)}' created."
            await message.answer(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"New folder error for server {server_id}, user {uid}: {e}")
            await message.answer(f"❌ Error creating folder: {html.escape(str(e))}")
        finally:
            user_state.pop('mode', None)

    # --- RENAME FILE START ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_rename_"))
    async def rename_file_start(callback: types.CallbackQuery):
        await callback.answer()
        try:
            _, server_id, file_name = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id:
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state['mode'] = 'rename'
            user_state['pending_operation'] = {'type': 'rename', 'files': [file_name]}
            text = f"✏️ Renaming '{html.escape(file_name)}' in {html.escape(user_state['current_path'])}. Please send a new name."
            await bot.send_message(callback.from_user.id, text, reply_markup=cancel_button())
        except Exception as e:
            logger.error(f"Rename start error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text("❌ Error initiating rename.", reply_markup=back_button(f"server_{server_id}"))

    # --- HANDLE RENAME ---
    @dp.message_handler(lambda m: user_input.get(m.from_user.id, {}).get('mode') == 'rename')
    async def handle_rename(message: types.Message):
        try:
            uid = message.from_user.id
            user_state = user_input.get(uid, {})
            server_id = user_state.get('server_id')
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await message.answer("❌ No active SSH session.")
                return
            pending_op = user_state.get('pending_operation', {})
            if pending_op.get('type') != 'rename':
                logger.error(f"Invalid pending operation for user {uid}")
                await message.answer("❌ Invalid operation state.")
                return
            new_name = re.sub(r'[;&|`\n\r$<>]', '', message.text.strip())
            if not new_name:
                await message.answer("❌ Invalid file name.")
                return
            files = pending_op.get('files', [])
            _, error = await handle_file_operation(server_id, user_state, 'rename', files, new_name=new_name)
            user_state['mode'] = 'file_manager'
            user_state.pop('pending_operation', None)
            if error:
                await message.answer(f"❌ Error renaming file: {html.escape(error)}")
                return
            await refresh_file_list(message, server_id, user_state)
            text = f"🗂 File Manager: {html.escape(user_state['current_path'])}\n✅ File '{html.escape(files[0])}' renamed to '{html.escape(new_name)}'."
            await message.answer(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Rename error for server {server_id}, user {uid}: {e}")
            await message.answer(f"❌ Error renaming file: {html.escape(str(e))}")
        finally:
            user_state.pop('mode', None)

    # --- VIEW FILE CONTENT ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_view_"))
    async def view_file(callback: types.CallbackQuery):
        await callback.answer()
        sftp = None
        try:
            _, server_id, file_name = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            current_path = user_state['current_path']
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            from main import get_ssh_session, get_server_by_id  # Delayed import
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            sftp = ssh.open_sftp()
            file_stat = sftp.stat(file_path)
            if file_stat.st_size > 1024 * 1024:
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
                    logger.warning(f"Failed to close SFTP session for server {server_id}")

    # --- FILE DETAILS ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_details_"))
    async def file_details(callback: types.CallbackQuery):
        await callback.answer()
        try:
            _, server_id, file_name = parse_callback_data(callback.data)
            if server_id not in active_sessions:
                logger.error(f"No active SSH session for server {server_id}")
                await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                return
            user_state = user_input.get(callback.from_user.id, {})
            if user_state.get('server_id') != server_id or user_state.get('mode') != 'file_manager':
                logger.error(f"Invalid file manager state for user {callback.from_user.id}")
                await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"server_{server_id}"))
                return
            current_path = user_state['current_path']
            file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
            from main import get_ssh_session, get_server_by_id  # Delayed import
            server = await get_server_by_id(server_id)
            if not server:
                logger.error(f"Server {server_id} not found")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button(f"server_{server_id}"))
                return
            ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            command = f'ls -l "{file_path}"'
            stdout_data, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                await callback.message.edit_text(f"❌ Error: {html.escape(stderr_data)}", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            file_info = parse_ls_output(stdout_data)[0]
            text = f"📄 File Details: {html.escape(file_name)}\n"
            text += f"Path: {html.escape(current_path)}\n"
            text += f"Size: {format_size(file_info['size'])}\n"
            text += f"Modified: {html.escape(file_info['mtime'])}\n"
            text += f"Permissions: {html.escape(file_info['perms'])}\n"
            text += f"Owner: {html.escape(file_info['owner'])}\n"
            text += f"Group: {html.escape(file_info['group'])}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_button(f"fm_refresh_{server_id}"))
        except Exception as e:
            logger.error(f"File details error for server {server_id}, user {callback.from_user.id}: {e}")
            await callback.message.edit_text(f"❌ Error fetching details: {html.escape(str(e))}", reply_markup=back_button(f"fm_refresh_{server_id}"))
