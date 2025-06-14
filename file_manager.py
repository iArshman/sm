import logging, paramiko, re, os, html
from datetime import datetime
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from io import BytesIO

logger = logging.getLogger(__name__)

def init_file_manager(dp, bot, sessions, user_state):
    def sanitize_path(path): return os.path.normpath(re.sub(r'[;&|`\n\r]', '', path)).replace('\\', '/').lstrip('/')
    def format_size(b): return f"{b} B" if b < 1024 else f"{b/1024:.2f} KB" if b < 1024**2 else f"{b/(1024**2):.2f} MB" if b < 1024**3 else f"{b/(1024**3):.2f} GB"
    
    def exec_ssh(ssh, cmd):
        try:
            _, stdout, stderr = ssh.exec_command(cmd)
            out, err = stdout.read().decode(), stderr.read().decode()
            if err: logger.warning(f"SSH cmd '{cmd}' error: {err}")
            return out.strip(), err.strip()
        except Exception as e:
            logger.error(f"SSH cmd '{cmd}' failed: {e}")
            raise

    def parse_ls(out):
        files = []
        for line in out.splitlines():
            m = re.match(r'^([drwx-]+)\s+\d+\s+(\S+)\s+(\S+)\s+(\d+)\s+(\w+\s+\d+\s+\d+:\d+|\w+\s+\d+\s+\d+)\s+(.+)$', line)
            if m:
                perms, owner, group, size, mtime, name = m.groups()
                try: mt = datetime.strptime(mtime, '%b %d %H:%M').replace(year=datetime.now().year).strftime('%Y-%m-%d %H:%M')
                except: mt = datetime.strptime(mtime, '%b %d %Y').strftime('%Y-%m-%d') if ' ' in mtime else mtime
                files.append({'name': name.strip(), 'is_dir': perms.startswith('d'), 'size': int(size), 'mtime': mt, 'perms': perms, 'owner': owner, 'group': group})
        return files

    async def get_files(sid, path, ssh):
        try:
            out, err = exec_ssh(ssh, f'ls -l --time-style=+"%b %d %H:%M" "{sanitize_path(path)}"')
            return None, f"Directory '{path}' not found" if "No such file" in err else (parse_ls(out), None)
        except Exception as e:
            return None, str(e)

    def build_kb(sid, path, files, uid, sel_mode=False, sel_files=None):
        kb = InlineKeyboardMarkup(row_width=4)
        kb.row(*[InlineKeyboardButton(t, callback_data=c) for t, c in [
            ("⬅️ Server", f"server_{sid}"), ("🔍 Search", f"fm_search_{sid}"),
            ("📤 Upload", f"fm_upload_{sid}"), ("📁 New Folder", f"fm_new_folder_{sid}")]])
        kb.add(InlineKeyboardButton("☑️ Select Files", callback_data=f"fm_select_mode_{sid}"))
        max_len = max((len(f['name']) for f in files), default=10)
        for f in sorted(files, key=lambda x: (not x['is_dir'], x['name'].lower())):
            icon, name, size, mt = "📁" if f['is_dir'] else "📄", f['name'].ljust(max_len), format_size(f['size']), f['mtime']
            label = f"{icon} {name} | {size} | {mt}"
            if sel_mode:
                sel = "✅" if f['name'] in sel_files else "☑️"
                cb = f"fm_toggle_select_{sid}_{f['name']}"
                kb.row(InlineKeyboardButton(sel, callback_data=cb), InlineKeyboardButton(label, callback_data=cb))
            else:
                cb = f"fm_nav_{sid}_{f['name']}" if f['is_dir'] else f"fm_file_{sid}_{f['name']}"
                kb.add(InlineKeyboardButton(label, callback_data=cb))
        if path != '/': kb.add(InlineKeyboardButton("⬆️ Parent Directory", callback_data=f"fm_nav_{sid}_.."))
        if sel_mode and sel_files: kb.add(InlineKeyboardButton(f"Selected: {len(sel_files)} Action", callback_data=f"fm_selection_actions_{sid}"))
        return kb

    def build_sel_actions(sid, sel_count):
        kb = InlineKeyboardMarkup(row_width=3)
        kb.row(*[InlineKeyboardButton(t, callback_data=c) for t, c in [
            ("📋 Copy", f"fm_batch_copy_{sid}"), ("✂️ Move", f"fm_batch_move_{sid}"), ("🗑 Delete", f"fm_batch_delete_{sid}")]])
        if sel_count > 1: kb.add(InlineKeyboardButton("🗜 Zip", callback_data=f"fm_zip_{sid}"))
        if sel_count == 1: kb.add(InlineKeyboardButton("✏️ Rename", callback_data=f"fm_rename_{sid}_{list(user_state[uid]['selected_files'])[0].split('/')[-1]}"))
        kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"fm_cancel_select_{sid}"))
        return kb

    def build_file_actions(sid, fname, is_zip=False):
        kb = InlineKeyboardMarkup(row_width=3)
        kb.row(*[InlineKeyboardButton(t, callback_data=c) for t, c in [
            ("📥 Download", f"fm_download_{sid}_{fname}"), ("🗑 Delete", f"fm_delete_{sid}_{fname}"), ("✏️ Rename", f"fm_rename_{sid}_{fname}")]])
        kb.row(*[InlineKeyboardButton(t, callback_data=c) for t, c in [
            ("👁️ View", f"fm_view_{sid}_{fname}"), ("📋 Copy", f"fm_copy_{sid}_{fname}"), ("✂️ Move", f"fm_move_{sid}_{fname}")]])
        if is_zip: kb.insert(InlineKeyboardButton("📂 Unzip", callback_data=f"fm_unzip_{sid}_{fname}"))
        kb.add(*[InlineKeyboardButton(t, callback_data=c) for t, c in [
            ("ℹ️ Details", f"fm_details_{sid}_{fname}"), ("⬅️ Back", f"fm_refresh_{sid}")]])
        return kb

    def back_btn(cb): return InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Back", callback_data=cb))
    def cancel_btn(): return InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Cancel", callback_data="fm_cancel"))

    async def handle_error(callback, sid, msg, e=None):
        if e: logger.error(f"Error for server {sid}: {e}")
        await callback.message.edit_text(f"❌ {msg}", reply_markup=back_btn(f"server_{sid}"))

    async def refresh_fm(callback, sid, uid, path, ssh, msg="🗂 File Manager: {}"):
        try:
            files, err = await get_files(sid, path, ssh)
            if err: return await handle_error(callback, sid, f"Error: {err}")
            kb = build_kb(sid, path, files, uid, user_state[uid].get('mode') == 'select_files', {f.split('/')[-1] for f in user_state[uid].get('selected_files', set())})
            await callback.message.edit_text(msg.format(path), parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            await handle_error(callback, sid, "Error refreshing file manager", e)

    @dp.callback_query_handler(lambda c: c.data.startswith("file_manager_"))
    async def fm_start(callback: types.CallbackQuery):
        sid = callback.data.split('_')[2]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        user_state[callback.from_user.id] = {'server_id': sid, 'current_path': '/home/ubuntu', 'mode': 'file_manager', 'selected_files': set()}
        ssh = sessions[sid]
        try:
            ssh.exec_command('whoami')
            await refresh_fm(callback, sid, callback.from_user.id, '/home/ubuntu', ssh)
        except Exception as e:
            del sessions[sid]
            await handle_error(callback, sid, "SSH session expired. Please reconnect", e)

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_nav_"))
    async def nav_dir(callback: types.CallbackQuery):
        sid, dir_name = callback.data.split('_', maxsplit=3)[2:4]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid: return await handle_error(callback, sid, "Invalid file manager state")
        path = us['current_path']
        new_path = sanitize_path('/' if dir_name == '..' else f"{path.rstrip('/')}/{dir_name}")
        us['current_path'], us['mode'], us['selected_files'] = new_path, 'file_manager', set()
        ssh = sessions[sid]
        try:
            ssh.exec_command('whoami')
            await refresh_fm(callback, sid, callback.from_user.id, new_path, ssh)
        except Exception as e:
            del sessions[sid]
            await handle_error(callback, sid, "SSH session expired. Please reconnect", e)

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_toggle_select_"))
    async def toggle_select(callback: types.CallbackQuery):
        sid, fname = callback.data.split('_', maxsplit=3)[2:4]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid or us.get('mode') != 'select_files': return await handle_error(callback, sid, "Invalid file manager state")
        path, sel = us['current_path'], us.get('selected_files', set())
        full_path = f"{path.rstrip('/')}/{fname}"
        sel.remove(full_path) if full_path in sel else sel.add(full_path)
        us['selected_files'] = sel
        ssh = sessions[sid]
        try:
            ssh.exec_command('whoami')
            await refresh_fm(callback, sid, callback.from_user.id, path, ssh, f"🗂 File Manager: {{}}\n☑️ Selected: {len(sel)} item(s)")
        except Exception as e:
            del sessions[sid]
            await handle_error(callback, sid, "SSH session expired. Please reconnect", e)

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_select_mode_"))
    async def sel_mode(callback: types.CallbackQuery):
        sid = callback.data.split('_')[3]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid: return await handle_error(callback, sid, "Invalid file manager state")
        us['mode'], us['selected_files'] = 'select_files', set()
        ssh = sessions[sid]
        try:
            ssh.exec_command('whoami')
            await refresh_fm(callback, sid, callback.from_user.id, us['current_path'], ssh, f"🗂 File Manager: {{}}\n☑️ Selection Mode: Click ☑️ to select files")
        except Exception as e:
            del sessions[sid]
            await handle_error(callback, sid, "SSH session expired. Please reconnect", e)

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_selection_actions_"))
    async def sel_actions(callback: types.CallbackQuery):
        sid = callback.data.split('_')[3]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid or us.get('mode') != 'select_files': return await handle_error(callback, sid, "Invalid file manager state")
        sel = us.get('selected_files', set())
        if not sel: return await callback.message.edit_text("❌ No files selected", reply_markup=back_btn(f"fm_refresh_{sid}"))
        kb = build_sel_actions(sid, len(sel))
        await callback.message.edit_text(f"🗂 File Manager: {us['current_path']}\n☑️ Selected: {len(sel)} item(s)\nChoose an action:", parse_mode="HTML", reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_cancel_select_"))
    async def cancel_sel(callback: types.CallbackQuery):
        sid = callback.data.split('_')[3]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid: return await handle_error(callback, sid, "Invalid file manager state")
        us['mode'], us['selected_files'] = 'file_manager', set()
        ssh = sessions[sid]
        try:
            ssh.exec_command('whoami')
            await refresh_fm(callback, sid, callback.from_user.id, us['current_path'], ssh)
        except Exception as e:
            del sessions[sid]
            await handle_error(callback, sid, "SSH session expired. Please reconnect", e)

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_file_"))
    async def file_actions(callback: types.CallbackQuery):
        sid, fname = callback.data.split('_', maxsplit=3)[2:4]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid or us.get('mode') != 'file_manager': return await handle_error(callback, sid, "Invalid file manager state")
        kb = build_file_actions(sid, fname, fname.lower().endswith('.zip'))
        await callback.message.edit_text(f"📄 File: {fname}\nPath: {us['current_path']}", parse_mode="HTML", reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_download_"))
    async def download(callback: types.CallbackQuery):
        sid, fname = callback.data.split('_', maxsplit=3)[2:4]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid or us.get('mode') != 'file_manager': return await handle_error(callback, sid, "Invalid file manager state")
        path, ssh = us['current_path'], sessions[sid]
        try:
            ssh.exec_command('whoami')
            await callback.message.edit_text(f"📥 Downloading {fname}...")
            sftp = ssh.open_sftp()
            try:
                with sftp.file(f"{path.rstrip('/')}/{fname}", 'rb') as f:
                    data = f.read()
                await bot.send_document(callback.from_user.id, document=types.InputFile(BytesIO(data), filename=fname), caption=f"File from {path}")
            finally:
                sftp.close()
            await refresh_fm(callback, sid, callback.from_user.id, path, ssh)
        except Exception as e:
            del sessions[sid] if isinstance(e, paramiko.SSHException) else None
            await handle_error(callback, sid, f"Error downloading file: {e}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_delete_"))
    async def delete_confirm(callback: types.CallbackQuery):
        sid, fname = callback.data.split('_', maxsplit=3)[2:4]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid or us.get('mode') != 'file_manager': return await handle_error(callback, sid, "Invalid file manager state")
        kb = InlineKeyboardMarkup(row_width=2).add(
            InlineKeyboardButton("✅ Yes, delete", callback_data=f"fm_delete_confirm_{sid}_{fname}"),
            InlineKeyboardButton("⬅️ Back", callback_data=f"fm_refresh_{sid}"))
        await callback.message.edit_text(f"⚠️ Delete '{fname}' from {us['current_path']}?", parse_mode="HTML", reply_markup=kb)

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_delete_confirm_"))
    async def delete(callback: types.CallbackQuery):
        sid, fname = callback.data.split('_', maxsplit=4)[3:5]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid or us.get('mode') != 'file_manager': return await handle_error(callback, sid, "Invalid state")
        path, ssh = us['current_path'], sessions[sid]
        try:
            ssh.exec_command('whoami')
            _, err = exec_ssh(ssh, f'rm -rf "{path.rstrip('/')}/{fname}")
            if err: return await callback.message.edit_text(f"❌ Error: {err}", reply=r_markup=back_btn(f"fm_refresh_{sid}"))
            await refresh_fm(callback, sid, callback.from_user.id, path, ssh, f"🗂 File Manager: {{}}\n✅ '{fname}' deleted")
        except Exception as e:
            del sessions[sid] if isinstance(e, paramiko.SSHException) else None
            await handle_error(callback, sid, f"Error deleting file: {e}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_delete_"))
    async def batch_delete_confirm(callback: types.CallbackQuery):
        sid = sid callback.data.split('_')[3]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = us user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid or us.get('mode') != 'select_files': return await handle_error(callback, sid, "Invalid file manager state")
        sel = sel us.get('selected_files', set())
        if not sel: return await callback.message.edit_text("❌ No files selected", reply_markup="back_btn(f"fm_refresh_{sid}"))
        kb = kb InlineKeyboardMarkup(row_width=2).add(
            InlineKeyboardButton("✅ Yes, delete", callback_data="f"fm_batch_delete_confirm_{sid}"),
            InlineKeyboardButton("⬅️ Back", callback_data="f"fm_cancel_select_{sid}"))
        await callback.message.edit_text(f"⚠️ Delete {len(sel)} file(s) from {us['current_path']}?", parse_mode="HTML", reply_markup=kb, parse_mode="HTML")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_delete_confirm_"))
    async def batch_delete(callback: types.CallbackQuery):
        sid = sid callback.data.split('_')[4]
        if sid not in sessions: return await handle_error(callback, sid, f"No active SSH session")
        us = us try user_state.get(callback.from_user.id, {})
        if us.get('server_id'): != sid or us.get('mode') != 'select_files': return await handle_error(callback, sid, "Invalid state")
        path, sel, ssh = sel, us sel['current_path'], us.get('selected_files', set()), sessions[sid]
        try:
            ssh.exec_command('whoami'):
            errors = errors []
            for for file_path in in sel:
                file_name = fname os.path.basename(file_path)
                _, err_data = exec_ssh(ssh, f"rm -rf \"{file_path}\"")
                if err_data: errors.append(f"{file_name}: {err_data}")
            us['selected_files'] = = set()
            us['mode'] = = 'file_manager'
            await refresh_fm(callback, sid, callback.from_user.id, path, ssh, f"🗂 File Manager: {{}}\n✅ {len(sel)-len(errors)} file(s) deleted" + (f"\nErrors:\n{'\n'.join(errors)}" if errors else ""))
        except Exception as e:
            del sessions[sid] if isinstance(e, paramiko.SSHException) else None
            await handle_error(callback, sid, f"Error deleting files: {e}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_copy_"))
    async def batch_copy_start(callback: types.CallbackQuery):
        sid = sid callback.data.split('_')[3]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = us user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid or us.get('mode') != 'select_files': return await handle_error(callback, sid, "Invalid file manager state")
        if not us.get('selected_files', set()): return await callback.message.edit_text("❌ No files selected", reply_markup="back_btn(f"fm_refresh_{sid}"))
        us['mode'] = = 'batch_copy'
        await bot.send_message(callback.from_user.id, f"📋 Send destination path for copying {len(us['selected_files'])} file(s) (e.g., /home/ubuntu/dest)", reply_markup=cancel_btn())

    @dp.message_handler(lambda m: user_state.get(m.from_user.id, {}).get('mode') == 'batch_copy')
    async def handle_batch_copy(message: types.Message):
        uid, us = uid message.from_user.id, user_state.get(message.from_user.id, {})
        sid = sid us.get('server_id')
        if sid not in sessions: return await message.answer("❌ No active SSH session")
        dest = dest sanitize_path(message.text.strip())
        if not dest: return await message.answer("❌ Invalid destination path")
        path, sel, ssh = sel us['current_path'], us.get('selected_files', set()), sessions[sid]
        try:
            ssh.exec_command('whoami')
            errors = errors []
            for for src in in sel:
                fname = fname os.path.basename(src)
                _, err = err exec_ssh(ssh, f"cp -r \"{src}\" \"{dest}/\"")
                if err: errors.append(f"{fname}: {err}")
            us['selected_files'], us['mode'] = = set(), 'file_manager'
            await refresh_fm(message, sid, uid, path, ssh, f"🗂 File Manager: {{}}\n✅ {len(sel)-len(errors)} file(s) copied to '{dest}'" + (f"\nErrors:\n{'\n'.join(errors)}" if errors else ""))
        except Exception as e:
            del sessions[sid] if isinstance(e, paramiko.SSHException) else None
            await message.answer(f"❌ Error copying files: {str(e)}")
        finally:
            if us.get('mode') == 'batch_copy': us['mode'] = 'file_manager'

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_batch_move_"))
    async def batch_move_start(callback: types.CallbackQuery):
        sid = sid callback.data.split('_')[3]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = us user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid or us.get('mode') != 'select_files': return await handle_error(callback, sid, "Invalid file manager state")
        if not us.get('selected_files', set()): return await callback.message.edit_text("❌ No files selected", reply_markup="back_btn(f"fm_refresh_{sid}"))
        us['mode'] = = 'batch_move'
        await bot.send_message(callback.from_user.id, f"✂️ Send destination path for moving {len(us['selected_files'])} file(s) (e.g., /home/ubuntu/dest)", reply_markup=cancel_btn())

    @dp.message_handler(lambda m: user_state.get(m.from_user.id, {}).get('mode') == 'batch_move')
    async def handle_batch_move(message: types.Message):
        uid, us = uid message.from_user.id, user_state.get(message.from_user.id, {})
        sid = sid us.get('server_id')
        if sid not in sessions: return await message.answer("❌ No active SSH session")
        dest = dest sanitize_path(message.text.strip())
        if not dest: return await message.answer("❌ Invalid destination path")
        path, sel, ssh = sel us['current_path'], us.get('selected_files', set()), sessions[sid]
        try:
            ssh.exec_command('whoami')
            errors = errors []
            for for src in in sel:
                fname = fname os.path.basename(src)
                _, err = err exec_ssh(ssh, f"mv \"{src}\" \"{dest}/\"")
                if err: errors.append(f"{fname}: {err}")
            us['selected_files'], us['mode'] = = set(), 'file_manager'
            await refresh_fm(message, sid, uid, path, ssh, f"🗂 File Manager: {{}}\n✅ {len(sel)-len(errors)} file(s) moved to '{dest}'" + (f"\nErrors:\n{'\n'.join(errors)}" if errors else ""))
        except Exception as e:
            del sessions[sid] if isinstance(e, paramiko.SSHException) else None
            await message.answer(f"❌ Error moving files: {str(e)}")
        finally:
            if us.get('mode') == 'batch_move': us['mode'] = 'file_manager'

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_zip_"))
    async def zip_start(callback: types.CallbackQuery):
        sid = sid callback.data.split('_')[2]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = us user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid or us.get('mode') != 'select_files': return await handle_error(callback, sid, "Invalid file manager state")
        if not us.get('selected_files', set()): return await callback.message.edit_text("❌ No files selected", reply_markup="back_btn(f"fm_refresh_{sid}"))
        us['mode'] = = 'zip_mode'
        await bot.send_message(callback.from_user.id, f"🗜 Enter zip file name (e.g., archive.zip) for {us['current_path']}", reply_markup=cancel_btn())

    @dp.message_handler(lambda m: user_state.get(m.from_user.id, {}).get('mode') == 'zip_mode')
    async def handle_zip(message: types.Message):
        uid, us = uid message.from_user.id, user_state.get(message.from_user.id, {})
        sid = sid us.get('server_id')
        if sid not in sessions: return await message.answer("❌ No active SSH session")
        zip_name = zip_name re.sub(r'[;&|`\n\r]', '', message.text.strip()) + ('.zip' if not message.text.endswith('.zip') else '')
        if not zip_name: return await message.answer("❌ Invalid zip file name")
        path, sel, ssh = sel us['current_path'], us.get('selected_files', set()), sessions[sid]
        try:
            ssh.exec_command('whoami')
            files = files ' '.join(f"\"{f}\"" for f in sel)
            _, err = err exec_ssh(ssh, f"zip -r \"{path.rstrip('/')}/{zip_name}\" {files}")
            if err: return await message.answer(f"❌ Error creating zip: {err}")
            us['selected_files'], us['mode'] = = set(), 'file_manager'
            await refresh_fm(message, sid, uid, path, ssh, f"🗂 File Manager: {{}}\n✅ Zip file '{zip_name}' created")
        except Exception as e:
            del sessions[sid] if isinstance(e, paramiko.SSHException) else None
            await message.answer(f"❌ Error creating zip: {str(e)}")
        finally:
            if us.get('mode') == 'zip_mode': us['mode'] = 'file_manager'

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_unzip_"))
    async def unzip(callback: types.CallbackQuery):
        sid, fname = fname callback.data.split('_', maxsplit=3)[2:4]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = us user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid or us.get('mode') != 'file_manager': return await handle_error(callback, sid, "Invalid file manager state")
        path, ssh = ssh us['current_path'], sessions[sid]
        try:
            ssh.exec_command('whoami')
            _, err = err exec_ssh(ssh, f"unzip -o \"{path.rstrip('/')}/{fname}\" -d \"{path}\"")
            if err: return await callback.message.edit_text(f"❌ Error unzipping: {err}", reply_markup="back_btn(f"fm_refresh_{sid}"))
            await refresh_fm(callback, sid, callback.from_user.id, path, ssh, f"🗂 File Manager: {{}}\n✅ File '{fname}' unzipped")
        except Exception as e:
            del sessions[sid] if isinstance(e, paramiko.SSHException) else None
            await handle_error(callback, sid, f"Error unzipping: {e}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_upload_"))
    async def upload_start(callback: types.CallbackQuery):
        sid = sid callback.data.split('_')[2]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = us user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid: return await handle_error(callback, sid, "Invalid file manager state")
        us['mode'] = = 'upload_file'
        await bot.send_message(callback.from_user.id, f"📤 Send file to upload to {us['current_path']}", reply_markup=cancel_btn())

    @dp.message_handler(content_types=types.ContentType.DOCUMENT)
    async def handle_upload(message: types.Message):
        uid, us = uid message.from_user.id, user_state.get(message.from_user.id, {})
        if us.get('mode') != 'upload_file': return await message.answer("❌ Not in upload mode")
        sid = sid us.get('server_id')
        if sid not in sessions: return await message.answer("❌ No active SSH session")
        fname, path = path message.document.file_name, us['current_path']
        try:
            ssh = ssh sessions[sid]
            ssh.exec_command('whoami')
            await message.answer(f"📤 Uploading {fname} to {path}")
            data = dataio = BytesIO()
            await message.document.download(data)
            sftp = sftp.open_sftp()
            try:
                with sftp.file(f"{path.rstrip('/')}/{fname}", 'wb') as f:
                    f.write(data.getvalue())
            finally:
                sftp.close()
            us['mode'] = = 'file_manager'
            await refresh_fm(message.reply, sid, uid, path, ssh, f"🗂 File Manager: {{}}\n✅ File '{fname}' uploaded")
        except Exception as e:
            del sessions[sid] if isinstance(e, paramiko.SSHException) else None
            await message.answer(f"❌ Error uploading: {str(e)}")
        finally:
            if us.get('mode') == 'upload_file': us['mode'] = 'file_manager'

    @dp.callback_query_handler(lambda c: c.data == "fm_cancel")
    async def cancel(callback: types.CallbackQuery):
        us = us user_state.get(callback.from_user.id, {})
        sid = sid us.get('server_id')
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us['mode'], us['selected_files'] = = 'file_manager', set()
        ssh = ssh sessions[sid]
        try:
            ssh.exec_command('whoami')
            await refresh_fm(callback, sid, callback.from_user.id, us['current_path'], ssh, f"🗂 File Manager: {{}}\n✅ Operation cancelled")
        except Exception as e:
            del sessions[sid] if isinstance(e, paramiko.SSHException) else None
            await handle_error(callback, sid, f"Error cancelling: {e}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_new_folder_"))
    async def new_folder_start(callback: types.CallbackQuery):
        sid = sid callback.data.split('_')[3]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = us user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid: return await handle_error(callback, sid, "Invalid file manager state")
        us['mode'] = = 'new_folder'
        await bot.send_message(callback.from_user.id, f"📁 Send folder name for {us['current_path']}", reply_markup=cancel_btn())

    @dp.message_handler(lambda m: user_state.get(m.from_user.id, {}).get('mode') == 'new_folder')
    async def handle_new_folder(message: types.Message):
        uid, us = uid message.from_user.id, user_state.get(message.from_user.id, {})
        sid = sid us.get('server_id')
        if sid not in sessions: return await message.answer("❌ No active SSH session")
        fname = fname re.sub(r'[;&|`\n\r]', '', message.text.strip())
        if not fname: return await message.answer("❌ Invalid folder name")
        path, ssh = ssh us['current_path'], sessions[sid]
        try:
            ssh.exec_command('whoami')
            _, err = err exec_ssh(ssh, f"mkdir \"{path.rstrip('/')}/{fname}\"")
            if err: return await message.answer(f"❌ Error creating folder: {err}")
            us['mode'] = = 'file_manager'
            await refresh_fm(message, sid, uid, path, ssh, f"🗂 File Manager: {{}}\n✅ Folder '{fname}' created")
        except Exception as e:
            del sessions[sid] if isinstance(e, paramiko.SSHException) else None
            await message.answer(f"❌ Error creating folder: {str(e)}")
        finally:
            if us.get('mode') == 'new_folder': us['mode'] = 'file_manager'

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_rename_"))
    async def rename_start(callback: types.CallbackQuery):
        sid, fname = fname callback.data.split('_', maxsplit=3)[2:4]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = us user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid or us.get('mode') not in ['file_manager', 'select_files']: return await handle_error(callback, sid, "Invalid file manager state")
        us['mode'], us['old_name'] = = 'rename_file', fname
        await bot.send_message(callback.from_user.id, f"✏️ Send new name for '{fname}' in {us['current_path']}", reply_markup=cancel_btn())

    @dp.message_handler(lambda m: user_state.get(m.from_user.id, {}).get('mode') == 'rename_file')
    async def handle_rename(message: types.Message):
        uid, us = uid message.from_user.id, user_state.get(message.from_user.id, {})
        sid = sid us.get('server_id')
        if sid not in sessions: return await message.answer("❌ No active SSH session")
        old, new = new us.get('old_name'), re.sub(r'[;&|`\n\r]', '', message.text.strip())
        if not new: return await message.answer("❌ Invalid file name")
        path, ssh = ssh us['current_path'], sessions[sid]
        try:
            ssh.exec_command('whoami')
            _, err = err exec_ssh(ssh, f"mv \"{path.rstrip('/')}/{old}\" \"{path.rstrip('/')}/{new}\"")
            if err: return await message.answer(f"❌ Error renaming: {err}")
            us['mode'] = = 'file_manager'
            await refresh_fm(message, sid, uid, path, ssh, f"🗂 File Manager: {{}}\n✅ File '{old}' renamed to '{new}'")
        except Exception as e:
            del sessions[sid] if isinstance(e, paramiko.SSHException) else None
            await message.answer(f"❌ Error renaming: {str(e)}")
        finally:
            if us.get('mode') == 'rename_file': us['mode'] = 'file_manager'

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_view_"))
    async def view_file(callback: types.CallbackQuery):
        sid, fname = callback.data.split('_', maxsplit=3)[2:4]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid or us.get('mode') != 'file_manager': return await handle_error(callback, sid, "Invalid file manager state")
        path, ssh = us['current_path'], sessions[sid]
        try:
            ssh.exec_command('whoami')
            sftp = ssh.open_sftp()
            try:
                out, err = exec_ssh(ssh, f'file "{path.rstrip('/')}/{fname}"')
                if err or 'text' not in out.lower(): return await callback.message.edit_text("❌ Only text files can be viewed", reply_markup=back_btn(f"fm_refresh_{sid}"))
                with sftp.file(f"{path.rstrip('/')}/{fname}", 'r') as f:
                    content = html.escape(f.read(4000).decode('utf-8', errors='replace'))[:3900] + ("..." if len(f.read()) >= 4000 else "")
                await callback.message.edit_text(f"📌 File: {fname}\nPath: {path}\n\n<pre>{content}</pre>", parse_mode="HTML", reply_markup=back_btn(f"fm_refresh_{sid}"))
            finally:
                sftp.close()
        except Exception as e:
            del sessions[sid] if isinstance(e, paramiko.SSHException) else None
            await handle_error(callback, sid, f"Error viewing file: {e}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_details_"))
    async def details_file(callback: types.CallbackQuery):
        sid, fname = callback.data.split('_', maxsplit=3)[2:4]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid or us.get('mode') != 'file_manager': return await handle_error(callback, sid, "Invalid file manager state")
        path, ssh = us['current_path'], sessions[sid]
        try:
            ssh.exec_command('whoami')
            out, err = exec_ssh(ssh, f'ls -l "{path.rstrip('/')}/{fname}"')
            if err: return await callback.message.edit_text(f"❌ Error: {err}", reply=back_btn(f"fm_refresh_{sid}"))
            info = parse_ls(out)[0]
            text = f"📋 File Details: {fname}\nPath: {path}\nSize: {format_size(info['size'])}\nModified: {info['mtime']}\nPermissions: {info['perms']}\nOwner: {info['owner']}\nGroup: {info['group']}"
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_btn(f"fm_refresh_{sid}"))
        except Exception as e:
            del sessions[sid] if isinstance(e, paramiko.SSHException) else None
            await handle_error(callback, sid, f"Error fetching details: {e}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_copy_"))
    async def copy_start(callback: types.CallbackQuery):
        sid, fname = callback.data.split('_', maxsplit=3)[2:4]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid or us.get('mode') != 'file_manager': return await handle_error(callback, sid, "Invalid file manager state")
        us['mode'], us['file_name'] = 'copy_file', fname
        await bot.send_message(callback.from_user.id, f"📋 Send destination path for copying '{fname}' from {us['current_path']}", reply_markup=cancel_btn())

    @dp.message_handler(lambda m: user_state.get(m.from_user.id, {}).get('mode') == 'copy_file')
    async def handle_copy(message: types.Message):
        uid, us = message.from_user.id, user_state.get(message.from_user.id, {})
        sid = us.get('server_id')
        if sid not in sessions: return await message.answer("❌ No active SSH session")
        fname, dest = us.get('file_name'), sanitize_path(message.text.strip())
        if not dest: return await message.answer("❌ Invalid destination path")
        path, ssh = us['current_path'], sessions[sid]
        try:
            ssh.exec_command('whoami')
            _, err = exec_ssh(ssh, f'cp -r "{path.rstrip('/')}/{fname}" "{dest}"')
            if err: return await message.answer(f"❌ Error copying: {err}")
            us['mode'] = 'file_manager'
            await refresh_fm(message, sid, uid, path, ssh, f"🗂 File Manager: {{}}\n✅ File '{fname}' copied to '{dest}'")
        except Exception as e:
            del sessions[sid] if isinstance(e, paramiko.SSHException) else None
            await message.answer(f"❌ Error copying: {str(e)}")
        finally:
            if us.get('mode') == 'copy_file': us['mode'] = 'file_manager'

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_move_"))
    async def move_start(callback: types.CallbackQuery):
        sid, fname = callback.data.split('_', maxsplit=3)[2:4]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid or us.get('mode') != 'file_manager': return await handle_error(callback, sid, "Invalid file manager state")
        us['mode'], us['file_name'] = 'move_file', fname
        await bot.send_message(callback.from_user.id, f"✂️ Send destination path for moving '{fname}' from {us['current_path']}", reply_markup=cancel_btn())

    @dp.message_handler(lambda m: user_state.get(m.from_user.id, {}).get('mode') == 'move_file')
    async def handle_move(message: types.Message):
        uid, us = message.from_user.id, user_state.get(message.from_user.id, {})
        sid = us.get('server_id')
        if sid not in sessions: return await message.answer("❌ No active SSH session")
        fname, dest = us.get('file_name'), sanitize_path(message.text.strip())
        if not dest: return await message.answer("❌ Invalid destination path")
        path, ssh = us['current_path'], sessions[sid]
        try:
            ssh.exec_command('whoami')
            _, err = exec_ssh(ssh, f'mv "{path.rstrip('/')}/{fname}" "{dest}"')
            if err: return await message.answer(f"❌ Error moving: {err}")
            us['mode'] = 'file_manager'
            await refresh_fm(message, sid, uid, path, ssh, f"🗂 File Manager: {{}}\n✅ File '{fname}' moved to '{dest}'")
        except Exception as e:
            del sessions[sid] if isinstance(e, paramiko.SSHException) else None
            await message.answer(f"❌ Error moving: {str(e)}")
        finally:
            if us.get('mode') == 'move_file': us['mode'] = 'file_manager'

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_refresh_"))
    async def refresh(callback: types.CallbackQuery):
        sid = callback.data.split('_')[2]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid: return await handle_error(callback, sid, "Invalid file manager state")
        ssh = sessions[sid]
        try:
            ssh.exec_command('whoami')
            await refresh_fm(callback, sid, callback.from_user.id, us['current_path'], ssh)
        except Exception as e:
            del sessions[sid] if isinstance(e, paramiko.SSHException) else None
            await handle_error(callback, sid, "Error refreshing: {e}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_search_"))
    async def search_start(callback: types.CallbackQuery):
        sid = callback.data.split('_')[2]
        if sid not in sessions: return await handle_error(callback, sid, "No active SSH session")
        us = user_state.get(callback.from_user.id, {})
        if us.get('server_id') != sid: return await handle_error(callback, sid, "Invalid file manager state")
        us['mode'] = 'search_files'
        await bot.send_message(callback.from_user.id, f"🔍 Enter search query for files in {us['current_path']}", reply_markup=cancel_btn())

    @dp.message_handler(lambda m: user_state.get(m.from_user.id, {}).get('mode') == 'search_files')
    async def handle_search(message: types.Message):
        uid, us = message.from_user.id, user_state.get(message.from_user.id, {})
        sid = us.get('server_id')
        if sid not in sessions: return await message.answer("❌ No active SSH session")
        query = re.sub(r'[;&|`\n\r]', '', message.text.strip())
        if not query: return await message.answer("❌ Invalid search query")
        path, ssh = us['current_path'], sessions[sid]
        try:
            ssh.exec_command('whoami')
            out, err = exec_ssh(ssh, f'find "{path}" -type f -name "*{query}*" 2>/dev/null')
            if err: return await message.answer(f"❌ Error searching: {err}")
            files = [{'name': f[len(path)+1:], 'is_dir': False, 'size': 0, 'mtime': 'Unknown', 'perms': '', 'owner': '', 'group': ''} for f in out.splitlines() if f]
            us['mode'] = 'file_manager'
            kb = build_kb(sid, path, files[:50], uid) if files else InlineKeyboardMarkup().add(InlineKeyboardButton("⬅️ Back", callback_data=f"fm_refresh_{sid}"))
            text = f"🗂 File Manager: {path}\n🔍 Found {len(files)} file(s) for '{query}'" + (f" (showing first 50)" if len(files) > 50 else "")
            await message.answer(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            del sessions[sid] if isinstance(e, paramiko.SSHException) else None
            await message.answer(f"❌ Error searching: {str(e)}")
        finally:
            if us.get('mode') == 'search_files': us['mode'] = 'file_manager'
