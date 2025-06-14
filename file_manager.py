import logging
import paramiko
import re
import os
import html
from datetime import datetime
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from io import BytesIO

logger = logging.getLogger(__name__)

def init_file_manager(dp, bot, sessions, user_state):
    def sanitize_path(path):
        path = re.sub(r'[;&|`\n\r]', '', path)
        path = os.path.normpath(path).replace('\\', '/')
        return path.lstrip('/')

    def format_size(bytes_size):
        if bytes_size < 1024:
            return f"{bytes_size} B"
        elif bytes_size < 1024**2:
            return f"{bytes_size/1024:.2f} KB"
        elif bytes_size < 1024**3:
            return f"{bytes_size/(1024**2):.2f} MB"
        else:
            return f"{bytes_size/(1024**3):.2f} GB"

    def execute_ssh_command(ssh, command):
        try:
            _, stdout, stderr = ssh.exec_command(command)
            output = stdout.read().decode()
            error = stderr.read().decode()
            if error:
                logger.warning(f"SSH command '{command}' error: {error}")
            return output.strip(), error.strip()
        except Exception as e:
            logger.error(f"SSH command '{command}' failed: {e}")
            raise

    def parse_ls_output(output):
        files = []
        for line in output.splitlines():
            match = re.match(r'^([drwx-]+)\s+\d+\s+(\S+)\s+(\S+)\s+(\d+)\s+(\w+\s+\d+\s+\d+:\d+|\w+\s+\d+\s+\d+)\s+(.+)$', line)
            if match:
                perms, owner, group, size, mtime, name = match.groups()
                try:
                    mt = datetime.strptime(mtime, '%b %d %H:%M').replace(year=datetime.now().year).strftime('%Y-%m-%d %H:%M')
                except:
                    mt = datetime.strptime(mtime, '%b %d %Y').strftime('%Y-%m-%d') if ' ' in mtime else mtime
                files.append({
                    'name': name.strip(),
                    'is_dir': perms.startswith('d'),
                    'size': int(size),
                    'mtime': mt,
                    'perms': perms,
                    'owner': owner,
                    'group': group
                })
        return files

    async def get_files(server_id, path, ssh):
        try:
            output, error = execute_ssh_command(ssh, f'ls -l --time-style=+"%b %d %H:%M" "{sanitize_path(path)}"')
            if "No such file" in error:
                return None, f"Directory '{path}' not found"
            return parse_ls_output(output), None
        except Exception as e:
            return None, str(e)

    def build_file_manager_keyboard(server_id, path, files, user_id, selection_mode=False, selected_files=None):
        keyboard = InlineKeyboardMarkup(row_width=4)
        top_buttons = [
            InlineKeyboardButton("⬅️ Server", callback_data=f"server_{server_id}"),
            InlineKeyboardButton("🔍 Search", callback_data=f"fm_search_{server_id}"),
            InlineKeyboardButton("📤 Upload", callback_data=f"fm_upload_{server_id}"),
            InlineKeyboardButton("📁 New Folder", callback_data=f"fm_new_folder_{server_id}")
        ]
        keyboard.row(*top_buttons)
        keyboard.add(InlineKeyboardButton("☑️ Select Files", callback_data=f"fm_select_mode_{server_id}"))

        max_name_length = max((len(f['name']) for f in files), default=10)
        for file in sorted(files, key=lambda x: (not x['is_dir'], x['name'].lower())):
            icon = "📁" if file['is_dir'] else "📄"
            name = file['name'].ljust(max_name_length)
            size = format_size(file['size'])
            mtime = file['mtime']
            label = f"{icon} {name} | {size} | {mtime}"
            if selection_mode:
                sel_icon = "✅" if file['name'] in selected_files else "☑️"
                cb_data = f"fm_toggle_select_{server_id}_{file['name']}"
                keyboard.row(
                    InlineKeyboardButton(sel_icon, callback_data=cb_data),
                    InlineKeyboardButton(label, callback_data=cb_data)
                )
            else:
                cb_data = f"fm_nav_{server_id}_{file['name']}" if file['is_dir'] else f"fm_file_{server_id}_{file['name']}"
                keyboard.add(InlineKeyboardButton(label, callback_data=cb_data))
        
        if path != '/':
            keyboard.add(InlineKeyboardButton("⬆️ Parent Directory", callback_data=f"fm_nav_{server_id}_.."))
        
        if selection_mode and selected_files:
            keyboard.add(InlineKeyboardButton(f"Selected: {len(selected_files)} Action", callback_data=f"fm_selection_actions_{server_id}"))
        
        return keyboard

    def build_selection_actions_keyboard(server_id, selected_count):
        keyboard = InlineKeyboardMarkup(row_width=3)
        actions = [
            InlineKeyboardButton("📋 Copy", callback_data=f"fm_batch_copy_{server_id}"),
            InlineKeyboardButton("✂️ Move", callback_data=f"fm_batch_move_{server_id}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"fm_batch_delete_{server_id}")
        ]
        keyboard.row(*actions)
        if selected_count > 1:
            keyboard.add(InlineKeyboardButton("🗜 Zip", callback_data=f"fm_zip_{server_id}"))
        if selected_count == 1:
            fname = list(user_state[user_id]['selected_files'])[0].split('/')[-1]
            keyboard.add(InlineKeyboardButton("✏️ Rename", callback_data=f"fm_rename_{server_id}_{fname}"))
        keyboard.add(InlineKeyboardButton("❌ Cancel", callback_data=f"fm_cancel_select_{server_id}"))
        return keyboard

    def build_file_actions_keyboard(server_id, file_name, is_zip=False):
        keyboard = InlineKeyboardMarkup(row_width=3)
        top_row = [
            InlineKeyboardButton("📥 Download", callback_data=f"fm_download_{server_id}_{file_name}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"fm_delete_{server_id}_{file_name}"),
            InlineKeyboardButton("✏️ Rename", callback_data=f"fm_rename_{server_id}_{file_name}")
        ]
        middle_row = [
            InlineKeyboardButton("👁️ View", callback_data=f"fm_view_{server_id}_{file_name}"),
            InlineKeyboardButton("📋 Copy", callback_data=f"fm_copy_{server_id}_{file_name}"),
            InlineKeyboardButton("✂️ Move", callback_data=f"fm_move_{server_id}_{file_name}")
        ]
        keyboard.row(*top_row)
        keyboard.row(*middle_row)
        if is_zip:
            keyboard.insert(InlineKeyboardButton("📂 Unzip", callback_data=f"fm_unzip_{server_id}_{file_name}"))
        bottom_row = [
            InlineKeyboardButton("ℹ️ Details", callback_data=f"fm_details_{server_id}_{file_name}"),
            InlineKeyboardButton("⬅️ Back", callback_data=f"fm_refresh_{server_id}")
        ]
        keyboard.add(*bottom_row)
        return keyboard

    def back_button(callback): return InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Back", callback_data=callback))
    def cancel_button(): return InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Cancel", callback_data="fm_cancel"))

    async def handle_error(callback, server_id, message, exception=None):
        if exception:
            logger.error(f"Error for server {server_id}: {exception}")
        await callback.message.edit_text(f"❌ {message}", reply_markup=back_button(f"server_{server_id}"))

    async def refresh_file_manager(callback, server_id, user_id, path, ssh, message="🗂 File Manager: {}"):
        try:
            files, error = await get_files(server_id, path, ssh)
            if error:
                return await handle_error(callback, server_id, f"Error: {error}")
            keyboard = build_file_manager_keyboard(
                server_id, path, files, user_id,
                user_state[user_id].get('mode') == 'select_files',
                {f.split('/')[-1] for f in user_state[user_id].get('selected_files', set())}
            )
            await callback.message.edit_text(message.format(path), parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            await handle_error(callback, server_id, "Error refreshing file manager", e)

    @dp.callback_query_handler(lambda c: c.data.startswith("file_manager_"))
    async def file_manager_start(callback: types.CallbackQuery):
        server_id = callback.data.split('_')[2]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_state[callback.from_user.id] = {
            'server_id': server_id,
            'current_path': '/home/ubuntu',
            'mode': 'file_manager',
            'selected_files': set()
        }
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            await refresh_file_manager(callback, server_id, callback.from_user.id, '/home/ubuntu', ssh)
        except Exception as e:
            del sessions[server_id]
            await handle_error(callback, server_id, "SSH session expired. Please reconnect", e)

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_nav_"))
    async def navigate_directory(callback: types.CallbackQuery):
        server_id, dir_name = callback.data.split('_', maxsplit=3)[2:4]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id:
            return await handle_error(callback, server_id, "Invalid file manager state")
        current_path = user_data['current_path']
        new_path = sanitize_path('/' if dir_name == '..' else f"{current_path.rstrip('/')}/{dir_name}")
        user_data['current_path'] = new_path
        user_data['mode'] = 'file_manager'
        user_data['selected_files'] = set()
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            await refresh_file_manager(callback, server_id, callback.from_user.id, new_path, ssh)
        except Exception as e:
            del sessions[server_id]
            await handle_error(callback, server_id, "SSH session expired. Please reconnect", e)

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_toggle_select_"))
    async def toggle_file_selection(callback: types.CallbackQuery):
        server_id, file_name = callback.data.split('_', maxsplit=3)[2:4]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id or user_data.get('mode') != 'select_files':
            return await handle_error(callback, server_id, "Invalid file manager state")
        current_path = user_data['current_path']
        selected_files = user_data.get('selected_files', set())
        full_path = f"{current_path.rstrip('/')}/{file_name}"
        if full_path in selected_files:
            selected_files.remove(full_path)
        else:
            selected_files.add(full_path)
        user_data['selected_files'] = selected_files
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            await refresh_file_manager(
                callback, server_id, callback.from_user.id, current_path, ssh,
                f"🗂 File Manager: {{}}\n☑️ Selected: {len(selected_files)} item(s)"
            )
        except Exception as e:
            del sessions[server_id]
            await handle_error(callback, server_id, "SSH session expired. Please reconnect", e)

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_select_mode_"))
    async def select_mode(callback: types.CallbackQuery):
        server_id = callback.data.split('_')[3]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id:
            return await handle_error(callback, server_id, "Invalid file manager state")
        user_data['mode'] = 'select_files'
        user_data['selected_files'] = set()
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            await refresh_file_manager(
                callback, server_id, callback.from_user.id, user_data['current_path'], ssh,
                f"🗂 File Manager: {{}}\n☑️ Selection Mode: Click ☑️ to select files"
            )
        except Exception as e:
            del sessions[server_id]
            await handle_error(callback, server_id, "SSH session expired. Please reconnect", e)

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_selection_actions_"))
    async def show_selection_actions(callback: types.CallbackQuery):
        server_id = callback.data.split('_')[3]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id or user_data.get('mode') != 'select_files':
            return await handle_error(callback, server_id, "Invalid file manager state")
        selected_files = user_data.get('selected_files', set())
        if not selected_files:
            await callback.message.edit_text("❌ No files selected", reply_markup=back_button(f"fm_refresh_{server_id}"))
            return
        keyboard = build_selection_actions_keyboard(server_id, len(selected_files))
        await callback.message.edit_text(
            f"🗂 File Manager: {user_data['current_path']}\n☑️ Selected: {len(selected_files)} item(s)\nChoose an action:",
            parse_mode="HTML",
            reply_markup=keyboard
        )

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_cancel_select_"))
    async def cancel_selection(callback: types.CallbackQuery):
        server_id = callback.data.split('_')[3]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id:
            return await handle_error(callback, server_id, "Invalid file manager state")
        user_data['mode'] = 'file_manager'
        user_data['selected_files'] = set()
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            await refresh_file_manager(callback, server_id, callback.from_user.id, user_data['current_path'], ssh)
        except Exception as e:
            del sessions[server_id]
            await handle_error(callback, server_id, "SSH session expired. Please reconnect", e)

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_file_"))
    async def file_actions(callback: types.CallbackQuery):
        server_id, file_name = callback.data.split('_', maxsplit=3)[2:4]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id or user_data.get('mode') != 'file_manager':
            return await handle_error(callback, server_id, "Invalid file manager state")
        keyboard = build_file_actions_keyboard(server_id, file_name, file_name.lower().endswith('.zip'))
        await callback.message.edit_text(
            f"📄 File: {file_name}\nPath: {user_data['current_path']}",
            parse_mode="HTML",
            reply_markup=keyboard
        )

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_download_"))
    async def download_file(callback: types.CallbackQuery):
        server_id, file_name = callback.data.split('_', maxsplit=3)[2:4]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id or user_data.get('mode') != 'file_manager':
            return await handle_error(callback, server_id, "Invalid file manager state")
        path = user_data['current_path']
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            await callback.message.edit_text(f"📥 Downloading {file_name}...")
            sftp = ssh.open_sftp()
            try:
                with sftp.file(f"{path.rstrip('/')}/{file_name}", 'rb') as file:
                    file_data = file.read()
                await bot.send_document(
                    callback.from_user.id,
                    document=types.InputFile(BytesIO(file_data), filename=file_name),
                    caption=f"File from {path}"
                )
            finally:
                sftp.close()
            await refresh_file_manager(callback, server_id, callback.from_user.id, path, ssh)
        except Exception as e:
            if isinstance(e, paramiko.SSHException):
                del sessions[server_id]
            await handle_error(callback, server_id, f"Error downloading file: {e}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_delete_"))
    async def delete_file_confirm(callback: types.CallbackQuery):
        server_id, file_name = callback.data.split('_', maxsplit=3)[2:4]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id or user_data.get('mode') != 'file_manager':
            return await handle_error(callback, server_id, "Invalid file manager state")
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("✅ Yes, delete", callback_data=f"fm_delete_confirm_{server_id}_{file_name}"),
            InlineKeyboardButton("⬅️ Back", callback_data=f"fm_refresh_{server_id}")
        )
        await callback.message.edit_text(
            f"⚠️ Delete '{file_name}' from {user_data['current_path']}?",
            parse_mode="HTML",
            reply_markup=keyboard
        )

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_delete_confirm_"))
    async def delete_file(callback: types.CallbackQuery):
        server_id, file_name = callback.data.split('_', maxsplit=4)[3:5]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id or user_data.get('mode') != 'file_manager':
            return await handle_error(callback, server_id, "Invalid state")
        path = user_data['current_path']
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            _, error = execute_ssh_command(ssh, f'rm -rf "{path.rstrip('/')}/{file_name}"')
            if error:
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            await refresh_file_manager(
                callback, server_id, callback.from_user.id, path, ssh,
                f"🗂 File Manager: {{}}\n✅ '{file_name}' deleted"
            )
        except Exception as e:
            if isinstance(e, paramiko.SSHException):
                del sessions[server_id]
            await handle_error(callback, server_id, f"Error deleting file: {e}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_delete_"))
    async def batch_delete_confirm(callback: types.CallbackQuery):
        server_id = callback.data.split('_')[3]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id or user_data.get('mode') != 'select_files':
            return await handle_error(callback, server_id, "Invalid file manager state")
        selected_files = user_data.get('selected_files', set())
        if not selected_files:
            await callback.message.edit_text("❌ No files selected", reply_markup=back_button(f"fm_refresh_{server_id}"))
            return
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("✅ Yes, delete", callback_data=f"fm_batch_delete_confirm_{server_id}"),
            InlineKeyboardButton("⬅️ Back", callback_data=f"fm_cancel_select_{server_id}")
        )
        await callback.message.edit_text(
            f"⚠️ Delete {len(selected_files)} file(s) from {user_data['current_path']}?",
            parse_mode="HTML",
            reply_markup=keyboard
        )

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_delete_confirm_"))
    async def batch_delete(callback: types.CallbackQuery):
        server_id = callback.data.split('_')[4]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id or user_data.get('mode') != 'select_files':
            return await handle_error(callback, server_id, "Invalid state")
        path = user_data['current_path']
        selected_files = user_data.get('selected_files', set())
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            errors = []
            for file_path in selected_files:
                fname = os.path.basename(file_path)
                _, error = execute_ssh_command(ssh, f'rm -rf "{file_path}"')
                if error:
                    errors.append(f"{fname}: {error}")
            user_data['selected_files'] = set()
            user_data['mode'] = 'file_manager'
            await refresh_file_manager(
                callback, server_id, callback.from_user.id, path, ssh,
                f"🗂 File Manager: {{}}\n✅ {len(selected_files)-len(errors)} file(s) deleted" + (f"\nErrors:\n{'\n'.join(errors)}" if errors else "")
            )
        except Exception as e:
            if isinstance(e, paramiko.SSHException):
                del sessions[server_id]
            await handle_error(callback, server_id, f"Error deleting files: {e}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_copy_"))
    async def batch_copy_start(callback: types.CallbackQuery):
        server_id = callback.data.split('_')[3]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id or user_data.get('mode') != 'select_files':
            return await handle_error(callback, server_id, "Invalid file manager state")
        selected_files = user_data.get('selected_files', set())
        if not selected_files:
            await callback.message.edit_text("❌ No files selected", reply_markup=back_button(f"fm_refresh_{server_id}"))
            return
        user_data['mode'] = 'batch_copy'
        await bot.send_message(
            callback.from_user.id,
            f"📋 Send destination path for copying {len(selected_files)} file(s) (e.g., /home/ubuntu/dest)",
            reply_markup=cancel_button()
        )

    @dp.message_handler(lambda m: user_state.get(m.from_user.id, {}).get('mode') == 'batch_copy')
    async def handle_batch_copy(message: types.Message):
        user_id = message.from_user.id
        user_data = user_state.get(user_id, {})
        server_id = user_data.get('server_id')
        if server_id not in sessions:
            return await message.answer("❌ No active SSH session")
        destination = sanitize_path(message.text.strip())
        if not destination:
            return await message.answer("❌ Invalid destination path")
        path = user_data['current_path']
        selected_files = user_data.get('selected_files', set())
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            errors = []
            for src in selected_files:
                fname = os.path.basename(src)
                _, error = execute_ssh_command(ssh, f'cp -r "{src}" "{destination}/"')
                if error:
                    errors.append(f"{fname}: {error}")
            user_data['selected_files'] = set()
            user_data['mode'] = 'file_manager'
            await refresh_file_manager(
                message, server_id, user_id, path, ssh,
                f"🗂 File Manager: {{}}\n✅ {len(selected_files)-len(errors)} file(s) copied to '{destination}'" + (f"\nErrors:\n{'\n'.join(errors)}" if errors else "")
            )
        except Exception as e:
            if isinstance(e, paramiko.SSHException):
                del sessions[server_id]
            await message.answer(f"❌ Error copying files: {str(e)}")
        finally:
            if user_data.get('mode') == 'batch_copy':
                user_data['mode'] = 'file_manager'

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_move_"))
    async def batch_move_start(callback: types.CallbackQuery):
        server_id = callback.data.split('_')[3]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id or user_data.get('mode') != 'select_files':
            return await handle_error(callback, server_id, "Invalid file manager state")
        selected_files = user_data.get('selected_files', set())
        if not selected_files:
            await callback.message.edit_text("❌ No files selected", reply_markup=back_button(f"fm_refresh_{server_id}"))
            return
        user_data['mode'] = 'batch_move'
        await bot.send_message(
            callback.from_user.id,
            f"✂️ Send destination path for moving {len(selected_files)} file(s) (e.g., /home/ubuntu/dest)",
            reply_markup=cancel_button()
        )

    @dp.message_handler(lambda m: user_state.get(m.from_user.id, {}).get('mode') == 'batch_move')
    async def handle_batch_move(message: types.Message):
        user_id = message.from_user.id
        user_data = user_state.get(user_id, {})
        server_id = user_data.get('server_id')
        if server_id not in sessions:
            return await message.answer("❌ No active SSH session")
        destination = sanitize_path(message.text.strip())
        if not destination:
            return await message.answer("❌ Invalid destination path")
        path = user_data['current_path']
        selected_files = user_data.get('selected_files', set())
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            errors = []
            for src in selected_files:
                fname = os.path.basename(src)
                _, error = execute_ssh_command(ssh, f'mv "{src}" "{destination}/"')
                if error:
                    errors.append(f"{fname}: {error}")
            user_data['selected_files'] = set()
            user_data['mode'] = 'file_manager'
            await refresh_file_manager(
                message, server_id, user_id, path, ssh,
                f"🗂 File Manager: {{}}\n✅ {len(selected_files)-len(errors)} file(s) moved to '{destination}'" + (f"\nErrors:\n{'\n'.join(errors)}" if errors else "")
            )
        except Exception as e:
            if isinstance(e, paramiko.SSHException):
                del sessions[server_id]
            await message.answer(f"❌ Error moving files: {str(e)}")
        finally:
            if user_data.get('mode') == 'batch_move':
                user_data['mode'] = 'file_manager'

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_zip_"))
    async def zip_files_start(callback: types.CallbackQuery):
        server_id = callback.data.split('_')[2]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id or user_data.get('mode') != 'select_files':
            return await handle_error(callback, server_id, "Invalid file manager state")
        selected_files = user_data.get('selected_files', set())
        if not selected_files:
            await callback.message.edit_text("❌ No files selected", reply_markup=back_button(f"fm_refresh_{server_id}"))
            return
        user_data['mode'] = 'zip_mode'
        await bot.send_message(
            callback.from_user.id,
            f"🗜 Enter zip file name (e.g., archive.zip) for {user_data['current_path']}",
            reply_markup=cancel_button()
        )

    @dp.message_handler(lambda m: user_state.get(m.from_user.id, {}).get('mode') == 'zip_mode')
    async def handle_zip_files(message: types.Message):
        user_id = message.from_user.id
        user_data = user_state.get(user_id, {})
        server_id = user_data.get('server_id')
        if server_id not in sessions:
            return await message.answer("❌ No active SSH session")
        zip_name = re.sub(r'[;&|`\n\r]', '', message.text.strip())
        zip_name += '.zip' if not zip_name.endswith('.zip') else ''
        if not zip_name:
            return await message.answer("❌ Invalid zip file name")
        path = user_data['current_path']
        selected_files = user_data.get('selected_files', set())
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            files = ' '.join(f'"{f}"' for f in selected_files)
            _, error = execute_ssh_command(ssh, f'zip -r "{path.rstrip('/')}/{zip_name}" {files}')
            if error:
                return await message.answer(f"❌ Error creating zip: {error}")
            user_data['selected_files'] = set()
            user_data['mode'] = 'file_manager'
            await refresh_file_manager(
                message, server_id, user_id, path, ssh,
                f"🗂 File Manager: {{}}\n✅ Zip file '{zip_name}' created"
            )
        except Exception as e:
            if isinstance(e, paramiko.SSHException):
                del sessions[server_id]
            await message.answer(f"❌ Error creating zip: {str(e)}")
        finally:
            if user_data.get('mode') == 'zip_mode':
                user_data['mode'] = 'file_manager'

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_unzip_"))
    async def unzip_file(callback: types.CallbackQuery):
        server_id, file_name = callback.data.split('_', maxsplit=3)[2:4]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id or user_data.get('mode') != 'file_manager':
            return await handle_error(callback, server_id, "Invalid file manager state")
        path = user_data['current_path']
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            _, error = execute_ssh_command(ssh, f'unzip -o "{path.rstrip('/')}/{file_name}" -d "{path}"')
            if error:
                await callback.message.edit_text(f"❌ Error unzipping: {error}", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            await refresh_file_manager(
                callback, server_id, callback.from_user.id, path, ssh,
                f"🗂 File Manager: {{}}\n✅ File '{file_name}' unzipped"
            )
        except Exception as e:
            if isinstance(e, paramiko.SSHException):
                del sessions[server_id]
            await handle_error(callback, server_id, f"Error unzipping: {e}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_upload_"))
    async def upload_file_start(callback: types.CallbackQuery):
        server_id = callback.data.split('_')[2]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id:
            return await handle_error(callback, server_id, "Invalid file manager state")
        user_data['mode'] = 'upload_file'
        await bot.send_message(
            callback.from_user.id,
            f"📤 Send file to upload to {user_data['current_path']}",
            reply_markup=cancel_button()
        )

    @dp.message_handler(content_types=types.ContentType.DOCUMENT)
    async def handle_file_upload(message: types.Message):
        user_id = message.from_user.id
        user_data = user_state.get(user_id, {})
        if user_data.get('mode') != 'upload_file':
            return await message.answer("❌ Not in upload mode")
        server_id = user_data.get('server_id')
        if server_id not in sessions:
            return await message.answer("❌ No active SSH session")
        file_name = message.document.file_name
        path = user_data['current_path']
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            await message.answer(f"📤 Uploading {file_name} to {path}")
            file_data = BytesIO()
            await message.document.download(destination=file_data)
            sftp = ssh.open_sftp()
            try:
                with sftp.file(f"{path.rstrip('/')}/{file_name}", 'wb') as file:
                    file.write(file_data.getvalue())
            finally:
                sftp.close()
            user_data['mode'] = 'file_manager'
            await refresh_file_manager(
                message, server_id, user_id, path, ssh,
                f"🗂 File Manager: {{}}\n✅ File '{file_name}' uploaded"
            )
        except Exception as e:
            if isinstance(e, paramiko.SSHException):
                del sessions[server_id]
            await message.answer(f"❌ Error uploading: {str(e)}")
        finally:
            if user_data.get('mode') == 'upload_file':
                user_data['mode'] = 'file_manager'

    @dp.callback_query_handler(lambda c: c.data == "fm_cancel")
    async def cancel_operation(callback: types.CallbackQuery):
        user_data = user_state.get(callback.from_user.id, {})
        server_id = user_data.get('server_id')
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data['mode'] = 'file_manager'
        user_data['selected_files'] = set()
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            await refresh_file_manager(
                callback, server_id, callback.from_user.id, user_data['current_path'], ssh,
                f"🗂 File Manager: {{}}\n✅ Operation cancelled"
            )
        except Exception as e:
            if isinstance(e, paramiko.SSHException):
                del sessions[server_id]
            await handle_error(callback, server_id, f"Error cancelling: {e}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_new_folder_"))
    async def new_folder_start(callback: types.CallbackQuery):
        server_id = callback.data.split('_')[3]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id:
            return await handle_error(callback, server_id, "Invalid file manager state")
        user_data['mode'] = 'new_folder'
        await bot.send_message(
            callback.from_user.id,
            f"📁 Send folder name for {user_data['current_path']}",
            reply_markup=cancel_button()
        )

    @dp.message_handler(lambda m: user_state.get(m.from_user.id, {}).get('mode') == 'new_folder')
    async def handle_new_folder(message: types.Message):
        user_id = message.from_user.id
        user_data = user_state.get(user_id, {})
        server_id = user_data.get('server_id')
        if server_id not in sessions:
            return await message.answer("❌ No active SSH session")
        folder_name = re.sub(r'[;&|`\n\r]', '', message.text.strip())
        if not folder_name:
            return await message.answer("❌ Invalid folder name")
        path = user_data['current_path']
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            _, error = execute_ssh_command(ssh, f'mkdir "{path.rstrip('/')}/{folder_name}"')
            if error:
                return await message.answer(f"❌ Error creating folder: {error}")
            user_data['mode'] = 'file_manager'
            await refresh_file_manager(
                message, server_id, user_id, path, ssh,
                f"🗂 File Manager: {{}}\n✅ Folder '{folder_name}' created"
            )
        except Exception as e:
            if isinstance(e, paramiko.SSHException):
                del sessions[server_id]
            await message.answer(f"❌ Error creating folder: {str(e)}")
        finally:
            if user_data.get('mode') == 'new_folder':
                user_data['mode'] = 'file_manager'

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_rename_"))
    async def rename_file_start(callback: types.CallbackQuery):
        server_id, file_name = callback.data.split('_', maxsplit=3)[2:4]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id or user_data.get('mode') not in ['file_manager', 'select_files']:
            return await handle_error(callback, server_id, "Invalid file manager state")
        user_data['mode'] = 'rename_file'
        user_data['old_name'] = file_name
        await bot.send_message(
            callback.from_user.id,
            f"✏️ Send new name for '{file_name}' in {user_data['current_path']}",
            reply_markup=cancel_button()
        )

    @dp.message_handler(lambda m: user_state.get(m.from_user.id, {}).get('mode') == 'rename_file')
    async def handle_rename_file(message: types.Message):
        user_id = message.from_user.id
        user_data = user_state.get(user_id, {})
        server_id = user_data.get('server_id')
        if server_id not in sessions:
            return await message.answer("❌ No active SSH session")
        old_name = user_data.get('old_name')
        new_name = re.sub(r'[;&|`\n\r]', '', message.text.strip())
        if not new_name:
            return await message.answer("❌ Invalid file name")
        path = user_data['current_path']
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            _, error = execute_ssh_command(ssh, f'mv "{path.rstrip('/')}/{old_name}" "{path.rstrip('/')}/{new_name}"')
            if error:
                return await message.answer(f"❌ Error renaming: {error}")
            user_data['mode'] = 'file_manager'
            await refresh_file_manager(
                message, server_id, user_id, path, ssh,
                f"🗂 File Manager: {{}}\n✅ File '{old_name}' renamed to '{new_name}'"
            )
        except Exception as e:
            if isinstance(e, paramiko.SSHException):
                del sessions[server_id]
            await message.answer(f"❌ Error renaming: {str(e)}")
        finally:
            if user_data.get('mode') == 'rename_file':
                user_data['mode'] = 'file_manager'

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_view_"))
    async def view_file(callback: types.CallbackQuery):
        server_id, file_name = callback.data.split('_', maxsplit=3)[2:4]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id or user_data.get('mode') != 'file_manager':
            return await handle_error(callback, server_id, "Invalid file manager state")
        path = user_data['current_path']
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            sftp = ssh.open_sftp()
            try:
                output, error = execute_ssh_command(ssh, f'file "{path.rstrip('/')}/{file_name}"')
                if error or 'text' not in output.lower():
                    await callback.message.edit_text("❌ Only text files can be viewed", reply_markup=back_button(f"fm_refresh_{server_id}"))
                    return
                with sftp.file(f"{path.rstrip('/')}/{file_name}", 'r') as file:
                    content = html.escape(file.read(4000).decode('utf-8', errors='replace'))[:3900]
                    content += "..." if len(file.read()) >= 4000 else ""
                await callback.message.edit_text(
                    f"📌 File: {file_name}\nPath: {path}\n\n<pre>{content}</pre>",
                    parse_mode="HTML",
                    reply_markup=back_button(f"fm_refresh_{server_id}")
                )
            finally:
                sftp.close()
        except Exception as e:
            if isinstance(e, paramiko.SSHException):
                del sessions[server_id]
            await handle_error(callback, server_id, f"Error viewing file: {e}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_details_"))
    async def file_details(callback: types.CallbackQuery):
        server_id, file_name = callback.data.split('_', maxsplit=3)[2:4]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id or user_data.get('mode') != 'file_manager':
            return await handle_error(callback, server_id, "Invalid file manager state")
        path = user_data['current_path']
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            output, error = execute_ssh_command(ssh, f'ls -l "{path.rstrip('/')}/{file_name}"')
            if error:
                await callback.message.edit_text(f"❌ Error: {error}", reply_markup=back_button(f"fm_refresh_{server_id}"))
                return
            info = parse_ls_output(output)[0]
            text = (
                f"📋 File Details: {file_name}\n"
                f"Path: {path}\n"
                f"Size: {format_size(info['size'])}\n"
                f"Modified: {info['mtime']}\n"
                f"Permissions: {info['perms']}\n"
                f"Owner: {info['owner']}\n"
                f"Group: {info['group']}"
            )
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_button(f"fm_refresh_{server_id}"))
        except Exception as e:
            if isinstance(e, paramiko.SSHException):
                del sessions[server_id]
            await handle_error(callback, server_id, f"Error fetching details: {e}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_copy_"))
    async def copy_file_start(callback: types.CallbackQuery):
        server_id, file_name = callback.data.split('_', maxsplit=3)[2:4]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id or user_data.get('mode') != 'file_manager':
            return await handle_error(callback, server_id, "Invalid file manager state")
        user_data['mode'] = 'copy_file'
        user_data['file_name'] = file_name
        await bot.send_message(
            callback.from_user.id,
            f"📋 Send destination path for copying '{file_name}' from {user_data['current_path']}",
            reply_markup=cancel_button()
        )

    @dp.message_handler(lambda m: user_state.get(m.from_user.id, {}).get('mode') == 'copy_file')
    async def handle_copy_file(message: types.Message):
        user_id = message.from_user.id
        user_data = user_state.get(user_id, {})
        server_id = user_data.get('server_id')
        if server_id not in sessions:
            return await message.answer("❌ No active SSH session")
        file_name = user_data.get('file_name')
        destination = sanitize_path(message.text.strip())
        if not destination:
            return await message.answer("❌ Invalid destination path")
        path = user_data['current_path']
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            _, error = execute_ssh_command(ssh, f'cp -r "{path.rstrip('/')}/{file_name}" "{destination}"')
            if error:
                return await message.answer(f"❌ Error copying: {error}")
            user_data['mode'] = 'file_manager'
            await refresh_file_manager(
                message, server_id, user_id, path, ssh,
                f"🗂 File Manager: {{}}\n✅ File '{file_name}' copied to '{destination}'"
            )
        except Exception as e:
            if isinstance(e, paramiko.SSHException):
                del sessions[server_id]
            await message.answer(f"❌ Error copying: {str(e)}")
        finally:
            if user_data.get('mode') == 'copy_file':
                user_data['mode'] = 'file_manager'

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_move_"))
    async def move_file_start(callback: types.CallbackQuery):
        server_id, file_name = callback.data.split('_', maxsplit=3)[2:4]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id or user_data.get('mode') != 'file_manager':
            return await handle_error(callback, server_id, "Invalid file manager state")
        user_data['mode'] = 'move_file'
        user_data['file_name'] = file_name
        await bot.send_message(
            callback.from_user.id,
            f"✂️ Send destination path for moving '{file_name}' from {user_data['current_path']}",
            reply_markup=cancel_button()
        )

    @dp.message_handler(lambda m: user_state.get(m.from_user.id, {}).get('mode') == 'move_file')
    async def handle_move_file(message: types.Message):
        user_id = message.from_user.id
        user_data = user_state.get(user_id, {})
        server_id = user_data.get('server_id')
        if server_id not in sessions:
            return await message.answer("❌ No active SSH session")
        file_name = user_data.get('file_name')
        destination = sanitize_path(message.text.strip())
        if not destination:
            return await message.answer("❌ Invalid destination path")
        path = user_data['current_path']
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            _, error = execute_ssh_command(ssh, f'mv "{path.rstrip('/')}/{file_name}" "{destination}"')
            if error:
                return await message.answer(f"❌ Error moving: {error}")
            user_data['mode'] = 'file_manager'
            await refresh_file_manager(
                message, server_id, user_id, path, ssh,
                f"🗂 File Manager: {{}}\n✅ File '{file_name}' moved to '{destination}'"
            )
        except Exception as e:
            if isinstance(e, paramiko.SSHException):
                del sessions[server_id]
            await message.answer(f"❌ Error moving: {str(e)}")
        finally:
            if user_data.get('mode') == 'move_file':
                user_data['mode'] = 'file_manager'

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_refresh_"))
    async def refresh_file_manager(callback: types.CallbackQuery):
        server_id = callback.data.split('_')[2]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id:
            return await handle_error(callback, server_id, "Invalid file manager state")
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            await refresh_file_manager(callback, server_id, callback.from_user.id, user_data['current_path'], ssh)
        except Exception as e:
            if isinstance(e, paramiko.SSHException):
                del sessions[server_id]
            await handle_error(callback, server_id, f"Error refreshing: {e}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_search_"))
    async def search_files_start(callback: types.CallbackQuery):
        server_id = callback.data.split('_')[2]
        if server_id not in sessions:
            return await handle_error(callback, server_id, "No active SSH session")
        user_data = user_state.get(callback.from_user.id, {})
        if user_data.get('server_id') != server_id:
            return await handle_error(callback, server_id, "Invalid file manager state")
        user_data['mode'] = 'search_files'
        await bot.send_message(
            callback.from_user.id,
            f"🔍 Enter search query for files in {user_data['current_path']}",
            reply_markup=cancel_button()
        )

    @dp.message_handler(lambda m: user_state.get(m.from_user.id, {}).get('mode') == 'search_files')
    async def handle_search_files(message: types.Message):
        user_id = message.from_user.id
        user_data = user_state.get(user_id, {})
        server_id = user_data.get('server_id')
        if server_id not in sessions:
            return await message.answer("❌ No active SSH session")
        query = re.sub(r'[;&|`\n\r]', '', message.text.strip())
        if not query:
            return await message.answer("❌ Invalid search query")
        path = user_data['current_path']
        ssh = sessions[server_id]
        try:
            ssh.exec_command('whoami')
            output, error = execute_ssh_command(ssh, f'find "{path}" -type f -name "*{query}*" 2>/dev/null')
            if error:
                return await message.answer(f"❌ Error searching: {error}")
            files = [
                {'name': f[len(path)+1:], 'is_dir': False, 'size': 0, 'mtime': 'Unknown', 'perms': '', 'owner': '', 'group': ''}
                for f in output.splitlines() if f
            ]
            user_data['mode'] = 'file_manager'
            keyboard = build_file_manager_keyboard(server_id, path, files[:50], user_id) if files else InlineKeyboardMarkup().add(
                InlineKeyboardButton("⬅️ Back", callback_data=f"fm_refresh_{server_id}")
            )
            text = f"🗂 File Manager: {path}\n🔍 Found {len(files)} file(s) for '{query}'" + (f" (showing first 50)" if len(files) > 50 else "")
            await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
        except Exception as e:
            if isinstance(e, paramiko.SSHException):
                del sessions[server_id]
            await message.answer(f"❌ Error searching: {str(e)}")
        finally:
            if user_data.get('mode') == 'search_files':
                user_data['mode'] = 'file_manager'
