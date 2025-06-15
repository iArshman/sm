import logging
import io
import zipfile
import os
import hashlib
from aiogram import Dispatcher, Bot, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from paramiko import SFTPClient, SSHException

logger = logging.getLogger(__name__)

# Configuration
ALLOWED_USERS = [7405203657]  # Restrict bot access to these Telegram user IDs
ITEMS_PER_PAGE = 20  # Number of files per page
path_mapping = {}  # Store callback data mappings

def init_file_manager(dp: Dispatcher, bot: Bot, active_sessions: dict, user_input: dict):
    """
    Initialize file manager handlers for the bot.
    """
    dp.bot = bot
    register_handlers(dp, active_sessions, user_input)

def generate_callback_data(data: str) -> str:
    """
    Generate a hashed callback ID to avoid Telegram's 64-byte limit.
    """
    callback_id = hashlib.md5(data.encode()).hexdigest()
    path_mapping[callback_id] = data
    return callback_id

def get_file_manager_keyboard(server_id: str, current_path: str, page: int, selected_files: list = None):
    """
    Generate keyboard for file manager with pagination and actions.
    """
    kb = InlineKeyboardMarkup(row_width=2)
    if current_path != '/':
        parent_path = os.path.dirname(current_path.rstrip('/')) or '/'
        parent_callback = generate_callback_data(parent_path)
        kb.add(InlineKeyboardButton("🔙 Parent Directory", callback_data=f"fm_nav_{server_id}_{parent_callback}_0"))
    kb.add(InlineKeyboardButton("📁 Select Files", callback_data=f"fm_select_{server_id}_{page}"))
    if selected_files:
        kb.add(InlineKeyboardButton(f"✔️ Confirm Selections ({len(selected_files)})", callback_data=f"fm_confirm_{server_id}_{page}"))
    kb.add(InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}"))
    return kb

def get_selection_keyboard(server_id: str, current_path: str, file_name: str, page: int, is_selected: bool):
    """
    Generate keyboard for file selection.
    """
    action = "deselect" if is_selected else "select"
    file_path = os.path.join(current_path, file_name)
    callback_id = generate_callback_data(file_path)
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton(f"{'🟩' if is_selected else '🟥'} {action.capitalize()}", callback_data=f"fm_{action}_{server_id}_{callback_id}_{page}"))
    kb.add(InlineKeyboardButton("✅ Done", callback_data=f"fm_list_{server_id}_{generate_callback_data(current_path)}_{page}"))
    return kb

def get_file_action_keyboard(server_id: str, current_path: str, file_name: str, page: int, is_dir: bool):
    """
    Generate keyboard for file actions.
    """
    file_path = os.path.join(current_path, file_name)
    callback_id = generate_callback_data(file_path)
    kb = InlineKeyboardMarkup(row_width=2)
    if not is_dir:
        kb.add(InlineKeyboardButton("⬇️ Download", callback_data=f"fm_download_{server_id}_{callback_id}_{page}"))
        kb.add(InlineKeyboardButton("📦 Unzip" if file_name.endswith('.zip') else "📄 View", callback_data=f"fm_unzip_{server_id}_{callback_id}_{page}" if file_name.endswith('.zip') else "noop"))
    else:
        kb.add(InlineKeyboardButton("📂 Open", callback_data=f"fm_nav_{server_id}_{callback_id}_0"))
    kb.add(InlineKeyboardButton("✏️ Rename", callback_data=f"fm_rename_{server_id}_{callback_id}_{page}"))
    kb.add(InlineKeyboardButton("🗑 Delete", callback_data=f"fm_delete_confirm_{server_id}_{callback_id}_{page}"))
    kb.add(InlineKeyboardButton("⬆️ Upload File", callback_data=f"fm_upload_{server_id}_{page}"))
    kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"fm_list_{server_id}_{generate_callback_data(current_path)}_{page}"))
    return kb

async def list_directory(sftp: SFTPClient, path: str):
    """
    List contents of a directory via SFTP.
    """
    try:
        files = sftp.listdir_attr(path)
        return sorted(files, key=lambda x: (not x.st_mode & 0o040000, x.filename.lower()))
    except Exception as e:
        logger.error(f"Error listing directory {path}: {e}")
        raise

async def get_sftp_client(server_id: str, active_sessions: dict) -> SFTPClient:
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
        user_id = callback.from_user.id
        if user_id not in ALLOWED_USERS:
            await callback.message.edit_text("❌ You do not have access to this bot.")
            return
        try:
            server_id = callback.data.split('_')[2]
            user_input[user_id] = {
                'server_id': server_id,
                'current_path': '/',
                'selected_files': [],
                'page': 0
            }
            await list_files(callback, server_id, '/', 0)
        except Exception as e:
            logger.error(f"File manager start error for server {server_id}: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    async def list_files(callback: types.CallbackQuery, server_id: str, current_path: str, page: int):
        try:
            user_id = callback.from_user.id
            user_input[user_id]['current_path'] = current_path
            user_input[user_id]['page'] = page
            sftp = await get_sftp_client(server_id, active_sessions)
            files = await list_directory(sftp, current_path)
            start_index = page * ITEMS_PER_PAGE
            end_index = start_index + ITEMS_PER_PAGE
            paginated_files = files[start_index:end_index]
            text = f"📂 <b>File Manager</b>\nPath: <code>{current_path}</code>\n\n"
            keyboard = []
            selected_files_list = user_input[user_id].get('selected_files', [])
            for file in paginated_files:
                file_path = os.path.join(current_path, file.filename)
                is_selected = file_path in selected_files_list
                icon = "📁" if file.st_mode & 0o040000 else "📄"
                button_label = "🟩" if is_selected else "🟥"
                callback_id = generate_callback_data(file_path)
                if file.st_mode & 0o040000:
                    keyboard.append([
                        InlineKeyboardButton(f"{icon} {file.filename}", callback_data=f"fm_nav_{server_id}_{callback_id}_0"),
                        InlineKeyboardButton(button_label, callback_data=f"fm_select_{server_id}_{callback_id}_{page}")
                    ])
                else:
                    keyboard.append([
                        InlineKeyboardButton(f"{icon} {file.filename}", callback_data=f"fm_action_{server_id}_{callback_id}_{page}"),
                        InlineKeyboardButton(button_label, callback_data=f"fm_select_{server_id}_{callback_id}_{page}")
                    ])
            navigation_buttons = []
            if page > 0:
                navigation_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"fm_page_{server_id}_{generate_callback_data(current_path)}_{page-1}"))
            if end_index < len(files):
                navigation_buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f"fm_page_{server_id}_{generate_callback_data(current_path)}_{page+1}"))
            if navigation_buttons:
                keyboard.append(navigation_buttons)
            keyboard.append([InlineKeyboardButton("✔️ Confirm Selections", callback_data=f"fm_confirm_{server_id}_{page}")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            text += "\n".join(f"{('📁' if f.st_mode & 0o040000 else '📄')} {f.filename}" for f in paginated_files)
            await callback.message.edit_text(text, parse_mode='HTML', reply_markup=reply_markup)
            sftp.close()
        except Exception as e:
            logger.error(f"List files error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_page_"))
    async def change_page(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            path_callback_id = parts[3]
            page = int(parts[4])
            current_path = path_mapping.get(path_callback_id)
            await list_files(callback, server_id, current_path, page)
        except Exception as e:
            logger.error(f"Change page error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_nav_"))
    async def navigate_directory(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            path_callback_id = parts[3]
            page = int(parts[4])
            new_path = path_mapping.get(path_callback_id)
            await list_files(callback, server_id, new_path, page)
        except Exception as e:
            logger.error(f"Navigate directory error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_action_"))
    async def file_action(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            file_callback_id = parts[3]
            page = int(parts[4])
            file_path = path_mapping.get(file_callback_id)
            current_path = user_input[callback.from_user.id]['current_path']
            file_name = os.path.basename(file_path)
            sftp = await get_sftp_client(server_id, active_sessions)
            file_stat = sftp.stat(file_path)
            is_dir = file_stat.st_mode & 0o040000
            sftp.close()
            await callback.message.edit_text(
                f"📄 <b>File: {file_name}</b>\nPath: <code>{current_path}</code>",
                parse_mode='HTML',
                reply_markup=get_file_action_keyboard(server_id, current_path, file_name, page, is_dir)
            )
        except Exception as e:
            logger.error(f"File action error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_select_"))
    async def toggle_selection(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            file_callback_id = parts[3]
            page = int(parts[4])
            file_path = path_mapping.get(file_callback_id)
            user_id = callback.from_user.id
            current_path = user_input[user_id]['current_path']
            file_name = os.path.basename(file_path)
            if 'selected_files' not in user_input[user_id]:
                user_input[user_id]['selected_files'] = []
            is_selected = file_path in user_input[user_id]['selected_files']
            if is_selected:
                user_input[user_id]['selected_files'].remove(file_path)
            else:
                user_input[user_id]['selected_files'].append(file_path)
            await callback.message.edit_text(
                f"📄 <b>File: {file_name}</b>\nPath: <code>{current_path}</code>",
                parse_mode='HTML',
                reply_markup=get_selection_keyboard(server_id, current_path, file_name, page, not is_selected)
            )
        except Exception as e:
            logger.error(f"Toggle selection error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_confirm_"))
    async def confirm_selection(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            page = int(parts[3])
            user_id = callback.from_user.id
            current_path = user_input[user_id]['current_path']
            selected_files_list = user_input[user_id].get('selected_files', [])
            if not selected_files_list:
                await callback.message.edit_text(
                    "❌ No files selected!",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"fm_list_{server_id}_{generate_callback_data(current_path)}_{page}")]])
                )
                return
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(InlineKeyboardButton("🗑 Delete Selected", callback_data=f"fm_delete_confirm_{server_id}_selected_{page}"))
            kb.add(InlineKeyboardButton("📦 Zip Selected", callback_data=f"fm_zip_{server_id}_{page}"))
            kb.add(InlineKeyboardButton("⬅️ Cancel", callback_data=f"fm_list_{server_id}_{generate_callback_data(current_path)}_{page}"))
            text = "✔️ <b>Selected Files:</b>\n" + "\n".join(os.path.basename(f) for f in selected_files_list)
            await callback.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
        except Exception as e:
            logger.error(f"Confirm selection error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_download_"))
    async def download_file(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            file_callback_id = parts[3]
            page = int(parts[4])
            file_path = path_mapping.get(file_callback_id)
            current_path = user_input[callback.from_user.id]['current_path']
            file_name = os.path.basename(file_path)
            sftp = await get_sftp_client(server_id, active_sessions)
            if sftp.stat(file_path).st_mode & 0o040000:
                # Zip directory before downloading
                zip_name = f"{file_name}.zip"
                zip_path = os.path.join(current_path, zip_name)
                with io.BytesIO() as zip_buffer:
                    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                        for root, dirs, files in sftp_walk(sftp, file_path):
                            for fname in files:
                                remote_path = os.path.join(root, fname)
                                arcname = os.path.relpath(remote_path, os.path.join(file_path, '..'))
                                with io.BytesIO() as file_buffer:
                                    sftp.getfo(remote_path, file_buffer)
                                    file_buffer.seek(0)
                                    zip_file.writestr(arcname, file_buffer.read())
                    zip_buffer.seek(0)
                    await callback.message.answer_document(types.InputFile(zip_buffer, filename=zip_name))
            else:
                with io.BytesIO() as file_buffer:
                    sftp.getfo(file_path, file_buffer)
                    file_buffer.seek(0)
                    await callback.message.answer_document(types.InputFile(file_buffer, filename=file_name))
            sftp.close()
            await callback.message.edit_text(
                f"📄 <b>File: {file_name}</b>\nDownloaded successfully.",
                parse_mode='HTML',
                reply_markup=get_file_action_keyboard(server_id, current_path, file_name, page, False)
            )
        except Exception as e:
            logger.error(f"Download file error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_upload_"))
    async def start_upload(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            page = int(parts[3])
            user_id = callback.from_user.id
            user_input[user_id]['action'] = 'upload'
            await callback.message.edit_text(
                "📤 Send the file to upload:",
                reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Cancel", callback_data=f"fm_list_{server_id}_{generate_callback_data(user_input[user_id]['current_path'])}_{page}"))
            )
        except Exception as e:
            logger.error(f"Start upload error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.message_handler(content_types=types.ContentType.DOCUMENT)
    async def handle_upload(message: types.Message):
        user_id = message.from_user.id
        if user_id not in user_input or user_input[user_id].get('action') != 'upload':
            return
        try:
            server_id = user_input[user_id]['server_id']
            current_path = user_input[user_id]['current_path']
            page = user_input[user_id]['page']
            file = await dp.bot.download_file_by_id(message.document.file_id)
            file_name = message.document.file_name
            file_path = os.path.join(current_path, file_name)
            sftp = await get_sftp_client(server_id, active_sessions)
            with io.BytesIO(file.read()) as file_buffer:
                sftp.putfo(file_buffer, file_path)
            sftp.close()
            user_input[user_id].pop('action', None)
            await message.answer("✅ File uploaded successfully!")
            await list_files(types.CallbackQuery(
                data=f"fm_list_{server_id}_{generate_callback_data(current_path)}_{page}",
                message=message,
                from_user=message.from_user
            ), server_id, current_path, page)
        except Exception as e:
            logger.error(f"Upload file error: {e}")
            await message.answer(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_zip_"))
    async def zip_files(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            page = int(parts[3])
            user_id = callback.from_user.id
            current_path = user_input[user_id]['current_path']
            selected_files_list = user_input[user_id].get('selected_files', [])
            if not selected_files_list:
                raise ValueError("No files selected")
            sftp = await get_sftp_client(server_id, active_sessions)
            zip_name = "archive.zip"
            zip_path = os.path.join(current_path, zip_name)
            with io.BytesIO() as zip_buffer:
                with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                    for file_path in selected_files_list:
                        file_name = os.path.basename(file_path)
                        if sftp.stat(file_path).st_mode & 0o040000:
                            for root, dirs, files in sftp_walk(sftp, file_path):
                                for fname in files:
                                    remote_path = os.path.join(root, fname)
                                    arcname = os.path.relpath(remote_path, os.path.join(file_path, '..'))
                                    with io.BytesIO() as file_buffer:
                                        sftp.getfo(remote_path, file_buffer)
                                        file_buffer.seek(0)
                                        zip_file.writestr(arcname, file_buffer.read())
                        else:
                            with io.BytesIO() as file_buffer:
                                sftp.getfo(file_path, file_buffer)
                                file_buffer.seek(0)
                                zip_file.writestr(file_name, file_buffer.read())
                zip_buffer.seek(0)
                sftp.putfo(zip_buffer, zip_path)
            sftp.close()
            user_input[user_id]['selected_files'] = []
            await callback.message.edit_text(
                f"📦 Created {zip_name} in {current_path}",
                parse_mode='HTML',
                reply_markup=get_file_manager_keyboard(server_id, current_path, page)
            )
        except Exception as e:
            logger.error(f"Zip files error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_unzip_"))
    async def unzip_file(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            file_callback_id = parts[3]
            page = int(parts[4])
            file_path = path_mapping.get(file_callback_id)
            user_id = callback.from_user.id
            current_path = user_input[user_id]['current_path']
            file_name = os.path.basename(file_path)
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
                reply_markup=get_file_action_keyboard(server_id, current_path, file_name, page, False)
            )
        except Exception as e:
            logger.error(f"Unzip file error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_delete_confirm_"))
    async def delete_confirm(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[3]
            file_callback_id = parts[4] if parts[4] != 'selected' else 'selected'
            page = int(parts[5])
            user_id = callback.from_user.id
            current_path = user_input[user_id]['current_path']
            files_to_delete = user_input[user_id].get('selected_files', []) if file_callback_id == 'selected' else [path_mapping.get(file_callback_id)]
            if not files_to_delete:
                raise ValueError("No files selected for deletion")
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(InlineKeyboardButton("✅ Confirm Delete", callback_data=f"fm_delete_final_{server_id}_{file_callback_id}_{page}"))
            kb.add(InlineKeyboardButton("⬅️ Cancel", callback_data=f"fm_list_{server_id}_{generate_callback_data(current_path)}_{page}"))
            text = f"⚠️ Are you sure you want to delete:\n" + "\n".join(os.path.basename(f) for f in files_to_delete)
            await callback.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
        except Exception as e:
            logger.error(f"Delete confirm error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_delete_final_"))
    async def delete_final(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[3]
            file_callback_id = parts[4]
            page = int(parts[5])
            user_id = callback.from_user.id
            current_path = user_input[user_id]['current_path']
            files_to_delete = user_input[user_id].get('selected_files', []) if file_callback_id == 'selected' else [path_mapping.get(file_callback_id)]
            sftp = await get_sftp_client(server_id, active_sessions)
            for file_path in files_to_delete:
                try:
                    if sftp.stat(file_path).st_mode & 0o040000:
                        sftp_rmtree(sftp, file_path)
                    else:
                        sftp.remove(file_path)
                except Exception as e:
                    logger.warning(f"Error deleting {file_path}: {e}")
            sftp.close()
            if file_callback_id == 'selected':
                user_input[user_id]['selected_files'] = []
            await callback.message.edit_text(
                f"🗑 Deleted {len(files_to_delete)} file(s)",
                parse_mode='HTML',
                reply_markup=get_file_manager_keyboard(server_id, current_path, page)
            )
        except Exception as e:
            logger.error(f"Delete final error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.callback_query_handler(lambda c: c.data.startswith("fm_rename_"))
    async def start_rename(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            file_callback_id = parts[3]
            page = int(parts[4])
            user_id = callback.from_user.id
            file_path = path_mapping.get(file_callback_id)
            file_name = os.path.basename(file_path)
            user_input[user_id]['action'] = 'rename'
            user_input[user_id]['file_path'] = file_path
            await callback.message.edit_text(
                f"✏️ Enter new name for {file_name}:",
                reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("❌ Cancel", callback_data=f"fm_list_{server_id}_{generate_callback_data(user_input[user_id]['current_path'])}_{page}"))
            )
        except Exception as e:
            logger.error(f"Start rename error: {e}")
            await callback.message.edit_text(f"❌ Error: {str(e)}")

    @dp.message_handler(lambda message: message.from_user.id in user_input and user_input[message.from_user.id].get('action') == 'rename')
    async def handle_rename(message: types.Message):
        user_id = message.from_user.id
        try:
            server_id = user_input[user_id]['server_id']
            current_path = user_input[user_id]['current_path']
            page = user_input[user_id]['page']
            old_path = user_input[user_id]['file_path']
            new_name = message.text
            new_path = os.path.join(current_path, new_name)
            sftp = await get_sftp_client(server_id, active_sessions)
            sftp.rename(old_path, new_path)
            sftp.close()
            user_input[user_id].pop('action', None)
            user_input[user_id].pop('file_path', None)
            await message.answer("✅ File renamed successfully!")
            await list_files(types.CallbackQuery(
                data=f"fm_list_{server_id}_{generate_callback_data(current_path)}_{page}",
                message=message,
                from_user=message.from_user
            ), server_id, current_path, page)
        except Exception as e:
            logger.error(f"Handle rename error: {e}")
            await message.answer(f"❌ Error: {str(e)}")

def sftp_walk(sftp: SFTPClient, path: str):
    """
    Walk through an SFTP directory recursively.
    """
    dirs = []
    files = []
    for entry in sftp.listdir_attr(path):
        if entry.st_mode & 0o040000:
            dirs.append(entry.filename)
        else:
            files.append(entry.filename)
    yield path, dirs, files
    for dir_name in dirs:
        new_path = os.path.join(path, dir_name)
        yield from sftp_walk(sftp, new_path)

def sftp_rmtree(sftp: SFTPClient, path: str):
    """
    Recursively delete a directory tree via SFTP.
    """
    for entry in sftp.listdir_attr(path):
        entry_path = os.path.join(path, entry.filename)
        if entry.st_mode & 0o040000:
            sftp_rmtree(sftp, entry_path)
        else:
            sftp.remove(entry_path)
    sftp.rmdir(path)
