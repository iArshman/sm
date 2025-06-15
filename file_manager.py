import logging
import io
import zipfile
import os
from aiogram import Dispatcher, Bot, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from paramiko import SFTPClient, SSHException
from bson.objectid import ObjectId

logger = logging.getLogger(__name__)

def init_file_manager(dp: Dispatcher, bot: Bot, active_sessions: dict, user_input: dict):
    """
    Initialize file manager handlers for the bot.
    """
    # Store bot instance for use in handlers
    dp.bot = bot
    # Register handlers
    register_handlers(dp, active_sessions, user_input)

def get_file_manager_keyboard(server_id: str, current_path: str, selected_files: list = None):
    """
    Generate keyboard for file manager.
    """
    kb = InlineKeyboardMarkup(row_width=2)
    if current_path != '/':
        kb.add(InlineKeyboardButton("⬆️ Parent Directory", callback_data=f"fm_nav_{server_id}_.."))
    kb.add(InlineKeyboardButton("📁 Select Files", callback_data=f"fm_select_{server_id}"))
    if selected_files:
        kb.add(InlineKeyboardButton(f"🗑 Delete ({len(selected_files)})", callback_data=f"fm_delete_confirm_{server_id}"))
        kb.add(InlineKeyboardButton(f"📦 Zip ({len(selected_files)})", callback_data=f"fm_zip_{server_id}"))
    kb.add(InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}"))
    return kb

def get_selection_keyboard(server_id: str, current_path: str, file_name: str, is_selected: bool):
    """
    Generate keyboard for file selection.
    """
    action = "deselect" if is_selected else "select"
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton(f"{'❌' if is_selected else '✅'} {action.capitalize()}", callback_data=f"fm_{action}_{server_id}_{file_name}"))
    kb.add(InlineKeyboardButton("✅ Done", callback_data=f"fm_done_{server_id}"))
    return kb

def get_file_action_keyboard(server_id: str, current_path: str, file_name: str, is_dir: bool):
    """
    Generate keyboard for file actions.
    """
    kb = InlineKeyboardMarkup(row_width=2)
    if not is_dir:
        kb.add(InlineKeyboardButton("⬇️ Download", callback_data=f"fm_download_{server_id}_{file_name}"))
    else:
        kb.add(InlineKeyboardButton("📂 Open", callback_data=f"fm_nav_{server_id}_{file_name}"))
    kb.add(InlineKeyboardButton("✏️ Rename", callback_data=f"fm_rename_{server_id}_{file_name}"))
    kb.add(InlineKeyboardButton("🗑 Delete", callback_data=f"fm_delete_confirm_{server_id}_{file_name}"))
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"fm_list_{server_id}_{current_path}"))
    return kb

async def list_directory(sftp: SFTPClient, path: str):
    """
    List contents of a directory.
    """
    try:
        files = sftp.listdir_attr(path)
        return sorted(files, key=lambda x: (not x.st_mode & 0o040000, x.filename.lower()))
    except Exception as e:
        logger.error(f"Error listing directory {path}: {e}")
        raise

async def get_sftp_client(server_id: str, active_sessions: dict):
    """
    Get SFTP client for a server.
    """
    if server_id not in active_sessions:
        raise ValueError("No active SSH session for server")
    ssh = active_sessions[server_id]
    try:
        sftp = ssh.open_sftp()
        return sftp
    except SSHException as e:
        logger.error(f"Failed to open SFTP for server {server_id}: {e}")
        raise

def register_handlers(dp: Dispatcher, active_sessions: dict, user_input: dict):
    """
    Register all file manager handlers.
    """

    @dp.callback_query_handler(lambda c: c.data.startswith("file_manager_"))
    async def file_manager_start(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            user_input[callback.from_user.id] = {
                'server_id': server_id,
                'current_path': '/',
                'selected_files': []
            }
            sftp = await get_sftp_client(server_id, active_sessions)
            files = await list_directory(sftp, '/')
            text = f"📂 <b>File Manager</b>\nPath: <code>/</code>\n\n"
            for file in files:
                icon = "📁" if file.st_mode & 0o040000 else "📄"
                text += f"{icon} {file.filename}\n"
            await callback.message.edit_text(text, parse_mode='HTML', reply_markup=get_file_manager_keyboard(server_id, '/'))
            sftp.close()
        except Exception as e:
            logger.error(f"File manager start error for server {server_id}: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_list_"))
    async def list_files(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            current_path = '_'.join(parts[3:])
            user_input[callback.from_user.id]['current_path'] = current_path
            sftp = await get_sftp_client(server_id, active_sessions)
            files = await list_directory(sftp, current_path)
            text = f"📂 <b>File Manager</b>\nPath: <code>{current_path}</code>\n\n"
            for file in files:
                icon = "📁" if file.st_mode & 0o040000 else "📄"
                text += f"{icon} {file.filename}\n"
            await callback.message.edit_text(
                text,
                parse_mode='HTML',
                reply_markup=get_file_manager_keyboard(server_id, current_path, user_input[callback.from_user.id].get('selected_files', []))
            )
            sftp.close()
        except Exception as e:
            logger.error(f"List files error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_nav_"))
    async def navigate_directory(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            file_name = '_'.join(parts[3:])
            current_path = user_input[callback.from_user.id]['current_path']
            if file_name == '..':
                new_path = os.path.dirname(current_path.rstrip('/')) or '/'
            else:
                new_path = os.path.join(current_path, file_name)
            user_input[callback.from_user.id]['current_path'] = new_path
            sftp = await get_sftp_client(server_id, active_sessions)
            files = await list_directory(sftp, new_path)
            text = f"📂 <b>File Manager</b>\nPath: <code>{new_path}</code>\n\n"
            for file in files:
                icon = "📁" if file.st_mode & 0o040000 else "📄"
                text += f"{icon} {file.filename}\n"
            await callback.message.edit_text(
                text,
                parse_mode='HTML',
                reply_markup=get_file_manager_keyboard(server_id, new_path, user_input[callback.from_user.id].get('selected_files', []))
            )
            sftp.close()
        except Exception as e:
            logger.error(f"Navigate directory error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_select_"))
    async def start_selection(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            current_path = user_input[callback.from_user.id]['current_path']
            sftp = await get_sftp_client(server_id, active_sessions)
            files = await list_directory(sftp, current_path)
            text = f"📂 <b>Select Files</b>\nPath: <code>{current_path}</code>\n\n"
            for file in files:
                is_selected = file.filename in user_input[callback.from_user.id].get('selected_files', [])
                icon = "✅" if is_selected else "⬜"
                text += f"{icon} {file.filename}\n"
            await callback.message.edit_text(
                text,
                parse_mode='HTML',
                reply_markup=get_file_manager_keyboard(server_id, current_path, user_input[callback.from_user.id].get('selected_files', []))
            )
            sftp.close()
        except Exception as e:
            logger.error(f"Start selection error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith(("fm_select_", "fm_deselect_")))
    async def toggle_selection(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            action = parts[1]
            server_id = parts[2]
            file_name = '_'.join(parts[3:])
            current_path = user_input[callback.from_user.id]['current_path']
            if 'selected_files' not in user_input[callback.from_user.id]:
                user_input[callback.from_user.id]['selected_files'] = []
            if action == 'select':
                user_input[callback.from_user.id]['selected_files'].append(file_name)
            else:
                user_input[callback.from_user.id]['selected_files'].remove(file_name)
            text = f"📂 <b>File: {file_name}</b>\n"
            await callback.message.edit_text(
                text,
                parse_mode='HTML',
                reply_markup=get_selection_keyboard(server_id, current_path, file_name, action == 'select')
            )
        except Exception as e:
            logger.error(f"Toggle selection error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_done_"))
    async def finish_selection(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            current_path = user_input[callback.from_user.id]['current_path']
            await list_files(types.CallbackQuery(data=f"fm_list_{server_id}_{current_path}", message=callback.message, from_user=callback.from_user))
        except Exception as e:
            logger.error(f"Finish selection error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_download_"))
    async def download_file(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            file_name = '_'.join(parts[3:])
            current_path = user_input[callback.from_user.id]['current_path']
            file_path = os.path.join(current_path, file_name)
            sftp = await get_sftp_client(server_id, active_sessions)
            with io.BytesIO() as file_buffer:
                sftp.getfo(file_path, file_buffer)
                file_buffer.seek(0)
                await callback.message.answer_document(types.InputFile(file_buffer, filename=file_name))
            sftp.close()
            await callback.message.edit_text(
                f"📂 <b>File: {file_name}</b>\nDownloaded successfully.",
                parse_mode='HTML',
                reply_markup=get_file_action_keyboard(server_id, current_path, file_name, False)
            )
        except Exception as e:
            logger.error(f"Download file error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_upload_"))
    async def start_upload(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            user_input[callback.from_user.id]['action'] = 'upload'
            await callback.message.edit_text(
                "📤 Send the file to upload:",
                reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Cancel", callback_data=f"fm_list_{server_id}_{user_input[callback.from_user.id]['current_path']}"))
            )
        except Exception as e:
            logger.error(f"Start upload error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.message_handler(content_types=types.ContentType.DOCUMENT)
    async def handle_upload(message: types.Message):
        uid = message.from_user.id
        if uid not in user_input or user_input[uid].get('action') != 'upload':
            return
        try:
            server_id = user_input[uid]['server_id']
            current_path = user_input[uid]['current_path']
            file = await dp.bot.download_file_by_id(message.document.file_id)
            file_name = message.document.file_name
            file_path = os.path.join(current_path, file_name)
            sftp = await get_sftp_client(server_id, active_sessions)
            with io.BytesIO(file.read()) as file_buffer:
                sftp.putfo(file_buffer, file_path)
            sftp.close()
            await message.answer("✅ File uploaded successfully!")
            user_input[uid].pop('action', None)
            await list_files(types.CallbackQuery(
                data=f"fm_list_{server_id}_{current_path}",
                message=message,
                from_user=message.from_user
            ))
        except Exception as e:
            logger.error(f"Upload file error: {e}")
            await message.answer(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_zip_"))
    async def zip_files(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            current_path = user_input[callback.from_user.id]['current_path']
            selected_files = user_input[callback.from_user.id].get('selected_files', [])
            if not selected_files:
                raise ValueError("No files selected")
            sftp = await get_sftp_client(server_id, active_sessions)
            zip_name = "archive.zip"
            zip_path = os.path.join(current_path, zip_name)
            with io.BytesIO() as zip_buffer:
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    for file_name in selected_files:
                        file_path = os.path.join(current_path, file_name)
                        with io.BytesIO() as file_buffer:
                            sftp.getfo(file_path, file_buffer)
                            file_buffer.seek(0)
                            zip_file.writestr(file_name, file_buffer.read())
                zip_buffer.seek(0)
                sftp.putfo(zip_buffer, zip_path)
            sftp.close()
            user_input[callback.from_user.id]['selected_files'] = []
            await callback.message.edit_text(
                f"📦 Created {zip_name} in {current_path}",
                parse_mode='HTML',
                reply_markup=get_file_manager_keyboard(server_id, current_path)
            )
        except Exception as e:
            logger.error(f"Zip files error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_unzip_"))
    async def unzip_file(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            file_name = '_'.join(parts[3:])
            current_path = user_input[callback.from_user.id]['current_path']
            file_path = os.path.join(current_path, file_name)
            sftp = await get_sftp_client(server_id, active_sessions)
            with io.BytesIO() as zip_buffer:
                sftp.getfo(file_path, zip_buffer)
                zip_buffer.seek(0)
                with zipfile.ZipFile(zip_buffer, 'r') as zip_file:
                    for member in zip_file.namelist():
                        member_path = os.path.join(current_path, member)
                        with io.BytesIO(zip_file.read(member)) as member_buffer:
                            sftp.putfo(member_buffer, member_path)
            sftp.close()
            await callback.message.edit_text(
                f"📂 Unzipped {file_name} in {current_path}",
                parse_mode='HTML',
                reply_markup=get_file_action_keyboard(server_id, current_path, file_name, False)
            )
        except Exception as e:
            logger.error(f"Unzip file error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_delete_confirm_"))
    async def delete_confirm(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[3]
            file_name = '_'.join(parts[4:]) if len(parts) > 4 else None
            current_path = user_input[callback.from_user.id]['current_path']
            selected_files = user_input[callback.from_user.id].get('selected_files', [])
            files_to_delete = [file_name] if file_name else selected_files
            if not files_to_delete:
                raise ValueError("No files selected for deletion")
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(InlineKeyboardButton("✅ Confirm Delete", callback_data=f"fm_delete_final_{server_id}_{file_name or 'selected'}"))
            kb.add(InlineKeyboardButton("⬅️ Cancel", callback_data=f"fm_list_{server_id}_{current_path}"))
            text = f"⚠️ Are you sure you want to delete:\n" + "\n".join(files_to_delete)
            await callback.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
        except Exception as e:
            logger.error(f"Delete confirm error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_delete_final_"))
    async def delete_final(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[3]
            file_name = '_'.join(parts[4:]) if parts[4] != 'selected' else None
            current_path = user_input[callback.from_user.id]['current_path']
            selected_files = user_input[callback.from_user.id].get('selected_files', []) if not file_name else []
            files_to_delete = [file_name] if file_name else selected_files
            sftp = await get_sftp_client(server_id, active_sessions)
            for file in files_to_delete:
                file_path = os.path.join(current_path, file)
                try:
                    sftp.remove(file_path)
                except:
                    sftp.rmdir(file_path)
            sftp.close()
            if not file_name:
                user_input[callback.from_user.id]['selected_files'] = []
            await callback.message.edit_text(
                f"🗑 Deleted {len(files_to_delete)} file(s)",
                parse_mode='HTML',
                reply_markup=get_file_manager_keyboard(server_id, current_path)
            )
        except Exception as e:
            logger.error(f"Delete final error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_rename_"))
    async def start_rename(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            file_name = '_'.join(parts[3:])
            user_input[callback.from_user.id]['action'] = 'rename'
            user_input[callback.from_user.id]['file_name'] = file_name
            await callback.message.edit_text(
                f"✏️ Enter new name for {file_name}:",
                reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Cancel", callback_data=f"fm_list_{server_id}_{user_input[callback.from_user.id]['current_path']}"))
            )
        except Exception as e:
            logger.error(f"Start rename error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.message_handler(lambda message: message.from_user.id in user_input and user_input[message.from_user.id].get('action') == 'rename')
    async def handle_rename(message: types.Message):
        uid = message.from_user.id
        try:
            server_id = user_input[uid]['server_id']
            current_path = user_input[uid]['current_path']
            old_name = user_input[uid]['file_name']
            new_name = message.text
            old_path = os.path.join(current_path, old_name)
            new_path = os.path.join(current_path, new_name)
            sftp = await get_sftp_client(server_id, active_sessions)
            sftp.rename(old_path, new_path)
            sftp.close()
            user_input[uid].pop('action', None)
            user_input[uid].pop('file_name', None)
            await message.answer("✅ File renamed successfully!")
            await list_files(types.CallbackQuery(
                data=f"fm_list_{server_id}_{current_path}",
                message=message,
                from_user=message.from_user
            ))
        except Exception as e:
            logger.error(f"Handle rename error: {e}")
            await message.answer(f"❌ Error: {str(e)}")
