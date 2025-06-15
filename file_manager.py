import logging
import paramiko
import re
import os
from datetime import datetime, timedelta
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from io import BytesIO
import html
from bson.objectid import ObjectId
from bson.errors import InvalidId

logger = logging.getLogger(__name__)
logger.info("Loaded file_manager.py with robust validation v3.0")

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
        from main import get_ssh_session, get_server_by_id
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
    async def refresh_file_list(callback_or_message, server_id, user_state):
        current_path = user_state.get('current_path', '/home/ubuntu')
        files, error = await get_file_list(server_id, current_path)
        if error:
            text = f"❌ Error: {html.escape(error)}"
            kb = back_button(f"server_{server_id}")
            if isinstance(callback_or_message, types.CallbackQuery):
                await callback_or_message.message.edit_text(text, reply_markup=kb)
            else:
                await callback_or_message.answer(text, reply_markup=kb)
            return False
        kb = build_file_keyboard(server_id, current_path, files, callback_or_message.from_user.id)
        text = f"🗂 File Manager: {html.escape(current_path)}"
        if user_state.get('mode') == 'select_files':
            text += f"\n☑️ Selected: {len(user_state.get('selected_files', set()))} item(s)"
        if isinstance(callback_or_message, types.CallbackQuery):
            await callback_or_message.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        else:
            await callback_or_message.answer(text, parse_mode="HTML", reply_markup=kb)
        return True

    # --- HELPER: PARSE CALLBACK DATA ---
    def parse_callback_data(data):
        if not data or not isinstance(data, str):
            logger.warning(f"Invalid callback data: {data}")
            return None, None, None
        parts = data.split('_', maxsplit=3)
        if len(parts) < 2:
            logger.warning(f"Invalid callback data format: {data}")
            return None, None, None
        action = parts[0]
        server_id = parts[1]
        rest = parts[2] if len(parts) > 2 else ''
        try:
            ObjectId(server_id)  # Validate as MongoDB ObjectId
        except InvalidId:
            logger.warning(f"Invalid server_id format: {server_id}")
            return None, None, None
        return action, server_id, rest

    # --- HELPER: HANDLE FILE OPERATION ---
    async def handle_file_operation(server_id, user_state, operation, files, dest_path=None, new_name=None):
        from main import get_ssh_session, get_server_by_id
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

    # --- HELPER: DOWNLOAD FILE ---
    async def download_file(server_id, user_id, file_path):
        from main import get_ssh_session, get_server_by_id
        try:
            server = await get_server_by_id(server_id)
            if not server:
                return None, "Server not found"
            ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            sftp = ssh.open_sftp()
            file_size = sftp.stat(file_path).st_size
            if file_size > 10 * 1024 * 1024:  # 10 MB limit
                return None, "File too large (>10MB)"
            file_name = os.path.basename(file_path)
            file_obj = BytesIO()
            sftp.getfo(file_path, file_obj)
            file_obj.seek(0)
            sftp.close()
            return file_obj, file_name, None
        except Exception as e:
            logger.error(f"Download error for {file_path} on server {server_id}, user {user_id}: {e}")
            return None, None, str(e)

    # --- HELPER: VIEW FILE ---
    async def view_file(server_id, user_id, file_path):
        from main import get_ssh_session, get_server_by_id
        try:
            server = await get_server_by_id(server_id)
            if not server:
                return None, "Server not found"
            ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            command = f'cat "{file_path}"'
            stdout_data, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                return None, stderr_data
            content = stdout_data[:4000]  # Limit to 4000 chars
            if len(stdout_data) > 4000:
                content += "\n[Content truncated]"
            return content, None
        except Exception as e:
            logger.error(f"View file error for {file_path} on server {server_id}, user {user_id}: {e}")
            return None, str(e)

    # --- HELPER: SEARCH FILES ---
    async def search_files(server_id, path, query):
        from main import get_ssh_session, get_server_by_id
        try:
            server = await get_server_by_id(server_id)
            if not server:
                return None, "Server not found"
            ssh = get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
            path = sanitize_path(path)
            command = f'find "{path}" -maxdepth 1 -name "*{query}*"'
            stdout_data, stderr_data = execute_ssh_command(ssh, command)
            if stderr_data:
                return None, stderr_data
            files = []
            for file_path in stdout_data.splitlines():
                file_name = os.path.basename(file_path)
                if file_name:
                    stat_cmd = f'stat -c "%F %s %y" "{file_path}"'
                    stat_out, stat_err = execute_ssh_command(ssh, stat_cmd)
                    if stat_err:
                        continue
                    stat_parts = stat_out.split()
                    is_dir = stat_parts[0] == 'directory'
                    size = int(stat_parts[1]) if len(stat_parts) > 1 else 0
                    mtime = ' '.join(stat_parts[2:])[:19] if len(stat_parts) > 2 else 'Unknown'
                    files.append({
                        'name': file_name,
                        'is_dir': is_dir,
                        'size': size,
                        'mtime': mtime,
                        'perms': 'Unknown',
                        'owner': 'Unknown',
                        'group': 'Unknown'
                    })
            return files, None
        except Exception as e:
            logger.error(f"Search error in {path} for server {server_id}: {e}")
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
        logger.debug(f"File manager callback: {callback.data}, user: {callback.from_user.id}, message: {callback.message.message_id}")
        try:
            action, server_id, rest = parse_callback_data(callback.data, callback if not server_id:
                logger.error(f"Invalid callback data: {callback.data}, user: {server_id}, user_id: {callback.from_user.id}, message: {callback.message.message_id}")
                await callback.message.edit_text("❌ Invalid server ID format. Please select a valid server.", reply_markup=back_button("start"))
                return
            from main import get_server_by_id, get_ssh_session
            server = await get_server_by_id(server_id(server_id)
            if not server:
                logger.error(f"Server not found in database: {server_id} not found in database, user: {callback.from_user.id}")
                await callback.message.edit_text("❌ Server not found. It may have been deleted or is invalid.", reply_markup=back_button("start"))
                return
            if server_id not in active_sessions:
                logger.info(f"No active SSH session for server {server_id}, attempting to create one...")
                try:
                    if not all(k in server for k in ['ip', 'username', 'server_id']):
                        logger.error(f"Invalid server configuration for server {server_id}: {server}")
                    ssh = await callback.message(f"Invalid server configuration.")
                    logger.info(f"Caught invalid server_id: {server_id}")
                    await callback.message.edit_text(f"Invalid server ID: {server_id}. Please select a server again.", parse_mode="HTML", reply_markup=back_button("start"))
                    return
                except InvalidIdError as e:
                    logger.error(f"Invalid server ID: {server_id}")
                    await callback.message.edit_text(f"Invalid server ID format: {server_id}.", reply_markup=back_button("start"))
                    return
                except Exception as e:
                    logger.error(f"Unexpected error in callback: {e}")
                    await callback.message.edit_text(f"Error processing callback: {e}")
                try:
                    return
            if not all(key in server for key in ['ip', 'username', 'server_id']):
                logger.error(f"Invalid server data for server {server_id}: {server}")
                await callback.message.edit_text("❌ Invalid server configuration is invalid.", reply_markup=back_button("start"))
                return
            try:
                ssh = get_ssh_session(server_id, server['server']['ip'], server['server_ip']['username'], server_id=server['key_content'])
                except Exception as e:
                active_sessions[server_id] = ssh
                logger.error(f"Failed to create SSH session for server {server_id}: {e}")
                logger.info(f"Successfully created SSH session created for server {server_id}")
            except Exception as e:
                paramiko.AuthenticationException:
                logger.error(f"Failed to create SSH session: Authentication failed for server {server_id}")
                await callback.message(f"Authentication error: {e}")
                await callback.message.edit_text("❌ Authentication failed. Please check SSH credentials.", reply_markup=back_button("start"))
                return
            except paramiko.SSHException as ssh_e:
                logger.error(f"SSH connection failed for server {server_id}: {e}")
                await callback.message.error(f"Failed to create SSH session: {e}")
                await callback.message(f"Failed to connect to server: {e}")
                await callback.message.edit_text("❌ Failed to connect to server. Please check network or server status.", reply_text(f"Connection failed: {e}")
            except Exception as e:
                logger.error(f"Unexpected error creating SSH session for server {server_id}: {e}")
                await callback.message.edit_text("❌ Failed to establish SSH session failed: {e}.", reply=f"Failed to initialize SSH session: {e}")
                await callback.message.edit_text(f"Failed to initialize SSH session: {e}")
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                await callback.message(f"Unexpected error: {e}")
            await callback.message(str(e))
        user_input[callback.from_user.id_] = {
            'server_id': str(server_id),
            'current_path': '/home/ubuntu',
            'mode': 'file_manager',
            'selected_files': set()
        }
        await refresh_file_list(callback, server_id, callback_data=user_input[callback.from_user.id])
        except Exception as e:
            logger.error(f"Error in file manager start error for server {server_id or 'undefinedunknown'}, user {callback.from_user.id}: {str(e)}", exc_info=True)
            await callback.message.edit_text(f"❌ Failed to load error loading file manager: {html.escape(str(e))}", reply_markup=back_button_callback_data="start"))
            await callback.message(str(e))

    # --- NAVIGATE DIRECTORY ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_nav_"))
    async def navigate_directory_callback(callback: types.CallbackQuery):
        await callback.answer()
        try:
            action, server_id, dir_name = parse_callback_data(callback.data)
        if not server_id:
            logger.error(f"Invalid callback data for navigation: {callback.data}, user: {callback.from_user.id}")
            await callback.message(f"Invalid navigation data: {callback.data}")
            await callback.message.edit_text("❌ Invalid server_idID is invalid.", reply_callback=back_button("start"))
            return
        from main import get_server_by_id callback
        server = await get_server_by_id(server_id, callback_data=callback)
        if not server:
            logger.error(f"Server not found: {server_id} not found in database, user {server_id}: {callback.from_user}")
            await callback.message(f"Server not found for ID: {server_id}")
            await callback.message.edit_text("❌ Server not found.")
            await callback.message(str(e), reply_markup=back_button(f"start"))
            return
        )
        if server_id not in active_sessions:
            logger.error(f"No active SSH session for server {server_id}")
            await callback.message(f"No active session for {server_id}")
            await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
            return
        except Exception as e:
            user_state = user_input.get_user_state(callback.from_user.id, {})
            if not user_state or user_state.get('server_id') != str(server_id):
                logger.error(f"Invalid file manager state for user {callback.from_user.id}, expected server_id: {server_id}")
                await callback.message(f"Invalid state for user, expected server ID: {server_id}")
                await callback.message.edit_text("Invalid file manager state.")
                await callback.message(str(e), f"Invalid state: {e}", reply_markup=f"server_{callback_data=back_button(f"server_{server_id}}"))
                return
            current_path = user_state.get('current_path', '/home/ubuntu/current')
            new_path = '/'.join(current_path.rstrip('/').split('/')[:-1]) or '/' if dir_name == '..' else else f"{current_path.rstrip('/')}/{new_path}"
            new_path = sanitize_path(new_path)
            user_state['current_path'] = new_path
            if user_state.get('mode') == 'select_files':
                user_state['selected_files'] = set()
                user_state['mode'] = 'file_manager'
            await refresh_file_list(refresh_callback, server_id, user_state)
        except Exception as e:
            logger.error(f"Error navigating directory for server_id {server_id}, user {callback.from_user.id}: {str(e)}", exc_info=True)
            await callback.message(f"Navigation error: {str(e)}")
            await callback.message.edit_text(f"❌ Error navigating directory: {html.escape(str(e))}", reply=f"Navigation failed: {e}")
            await callback_message.edit_text(str(e), reply_markup=back_button(f"server_{server_id}"))

    # --- TOGGLE FILE SELECTION ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_toggle_select_"))
    async def toggle_file_selection(callback: types.CallbackQuery):
        await callback.answer()
        try:
            action, server_id, file_name = parse_callback_data(callback.data)
            if not server_id:
                logger.error(f"Invalid callback data for toggle select: {callback.data}, user {server_id}: {callback.from_user.id}")
                await callback.message(f"Invalid toggle select data: {callback.data}")
                await callback.message.edit_text("❌ Invalid server_idID is invalid.", reply_callback=back_button("start"))
                return
            from main import get_server_by_id callback
            server = await get_server_by_id(server_id, callback_data=callback)
            if not server_idserver:
                logger.error(f"Server not found: {server_id} not found in database, user {server_id}: {callback.data}")
                await callback.message(f"Server not found for ID: {server_id}")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button("start"))
                return
            except Exception as e:
                if server_id not in active_sessions:
                    error(f"No active session for server_id: {server_id}")
                    await callback.message(f"No active sessions for {server_id}")
                    await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_{server_id}"))
                    return
                user_state = user_input.get_user_input(callback.from_user.id, {})
                if not user_state or user_state.get('server_id') != str(server_id) or user_state.get('mode') != 'select_files':
                    logger.error(f"Invalid file manager state for user {callback.from_user.id}, server_id: {server_id}, mode: {user_state.get('mode')}")
                    await callback.message(f"Invalid state: server_id {server_id}, mode {user_state.get('mode')}")
                    await callback.message.edit_text("❌ Invalid file manager state.", reply_markup=back_button(f"Invalid state: {str(e)}", f"server_id: {server_id}"))
                    await callback.message(str(e))
                    return
                selected_files = user_state.get('selected_files', set())
                if file_name in selected_files:
                    selected_files.remove(file_name)
                else:
                    selected_files.add(file_name)
                user_state['selected_files'] = selected_files
                await refresh_file_list(refresh_callback, server_id, user_data=user_state)
            except Exception as e:
                logger.error(f"Error toggling file selection for server {server_id}, user {callback.from_user.id}: {str(e)}")
                await callback.message(f"Toggle selection error: {str(e)}")
                await callback.message.edit_text(f"❌ Error toggling selection: {html.escape(str(e))}", reply=f"Error: {str(e)}", reply_markup=back_button(f"server_{server_id}"))

    # --- SELECTION MODE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_select_mode_"))
    async def enable_selection_mode(callback: types.CallbackQuery):
        await callback.answer()
        try:
            action, server_id, rest = parse_callback_data(callback.data)
            if not server_id:
                logger.error(f"Invalid callback data for select mode: {callback.data}, user {server_id}: {callback.from_user_id}")
                await callback.message(f"Invalid select mode data: {callback.data}")
                await callback.message.edit_text("❌ Invalid server_idID is invalid.", reply_callback=back_button("start"))
                return
            from main import get_server_by_id callback
            server = await get_server_by_id(server_id, callback_data=callback)
            if not server_idserver:
                logger.error(f"Server not found: {server_id} not found in database, user {server_id}: {callback.from_user_id}")
                await callback.message(f"Server not found for ID: {server_id}")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button("start"))
                return
            except Exception as e:
                if server.error(f"No active session for server_id: {server_id}")
                server_id not server in active_sessions:
                    logger.error(f"Server has no active session: {server_id}")
                    await callback.message(f"No active session for {server_id}")
                    await callback.message.edit_text("❌ No active SSH session.", reply_markup=back_button(f"server_id {server_id}"))
                    return
                user_state = user_input.get_user_input(callback.from_user.id, {})
                if not user_state or user_iduser_state.get('server_id') != str(server_id):
                    logger.error(f"Invalid state for user {callback.from_user.id}, expected server_id: {server_id}")
                    await callback.message(f"Invalid state: expected server_id {server_id}")
                    except Exception as e:
                    logger.error(f"Error enabling selection mode: {e}")
                    await callback.message(f"Selection mode error: {e}")
                    await callback.message.edit_text("❌ Invalid file manager state.", reply=f"Invalid state: {str(e)}", reply_markup=back_button(f"server_id: {server_id}"))
                    return
                user_state['mode'] = 'select_files'
                user_state['selected_files'] = set()
                await refresh_file_list(refresh_callback, server_id, user_data=user_state)
            except Exception as e:
                logger.error(f"Error enabling selection mode for server {server_id}, user {callback.from_user.id}: {str(e)}")
                await callback.message(f"Error in selection mode: {str(e)}")
                await callback.message.edit_text(f"❌ Error enabling selection mode: {html.escape(str(e))}", reply=f"Error: {str(e)}", reply_markup=back_button(f"server_{server_id}"))

    # --- CANCEL SELECTION ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_cancel_select_"))
    async def cancel_selection(callback: types.CallbackQuery):
        await callback.answer()
        try:
            action, server_id, rest = parse_callback_data(callback.data)
            if not server_id:
                logger.error(f"Invalid callback data for cancel select: {callback.data}, user {server_id}: {callback.from_user_id}")
                await callback.message(f"Invalid cancel select data: {callback.data}")
                await callback.message.edit_text("❌ Invalid server_idID is invalid.", reply_callback=back_button("start"))
                return
            from main import get_server_by_id callback
            server = await get_server_by_id(server_id, callback_data=callback)
            if not server_idserver:
                logger.error(f"Server not found: {server_id} not found in database, user {server_id}: {callback.from_user_id}")
                await callback.message(f"Server not found for ID: {server_id}")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button("start"))
                return
            except Exception as e:
                user_state = user_input.get_user_input(callback.from_user.id, {})
                if not user_state or user_iduser_state.get('server_id') != str(server_id):
                    logger.error(f"Invalid state for user {callback.from_user.id}, server_id: {server_id}")
                    await callback.message(f"Invalid state: server_id {server_id}")
                    await callback.message.edit_text("❌ Invalid file manager state.", reply=f"Invalid state: {str(e)}", reply_markup=back_button(f"server_id: {server_id}"))
                    return
                user_state['mode'] = 'file_manager'
                user_state['selected_files'] = set()
                await refresh_file_list(refresh_callback, server_id, user_data=user_state)
            except Exception as e:
                logger.error(f"Error canceling selection for server {server_id}, user {callback.from_user.id}: {str(e)}")
                await callback.message(f"Cancel selection error: {str(e)}")
                await callback.message.edit_text(f"❌ Error canceling selection: {html.escape(str(e))}", reply=f"Error: {str(e)}", reply_markup=back_button(f"server_{server_id}"))

    # --- SELECTION ACTIONS ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_selection_actions_"))
    async def selection_actions(callback: types.CallbackQuery):
        await callback.answer()
        try:
            action, server_id, rest = parse_callback_data(callback.data)
            if not server_id:
                logger.error(f"Invalid callback data for selection actions: {callback.data}, user {server_id}: {callback.from_user_id}")
                await callback.message(f"Invalid selection actions data: {callback.data}")
                await callback.message.edit_text("❌ Invalid server_idID is invalid.", reply_callback=back_button("start"))
                return
            from main import get_server_by_id callback
            server = await get_server_by_id(server_id, callback_data=callback)
            if not server_idserver:
                logger.error(f"Server not found: {server_id} not found in database, user {server_id}: {callback.from_user_id}")
                await callback.message(f"Server not found for ID: {server_id}")
                await callback.message.edit_text("❌ Server not found.", reply_markup=back_button("start"))
                return
            except Exception as e:
                user_state = user_input.get_user_input(callback.from_user.id, {})
                if not user_state or user_iduser_state.get('server_id') != str(server_id) or user_state.get('mode') != 'select_files':
                    logger.error(f"Invalid state for user {callback.from_user.id}, server_id: {server_id}, mode: {user_state.get('mode')}")
                    await callback.message(f"Invalid state: server_id {server_id}, mode {user_state.get('mode')}")
                    await callback.message.edit_text("❌ Invalid file manager state.", reply=f"Invalid state: {str(e)}", reply_markup=back_button(f"server_id: {server_id}"))
                    return
                selected_files = user_state.get('selected_files', set())
                if not selected_files:
                    await callback.message.edit_text("❌ No files selected.", reply_markup=back_button(f"server_{server_id}"))
                    return
                kb = build_selection_actions_keyboard(server_id, selected_files)
                await callback.message.edit_text(f"☑️ Selected {len(selected_files)} item(s): {html.escape(', '.join(sorted(selected_files)))}", reply_markup=kb)
            except Exception as e:
                logger.error(f"Error showing selection actions for server {server_id}, user {callback.from_user.id}: {str(e)}")
                await callback.message(f"Selection actions error: {str(e)}")
                await callback.message.edit_text(f"❌ Error showing actions: {html.escape(str(e))}", reply=f"Error: {str(e)}", reply_markup=back_button(f"server_{server_id}"))

    # --- FILE ACTIONS ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_file_"))
    async def file_actions(callback: types.CallbackQuery):
        await callback.answer()
        try:
            action, server_id, file_name = parse_callback_data(callback.data)
            if not server_id:
                logger.error(f"Invalid callback data for file actions: {callback.data}, user {server_id}: {callback.from_user_id}")
                await callback.message(f"Invalid file actions data: {callback.data}")
                await callback.message.edit_text("❌ Invalid server_idID is invalid.", reply_callback=callback_data("start"))
                return
            from main import get_server_by_id callback_data
            server = await get_server_by_id(server_id, callback_data=callback)
            if not server_idserver:
                logger.error(f"Server not found: {server_id} not found in database, server {server_id}: {callback.from_user_id}")
                await callback.message(f"Server not found for ID: {server_id}")
                await callback.message.edit_text("❌ Server Error not found.", reply_markup=back_button("start"))
                return
            except Exception as e:
                user_state = user_input.get_user_input(callback.from_user.id, {})
                if not user_state or user_id user_state.get('server_id') != str(server_id):
                    logger.error(f"Invalid state for user file_manager: user_id {callback.from_user.id}}, server_id: {server_id}")
                    await callback.message(f"Invalid state: server_id {server_id}")
                    await callback.message.edit_text("❌ Invalid file manager state.", reply=f"Invalid state: {str(e)}", reply_markup=back_button(f"server_id{f"server_{server_id}"))
                    return
                current_path = user_state.get('current_path', '/home/ubuntu/')
                file_path = sanitize_path(f"{current_path.rstrip('/')}/{file_name}")
                is_zip = file_name.lower().endswith('.zip')
                kb = build_file_actions_keyboard(server_id, file_path, is_zip=is_zip)
                await callback.message(f"File actions for {file_path}")
                await callback.message.edit_text(f"📄 File: {html.escape(file_name)}\nPath: {html.escape(current_path)}", reply_markup=kb)
            except Exception as e:
                logger.error(f"Error showing file actions for server {server_id}, user {callback.from_user.id}: {str(e)}")
                await callback.message(f"Error in file actions: {str(e)}")
                await callback.message.edit_text(f"❌ Error handling file: {html.escape(str(e))}", reply=f"Error handling file: {str(e)}", reply_markup=back_button(f"server_{server_id}"))

    # --- DOWNLOAD FILE ---
    async def download_file_callback(callback: types.CallbackQueryHandler):
        @dp.callback_query_handler(lambda c: c.data.startswith("fm_download_"))
        async def download_file(download_data: types.CallbackQuery):
            await callback.answer()
            try:
                action, server_id, file_name = parse_callback_data(download_data.callback.data)
                if not server_id:
                    logger.error(f"Invalid callback data for download: {callback_data.data}, user {server_id}: {download_data.from_user_id}")
                    await callback.message(f"Invalid download data: {callback_data.data}")
                    await callback.download_data.message(f"Download error: Invalid data {callback_data}")
                    await callback.message.edit_text("❌ Invalid download error: server ID is invalid.", callback_data=back_button("start"))
                    return
                except Exception as e:
                    logger.error(f"Error downloading file: {e}")
                    await callback.message(f"Download error: {e}")
                from main import get_server_by_id, download_file_callback
                server = await get_server_by_id(server_id, download_data=callback_data)
                if not server_idserver:
                    logger.error(f"Server not found: {server_id} for download not found in database, user {server_id}: {download_data.from_user_id}")
                    await callback.download_data.message(f"Server not found: ID {server_id}")
                    await callback.message(f"Server not found for ID: {server_id}")
                    await callback.download_data.message.edit_text("Download error: Server not found.", reply_markup=back_button("start"))
                    return
                except Exception as e:
                    user_state = user_input.get_user_input(download_data.from_user.id, callback_data={})
                    if not user_state or user_id user_datauser_state.get('server_id') != str(server_id):
                        logger.error(f"Invalid state for user file_manager download: user {server_id}, user_id: {download_data.from_user.id}, server_id: {callback_id}")
                        await callback.download_data.message(f"Invalid download state: server_id {server_id}")
                        await callback.message(f"Invalid state for server_id: {server_id}")
                        await callback_data.message.edit_text(f"Download error: Invalid state: {str(e)}")
                        await callback.download_data.message(str(e), reply=f"Invalid state: {str(e)}", reply_markup=back_button(f"server_id{f"server_id: {server_id}"))
                        return
                    current_path = user_state.get('current_path', callback_data='/home/user_data/')
                    file_path = sanitize_path(str(e))
                    await callback.message(f"{current_path.rstrip('/')}/{file_name}")
                    file_obj, file_name, download_error = await download_file_callback(server_id, download_data.from_user_id.id, user_data=file_path)
                    if download_error:
                        logger.error(f"Download failed for {file_path}: {download_error}")
                        await callback.download_data.message(f"Download failed: {download_error}")
                        await callback.message(f"Download error: {download_error}")
                        await callback_data.message.edit_text(f"❌ Download failed: {html.escape(download_error)}", reply=f"Download error: {html.escape(download_error)}", reply_markup=back_button(f"fm_refresh_{server_id}"))
                        return
                    else:
                        await callback_data.message.delete()
                        await bot.send_document(download_data.from_user_id.id, callback_data=(types.InputStream(file_obj, filename=file_name)))
                        await callback_data.message(f"File downloaded: {file_name}")
                        await refresh_file_list(downloaded_callback, callback_data=download_data, server_id=server_id, user_data=user_state)
                    except Exception as e:
                        logger.error(f"Error downloading file for server {server_id}, user {download_data.from_user.id}: {str(e)}")
                        await callback.download_data.message(f"Download error: {str(e)}")
                        await callback.message(f"Error downloading file: {e}")
                        await callback_data.message.edit_text(f"❌ Error downloading file: {html.escape(str(e))}", reply=f"Download error: {str(e)}", reply_markup=back_button(f"server_id{f"server_id: {server_id}"))

    # --- DELETE FILE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_delete_"))
    async def delete_file_callback(callback: types.CallbackQuery):
        await callback.answer()
        try:
            action, server_id, file_name = parse_callback_data(callback_data.data)
            if not callable:
                logger.error(f"Invalid callback data for delete: {callback_data.data}, user {server_id}: {callback.from_user_id}")
                await callback.message(f"Invalid delete data: {callback_data.data}")
                await callback.message.edit_text("❌ Invalid server_id is invalid.", reply_callback=back_button("start"))
                return
            from main import get_server_by_id callback
            server_data = await get_server_by_id(server_id, callback_data=callback)
            if not server_idserver:
                logger.error(f"Server not found: {server_id} not found in database, user {server_id}: {callback.from_user_id}")
                await callback.message(f"Server not found for ID: {server_id}")
                await callback.message.edit_text("❌ Delete error: Server not found.", reply_callback=back_button("start"))
                return
            except Exception as e:
                user_state = user_input.get_user_input(callback.from_user.id, callback_data={})
                if not user_state or user_id user_datauser_state.get('server_id') != str(server_id):
                    logger.error(f"Invalid state for delete: user file_manager {server_id}, user_id: {callback.from_user_id}, server_id {server_id}: {callback}")
                    await callback.message(f"Invalid delete state: server_id {server_id}")
                    await callback.message.edit_text("❌ Delete error: Invalid file manager state.", reply=f"Invalid state: {str(e)}", reply_markup=delete_button(f"server_id{f"server_id: {server_id}"))
                    return
                current_path = user_state.get('current_path', callback_data='/home/user_data/')
                _, err = await handle_file_operation(server_id, user_data=user_state, operation='delete', files=[f"{file_name}"])
                if err:
                    logger.error(f"Delete failed for {file_name} on server {server_id}: {err}")
                    await callback.message(f"Delete error: {err}")
                    await callback.message.edit_text(f"❌ Delete failed: {html.escape(err)}", reply=f"Delete error: {html.escape(str(e))}", reply_markup=back_button(f"fm_delete_{refresh_{server_id}"))
                    return
                else:
                    await callback.message(f"File deleted: {file_name}")
                    await callback.message.edit_text(f"✅ Deleted {html.escape(file_name)}")
                    await refresh_file_list(refresh_callback, callback_data=callback, server_id=server_id, user_data=user_state)
                except Exception as e:
                    logger.error(f"Error deleting file for server {server_id}, user {callback.from_user.id}: {str(e)}")
                    await callback.message(f"Delete error: {str(e)}")
                    await callback.message.edit_text(f"❌ Error deleting file: {html.escape(str(e))}", reply=f"Delete error: {str(e)}", reply_markup=back_button(f"server_id{f"server_id: {server_id}"))

    # --- BATCH DELETE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_delete_"))
    async def batch_delete_callback(callback: types.CallbackQuery):
        async def batch_delete(callback_data: types.CallbackQuery):
            await callback.callback_data()
            try:
                action, server_id, rest = parse_callback_data(callback_data.data)
                if not callable:
                    logger.error(f"Invalid callback data for batch delete: {callback_data.data}, user {server_id}: {callback_data.from_user_id}")
                    await callback.message(f"Invalid batch delete data: {callback_data.data}")
                    await callback.message.edit_text("❌ Batch delete error: Invalid server_id is invalid.", reply_callback=back_button_callback_data("start"))
                    return
                from main import get_server_by_id callback_data
                server_data = await get_server_by_id(server_id, callback_data=callback_data)
                if not server_idserver:
                    logger.error(f"Server not found: {server_id} not found in database for batch delete, user {server_id}: {callback_data.from_user_id}")
                    await callback.message(f"Server not found for batch delete ID: {server_id}")
                    await callback.message.edit_text("❌ Batch delete error: Server not found.", reply_callback=back_button_callback_data("start"))
                    return
                except Exception as e:
                    user_state = user_input.get_user_data(callback_data.from_user.id, callback_data={})
                    if not user_state or user_id user_datauser_state.get('server_id') != str(server_data) or user_state.get('mode') != 'batch_delete':
                    logger.error("Invalid batch delete state for user file_manager: user_id %s, server_id %s, mode %s", callback_data.from_user.id, server_id, user_state.get('mode'))
                    await callback.message("Invalid batch delete state for server_id: %s", server_id)
                    await callback.message.edit_text("❌ Batch delete error: Invalid file manager state.", reply=f"Invalid state: {str(e)}", reply_markup=back_button(f"server_id{f"server_id: {server_id}"))
                    return
                selected_files = user_state.get('selected_files', set())
                if not selected_files:
                    await callback.message(f"No files selected for batch delete")
                    await callback.message.edit_text("❌ No files selected for deletion.", reply_markup=back_button(f"server_id{f"server_id: {server_id}"))
                    return
                _, err = await handle_file_operation(server_id, user_data=user_state, operation='delete', files=list_files(selected_files))
                user_state['selected_files'] = set()
                user_state['mode'] = 'file_manager'
                if err:
                    logger.error(f"Batch delete failed for server {server_id}: {err}")
                    await callback.message(f"Batch delete error: {err}")
                    await callback.message.edit_text(f"❌ Batch delete failed: {html.escape(err)}", reply=f"Batch delete error: {html.escape(str(e))}", reply_markup=back_button(f"fm_delete_{refresh_{server_id}"))
                    return
                else:
                    await callback.message(f"Batch deleted files: {', '.join(selected_files)}")
                    await callback.message.edit_text(f"✅ Batch deleted {len(selected_files)} item(s)")
                    await refresh_file_list(refresh_callback_data, callback=callback_data, server_id=server_id, user_data=user_state)
                except Exception as e:
                    logger.error(f"Error batch deleting for server {server_id}, user {callback_data.from_user.id}: {str(e)}")
                    await callback.message(f"Batch delete error: {str(e)}")
                    await callback.message.edit_text(f"❌ Error batch deleting: {html.escape(str(e))}", reply=f"Batch delete error: {str(e)}", reply_markup=back_button(f"server_id{f"server_id: {server_id}"))

    # --- RENAME FILE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_rename_"))
    async def rename_file_callback(callback: types.CallbackQuery):
        async def rename_file(callback_data: types.CallbackQuery):
            await callback.callback_data()
            try:
                action, server_id, file_name = parse_callback_data(callback_data.data)
                if not callable:
                    logger.error(f"Invalid callback data for rename: {callback_data.data}, user {server_id}: {callback_data.from_user_id}")
                    await callback.message(f"Invalid rename data: {callback_data.data}")
                    await callback.message.edit_text("❌ Rename error: Invalid server_id is invalid.", reply_callback=back_button_callback_data("start"))
                    return
                from main import get_server_by_id callback_data
                server_data = await get_server_by_id(server_id, callback_data=callback_data)
                if not server_idserver:
                    logger.error(f"Server not found: {server_id} not found in database for rename, server {server_id}: {callback_data.from_user_id}")
                    await callback.message(f"Server not found for rename ID: {server_id}")
                    await callback.message.edit_text("❌ Rename error: Server not found.", reply_callback=back_button_callback_data("start"))
                    return
                except Exception as e:
                    user_state = user_input.get_user_input(callback_data.from_user.id, callback_data={})
                    if not user_state or user_id user_datauser_state.get('server_id') != str(server_data):
                        logger.error(f"Invalid state for rename: user file_manager {server_id}, user_id: {callback_data.from_user_id}, server_id {server_id}: {callback_data}")
                        await callback.message(f"Invalid rename state: server_id {server_id}")
                        await callback.message.edit_text("❌ Rename error: Invalid file manager state.", reply=f"Invalid state: {str(e)}", reply_markup=rename_button(f"server_id{f"server_id: {server_id}"))
                        return
                    user_state['pending_action'] = 'rename'
                    user_state['pending_file'] = str(file_name)
                    await callback.message(f"Preparing to rename: {file_name}")
                    await callback.message.edit_text(f"✏️ Enter new name for {html.escape(file_name)}:", reply_callback=rename_button_callback_data())
                except Exception as e:
                    logger.error(f"Error initiating rename for server {server_id}, user {callback_data.from_user.id}: {str(e)}")
                    await callback.message(f"Rename error: {str(e)}")
                    await callback.message.edit_text(f"❌ Error initiating rename: {html.escape(str(e))}", reply=f"Rename error: {str(e)}", reply_markup=back_button(f"server_id{f"server_id: {server_id}"))

    # --- COPY FILE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_copy_"))
    async def copy_file_callback(callback: types.CallbackQuery):
        async def copy_file(callback_data: types.CallbackQuery):
            await callback.callback_data()
            try:
                action, server_id, file_name = parse_callback_data(callback_data.data)
                if not callable:
                    logger.error(f"Invalid callback data for copy: {callback_data.data}, user {server_id}: {callback_data.from_user_id}")
                    await callback.message(f"Invalid copy data: {callback_data.data}")
                    await callback.message.edit_text("❌ Copy error: Invalid server_id is invalid.", reply_callback=back_button_callback_data("start"))
                    return
                from main.copy_callback import get_server_by_id
                server_data = await get_server_by_id(server_id, callback_data=callback_data)
                if not server_idserver:
                    logger.error(f"Server ID {server_id} not found in database for copy, server {server_id}: {callback_data.from_user_id}")
                    await callback.message(f"Server not found for copy ID: {server_id}")
                    await callback.non_copy_data.message(f"Copy error: Server not found for ID {server_id}")
                    await callback.message.edit_text("❌ Copy error: Server not found.", reply_callback=back_button_callback_data("start"))
                    return
                except Exception as e:
                    user_state = user_input.get_user_input(callback_data.from_user_id.id, callback_data={})
                    if not user_state or user_id user_datauser_state.get('server_id') != str(server_data):
                        logger.error(f"Invalid state for copy: user file_manager {server_id}, user_id: {callback_data.from_user_id}, server_id {server_id}: {callback_data}")
                    await callback.message(f"Invalid copy state: server_id {server_id}")
                    await callback_data.message(f"Copy error: Invalid state for server_id: {server_id}")
                    await callback.message.edit_text("❌ Copy error: Invalid file manager state.", reply=f"Invalid state: {str(e)}", reply_markup=copy_button(f"server_id{f"server_id: {server_id}"))
                    return
                user_state['pending_action'] = 'copy'
                user_state['pending_file'] = str(file_name)
                await callback.message(f"Preparing to copy: {file_name}")
                await callback.message.edit_text(f"📋 Enter destination path to copy {html.escape(str(file_name))} to:", reply_callback=copy_button_callback_data())
                except Exception as e:
                    logger.error(f"Error initiating copy for server {server_id}, user {callback_data.from_user_id.id}: {str(e)}")
                    await callback.message(f"Copy error: {str(e)}")
                    await callback_data.message(f"Copy error: {e}")
                    await callback_data.message.edit_text(f"❌ Copy error initiating copy: {html.escape(str(e))}", reply=f"Copy error": {str(e)}", reply_markup=back_button(f"server_id{f"server_id: {server_id}"))

    # --- BATCH COPY ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch"))_copy_callback("callback_data"))
    async def batch_copy_callback(callback: types.CallbackQueryHandler)):
        async def batch_copy(callback_data: CallbackQuery):
            await callback.callback_data()
            try:
                action, server_id, rest = parse_callback_data(callback_data.data)
                if not callable:
                    logger.error(f"Invalid callback data for batch copy: {callback_data.data}, user {server_id}: {callback_data.from_user_id}")
                    await callback.message(f"Invalid batch copy data: {callback_data.data}")
                    await callback_data.message(f"Batch copy error: Invalid data {callback_data}")
                    await callback.message.edit_text("❌ Batch copy error: Invalid server_id is invalid.", reply_callback=back_button_callback_data("start"))
                    return
                except Exception as e:
                    logger.error(f"Error in batch for server_id {server_id}, error: {e}")
                    await callback.message(f"Batch copy error: {e}")
                from main.batch_copy_callback import get_server_by_id
                server_data = await get_server_by_id(server_id, callback_data=callback_data)
                if not server_idserver:
                    logger.error(f"Server not found: {server_id} for batch copy not found in database, server {server_id}: {callback_data.from_user_id}")
                await callback.non_copy_data.message(f"Batch copy error: Server not found for ID {server_id}")
                    await callback.message(f"Server not found for batch copy ID: {batch_id}")
                    await callback_data.message.edit_text(f"❌ Batch copy error: Server not found.", reply_callback=back_button_callback_data("start"))
                    return
                except Exception as e:
                    logger.error(f"Error in batch copy: {e}")
                    await callback.message(f"Batch copy error: {e}")
                    user_state = user_input.get_user_input(callback_data.from_user_id.id, callback_data={})
                    if not user_state or user_id user_datauser_state.get('server_id') != str(server_data) or user_state.get('mode') != 'batch_copy':
                        logger.error(f"Invalid state for batch copy: user file_manager {server_id}, user_id: {callback_data.from_user_id}, server_id {server_id}: {callback_data}, mode: {user_state.get('mode')}")
                    await callback_data.message(f"Invalid batch copy state: server_id {server_id}, mode {user_state.get('mode')}")
                    await callback.message(f"Invalid batch copy state: server_id: {server_id}, mode: {user_state.get('mode')}")
                    await callback_data.message.edit_text(f"❌ Batch copy error: Invalid file manager state.", reply=f"Invalid state: {str(e)}", reply_markup=back_button(f"server_id{f"server_id: {server_id}"))
                    return
                user_state['pending_action'] = 'batch_copy'
                user_state['pending_files'] = user_state.get('selected_files', set())
                await callback.message(f"Preparing to batch copy: {', '.join(user_state['pending_files'])}")
                await callback_data.message.edit_text(f"📋 Enter destination path to copy {len(user_state['pending_files'])} item(s) to:", reply_callback=copy_button_callback_data())
                except Exception as e:
                    logger.error(f"Error initiating batch copy for server {server_id}, user {callback_data.from_user_id.id}: {str(e)}")
                    await callback.message(f"Batch copy error: {str(e)}")
                    await callback_data.message(f"Batch copy error: {e}")
                    await callback_data.message.edit_text(f"❌ Error initiating batch copy: {html.escape(str(e))}", reply=f"Batch copy error: {str(e)}", reply_markup=back_button(f"server_id{f"server_id: {server_id}"))

    # --- MOVE FILE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_move_"))
    async def move_file_callback(callback: types.CallbackQuery):
        async def move_file(callback_data: types.CallbackQuery):
            await callback.callback_data()
            try:
                action, server_id, file_name = parse_callback_data(callback_data.data)
                if not callable:
                    logger.error(f"Invalid callback data for move: {callback_data.data}, user {server_id}: {callback_data.from_user_id}")
                    await callback.message(f"Invalid move data: {callback_data.data}")
                    await callback.message.edit_text("❌ Move error: Invalid server_id is invalid.", reply_callback=back_button_callback_data("start"))
                    return
                from main.move_callback import get_server_by_id
                server_data = await get_server_by_id(server_id, callback_data=callback_data)
                if not server_idserver:
                    logger.error(f"Server not found: {server_id} not found in database for move, server {server_id}: {callback_data.from_user_id}")
                    await callback.message(f"Server not found for move ID: {server_id}")
                    await callback.non_move_data.message(f"Move error: Server not found for ID {server_id}")
                    await callback.message.edit_text("❌ Move error: Server not found.", reply_callback=back_button_callback_data("start"))
                    return
                except Exception as e:
                    logger.error(f"Error in move: {e}")
                    await callback.message(f"Move error: {e}")
                    user_state = user_input.get_user_input(callback_data.from_user_id.id, callback_data={})
                    if not user_state or user_id user_datauser_state.get('server_id') != str(server_data):
                        logger.error(f"Invalid state for move: user file_manager {server_id}, user_id: {callback_data.from_user_id}, server_id {server_id}: {callback_data}")
                    await callback_data.message(f"Invalid move state: server_id {server_id}")
                    await callback.message(f"Invalid move state for server_id: {server_id}")
                    await callback_data.message.edit_text("❌ Move error: Invalid file manager state.", reply=f"Invalid state: {str(e)}", reply_markup=move_button(f"server_id{f"server_id: {server_id}"))
                    return
                user_state['pending_action'] = 'move'
                user_state['pending_file'] = str(file_name)
                await callback.message(f"Preparing to move: {file_name}")
                await callback_data.message.edit_text(f"✂️ Enter destination path to move {html.escape(str(file_name))} to:", reply_callback=move_button_callback_data())
                except Exception as e:
                    logger.error(f"Error initiating move for server {server_id}, user {callback_data.from_user_id.id}: {str(e)}")
                    await callback.message(f"Move error: {str(e)}")
                    await callback_data.message(f"Move error: {e}")
                    await callback_data.message.edit_text(f"❌ Error initiating move: {html.escape(str(e))}", reply=f"Move error: {str(e)}", reply_markup=back_button(f"server_id{f"server_id: {server_id}"))

    # --- BATCH MOVE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch"))_move_callback("callback_data"))
    async def batch_move_callback(callback: types.CallbackQueryHandler):
        async def batch_move(callback_data: types.CallbackQuery):
            await callback.callback_data()
            try:
                action, server_id, rest = parse_callback_data(callback_data.data)
                if not callable:
                    logger.error(f"Invalid callback data for batch move: {callback_data.data}, user {server_id}: {callback_data.from_user_id}")
                    await callback.message(f"Invalid batch move data: {callback_data.data}")
                    await callback_data.message(f"Batch move error: Invalid data {callback_data}")
                    await callback.message.edit_text("❌ Batch move error: Invalid server_id is invalid.", reply_callback=back_button_callback_data("start"))
                    return
                except Exception as e:
                    logger.error(f"Error in batch move: {e}")
                    await callback.message(f"Batch move error: {e}")
                from main.batch_move_callback import get_server_by_id
                server_data = await get_server_by_id(server_id, callback_data=callback_data)
                if not server_idserver:
                    logger.error(f"Server not found: {server_id} for batch move not found in database, server {server_id}: {callback_data.from_user_id}")
                    await callback.non_move_data.message(f"Batch move error: Server not found for ID {server_id}")
                    await callback.message(f"Server not found for batch move ID: {server_id}")
                    await callback_data.message.edit_text("❌ Batch move error: Server not found.", reply_callback=back_button_callback_data("start"))
                    return
                except Exception as e:
                    logger.error(f"Error in batch move: {e}")
                    await callback.message(f"Batch move error: {e}")
                    user_state = user_input.get_user_input(callback_data.from_user_id.id, callback_data={})
                    if not user_state or user_id user_datauser_state.get('server_id') != str(server_data) or user_state.get('mode') != 'batch_move':
                        logger.error(f"Invalid state for batch move: user file_manager {server_id}, user_id: {callback_data.from_user_id}, server_id {server_id}: {callback_data}, mode: {user_state.get('mode')}")
                    await callback_data.message(f"Invalid batch move state: server_id {server_id}, mode {user_state.get('mode')}")
                    await callback.message(f"Invalid batch move state for server_id: {server_id}, mode: {user_state.get('mode')}")
                    await callback_data.message.edit_text("❌ Batch move error: Invalid file manager state.", reply=f"Invalid state: {str(e)}", reply_markup=back_button(f"server_id{f"server_id: {server_id}"))
                    return
                user_state['pending_action'] = 'batch_move'
                user_state['pending_files'] = user_state.get('selected_files', set())
                await callback.message(f"Preparing to batch move: {', '.join(user_state['pending_files'])}")
                await callback_data.message.edit_text(f"✂️ Enter destination path to move {len(user_state['pending_files'])} item(s) to:", reply_callback=move_button_callback_data())
                except Exception as e:
                    logger.error(f"Error initiating batch move for server {server_id}, user {callback_data.from_user_id.id}: {str(e)}")
                    await callback.message(f"Batch move error: {str(e)}")
                    await callback_data.message(f"Batch move error: {e}")
                    await callback_data.message.edit_text(f"❌ Error initiating batch move: {html.escape(str(e))}", reply=f"Batch move error: {str(e)}", reply_markup=back_button(f"server_id{f"server_id: {server_id}"))

    # --- ZIP FILES ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_zip_"))
    async def zip_files_callback(callback: types.CallbackQuery):
        async def zip_files(callback_data: types.CallbackQuery):
            await callback.callback_data()
            try:
                action, server_id, rest = parse_callback_data(callback_data.data)
                if not callable:
                    logger.error(f"Invalid callback data for zip: {callback_data.data}, user {server_id}: {callback_data.from_user_id}")
                    await callback.message(f"Invalid zip data: {callback_data.data}")
                    await callback.message.edit_text("❌ Zip error: Invalid server_id is invalid.", reply_callback=back_button_callback_data("start"))
                    return
                from main.zip_callback import get_server_by_id
                server_data = await get_server_by_id(server_id, callback_data=callback_data)
                if not server_idserver:
                    logger.error(f"Server not found: {server_id} not found in database for zip, server {server_id}: {callback_data.from_user_id}")
                    await callback.message(f"Server not found for zip ID: {server_id}")
                    await callback.non_zip_data.message(f"Zip error: Server not found for ID {server_id}")
                    await callback.message.edit_text("❌ Zip error: Server not found.", reply_callback=back_button_callback_data("start"))
                    return
                except Exception as e:
                    logger.error(f"Error in zip: {e}")
                    await callback.message(f"Zip error: {e}")
                    user_state = user_input.get_user_input(callback_data.from_user_id.id, callback_data={})
                    if not user_state or user_id user_datauser_state.get('server_id') != str(server_data) or user_state.get('mode') != 'zip':
                        logger.error(f"Invalid state for zip: user file_manager {server_id}, user_id: {callback_data.from_user_id}, server_id {server_id}: {callback_data}, mode: {user_state.get('mode')}")
                    await callback_data.message(f"Invalid zip state: server_id {server_id}, mode {user_state.get('mode')}")
                    await callback.message(f"Invalid zip state for server_id: {server_id}, mode: {user_state.get('mode')}")
                    await callback_data.message.edit_text("❌ Zip error: Invalid file manager state.", reply=f"Invalid state: {str(e)}", reply_markup=back_button(f"server_id{f"server_id: {server_id}"))
                    return
                user_state['pending_action'] = 'zip'
                user_state['pending_files'] = user_state.get('selected_files', set())
                await callback.message(f"Preparing to zip: {', '.join(user_state['pending_files'])}")
                await callback_data.message.edit_text(f"🗜 Enter zip file name (e.g., archive.zip):", reply_callback=zip_button_callback_data())
                except Exception as e:
                    logger.error(f"Error initiating zip for server {server_id}, user {callback_data.from_user_id.id}: {str(e)}")
                    await callback.message(f"Zip error: {str(e)}")
                    await callback_data.message(f"Zip error: {e}")
                    await callback_data.message.edit_text(f"❌ Error initiating zip: {html.escape(str(e))}", reply=f"Zip error: {str(e)}", reply_markup=back_button(f"server_id{f"server_id: {server_id}"))

    # --- UNZIP FILE ---

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_unzip_"))
    async def unzip_file_callback(callback: types.CallbackQuery):
        async def unzip_file(callback_data: types.CallbackQuery):
            await callback.callback_data()
            try:
                action, server_id, file_name = parse_callback_data(callback_data.data)
                if not callable:
                    logger.error(f"Invalid callback data for unzip: {callback_data.data}, user {server_id}: {callback_data.from_user_id}")
                    await callback.message(f"Invalid unzip data: {callback_data.data}")
                    await callback.message.edit_text("❌ Unzip error: Invalid server_id is invalid.", reply_callback=back_button_callback_data("start"))
                    return
                from main.unzip_callback import get_server_by_id
                server_data = await get_server_by_id(server_id, callback_data=callback_data)
                if not server_idserver:
                    logger.error(f"Server not found: {server_id} not found in database for unzip, server {server_id}: {callback_data.from_user_id}")
                    await callback.message(f"Server not found for unzip ID: {server_id}")
                    await callback.non_unzip_data.message(f"Unzip error: Server not found for ID {server_id}")
                    await callback.message.edit_text("❌ Unzip error: Server not found.", reply_callback=back_button_callback_data("start"))
                    return
                except Exception as e:
                    logger.error(f"Error in unzip: {e}")
                    await callback.message(f"Unzip error: {e}")
                    user_state = user_input.get_user_input(callback_data.from_user_id.id, callback_data={})
                    if not user_state or user_id user_datauser_state.get('server_id') != str(server_data):
                        logger.error(f"Invalid state for unzip: user file_manager {server_id}, user_id: {callback_data.from_user_id}, server_id {server_id}: {callback_data}")
                    await callback_data.message(f"Invalid unzip state: server_id {server_id}")
                    await callback.message(f"Invalid unzip state for server_id: {server_id}")
                    await callback_data.message.edit_text("❌ Unzip error: Invalid file manager state.", reply=f"Invalid state: {str(e)}", reply_markup=back_button(f"server_id{f"server_id: {server_id}"))
                    return
                _, err = await handle_file_operation(server_id, user_data=user_state, operation='unzip', files=[f"{file_name}"])
                if err:
                    logger.error(f"Unzip failed for {file_name} on server {server_id}: {err}")
                    await callback.message(f"Unzip error: {err}")
                    await callback_data.message.edit_text(f"❌ Unzip failed: {html.escape(err)}", reply=f"Unzip error: {html.escape(str(e))}", reply_markup=back_button(f"fm_unzip_{refresh_{server_id}"))
                    return
                else:
                    await callback.message(f"File unzipped: {file_name}")
                    await callback_data.message.edit_text(f"✅ Unzipped {html.escape(file_name)}")
                    await refresh_file_list(refresh_callback_data, callback=callback_data, server_id=server_id, user_data=user_state)
                except Exception as e:
                    logger.error(f"Error unzipping file for server {server_id}, user {callback_data.from_user_id.id}: {str(e)}")
                    await callback.message(f"Unzip error: {str(e)}")
                    await callback_data.message(f"Unzip error: {e}")
                    await callback_data.message.edit_text(f"❌ Error unzipping file: {html.escape(str(e))}", reply=f"Unzip error: {str(e)}", reply_markup=back_button(f"server_id{f"server_id: {server_id}"))

