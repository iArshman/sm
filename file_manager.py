import logging
import os
import zipfile
import tarfile
import tempfile
import shutil
import hashlib
from datetime import datetime
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import paramiko

logger = logging.getLogger(__name__)

# Global variables for file manager state
file_manager_state = {}
selected_files = {}
file_name_cache = {}  # Cache for long filenames: {hash: filename}

def get_file_hash(filename):
    """Generate short hash for long filenames"""
    return hashlib.md5(filename.encode()).hexdigest()[:8]

def cache_filename(filename):
    """Cache filename and return hash if too long"""
    if len(filename) <= 30:  # Safe length for callback data
        return filename
    
    file_hash = get_file_hash(filename)
    file_name_cache[file_hash] = filename
    return file_hash

def get_cached_filename(identifier):
    """Get filename from cache or return identifier if not cached"""
    return file_name_cache.get(identifier, identifier)

def init_file_manager(dp, bot, active_sessions, user_input):
    """Initialize file manager handlers"""
    
    # --- FILE MANAGER MAIN ---
    @dp.callback_query_handler(lambda c: c.data.startswith("file_manager_"))
    async def file_manager_main(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            user_id = callback.from_user.id
            
            # Initialize user state
            if user_id not in file_manager_state:
                file_manager_state[user_id] = {}
            
            file_manager_state[user_id]['server_id'] = server_id
            file_manager_state[user_id]['current_path'] = f"/home/{await get_current_user(server_id, active_sessions)}"
            file_manager_state[user_id]['selection_mode'] = False
            file_manager_state[user_id]['operation'] = None
            
            # Clear selections
            if user_id in selected_files:
                selected_files[user_id] = []
            
            await show_file_manager(callback, server_id, file_manager_state[user_id]['current_path'])
            
        except Exception as e:
            logger.error(f"File manager main error: {e}")
            await callback.message.edit_text("❌ Error accessing file manager.")

    # --- SHOW FILE MANAGER ---
    async def show_file_manager(callback, server_id, path):
        try:
            user_id = callback.from_user.id
            
            # Get file listing
            files = await get_file_listing(server_id, path, active_sessions)
            
            if files is None:
                await callback.message.edit_text("❌ Error accessing directory.")
                return
            
            # Create header buttons
            kb = InlineKeyboardMarkup(row_width=3)
            kb.add(
                InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}"),
                InlineKeyboardButton("📤 Upload", callback_data=f"fm_upload_{server_id}"),
                InlineKeyboardButton("📁 New Folder", callback_data=f"fm_newfolder_{server_id}")
            )
            
            # Add select/deselect all button
            selection_mode = file_manager_state.get(user_id, {}).get('selection_mode', False)
            operation = file_manager_state.get(user_id, {}).get('operation')
            
            if operation in ['copy', 'move']:
                # Show operation buttons
                kb.add(
                    InlineKeyboardButton(f"📋 {operation.title()} Here", callback_data=f"fm_exec_{operation}_{server_id}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"fm_cancel_op_{server_id}")
                )
            elif selection_mode:
                kb.add(InlineKeyboardButton("❌ Cancel Selection", callback_data=f"fm_cancel_select_{server_id}"))
            else:
                kb.add(InlineKeyboardButton("☑️ Select", callback_data=f"fm_select_mode_{server_id}"))
            
            # Add files and folders
            for file_info in files:
                if file_info['name'] == '..':
                    continue  # Skip parent directory here, add at bottom
                
                icon = "📁" if file_info['type'] == 'directory' else "📄"
                name = file_info['name']
                
                # Show selection indicator
                if selection_mode and user_id in selected_files and name in selected_files[user_id]:
                    icon = "✅"
                
                # Truncate long names for display
                display_name = name[:25] + "..." if len(name) > 25 else name
                
                # Cache filename if too long for callback data
                cached_name = cache_filename(name)
                
                if selection_mode:
                    kb.add(InlineKeyboardButton(f"{icon} {display_name}", 
                                              callback_data=f"fm_toggle_{server_id}_{cached_name}"))
                else:
                    if file_info['type'] == 'directory':
                        kb.add(InlineKeyboardButton(f"{icon} {display_name}", 
                                                  callback_data=f"fm_enter_{server_id}_{cached_name}"))
                    else:
                        kb.add(InlineKeyboardButton(f"{icon} {display_name}", 
                                                  callback_data=f"fm_file_{server_id}_{cached_name}"))
            
            # Show selected count and actions if in selection mode
            if selection_mode and user_id in selected_files and selected_files[user_id]:
                selected_count = len(selected_files[user_id])
                kb.add(InlineKeyboardButton(f"📋 Selected ({selected_count})", callback_data="fm_noop"))
                kb.add(
                    InlineKeyboardButton("🔧 Actions", callback_data=f"fm_actions_{server_id}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"fm_cancel_select_{server_id}")
                )
            
            # Add parent directory button at bottom
            kb.add(InlineKeyboardButton("📁 .. (Parent Directory)", callback_data=f"fm_parent_{server_id}"))
            
            # Path display
            path_display = path.replace('/home/', '~/')
            if len(path_display) > 40:
                path_display = "..." + path_display[-37:]
            
            text = f"📂 <b>File Manager</b>\n📍 Path: <code>{path_display}</code>"
            
            if operation in ['copy', 'move']:
                text += f"\n\n🔄 <b>{operation.title()} Operation Active</b>\nNavigate to destination and click '{operation.title()} Here'"
            
            await callback.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Show file manager error: {e}")
            await callback.message.edit_text("❌ Error displaying file manager.")

    # --- ENTER DIRECTORY ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_enter_"))
    async def enter_directory(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            folder_identifier = parts[3]
            user_id = callback.from_user.id
            
            # Get actual folder name
            folder_name = get_cached_filename(folder_identifier)
            
            current_path = file_manager_state[user_id]['current_path']
            new_path = os.path.join(current_path, folder_name).replace('\\', '/')
            file_manager_state[user_id]['current_path'] = new_path
            
            await show_file_manager(callback, server_id, new_path)
            
        except Exception as e:
            logger.error(f"Enter directory error: {e}")
            await callback.message.edit_text("❌ Error entering directory.")

    # --- PARENT DIRECTORY ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_parent_"))
    async def parent_directory(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            user_id = callback.from_user.id
            
            current_path = file_manager_state[user_id]['current_path']
            parent_path = os.path.dirname(current_path)
            
            # Prevent going above home directory
            home_path = f"/home/{await get_current_user(server_id, active_sessions)}"
            if len(parent_path) < len(home_path):
                parent_path = home_path
                
            file_manager_state[user_id]['current_path'] = parent_path
            
            await show_file_manager(callback, server_id, parent_path)
            
        except Exception as e:
            logger.error(f"Parent directory error: {e}")
            await callback.message.edit_text("❌ Error navigating to parent directory.")

    # --- SELECTION MODE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_select_mode_"))
    async def toggle_selection_mode(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[3]
            user_id = callback.from_user.id
            
            file_manager_state[user_id]['selection_mode'] = True
            if user_id not in selected_files:
                selected_files[user_id] = []
            
            current_path = file_manager_state[user_id]['current_path']
            await show_file_manager(callback, server_id, current_path)
            
        except Exception as e:
            logger.error(f"Selection mode error: {e}")

    # --- CANCEL SELECTION ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_cancel_select_"))
    async def cancel_selection(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[3]
            user_id = callback.from_user.id
            
            file_manager_state[user_id]['selection_mode'] = False
            if user_id in selected_files:
                selected_files[user_id] = []
            
            current_path = file_manager_state[user_id]['current_path']
            await show_file_manager(callback, server_id, current_path)
            
        except Exception as e:
            logger.error(f"Cancel selection error: {e}")

    # --- CANCEL OPERATION ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_cancel_op_"))
    async def cancel_operation(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[3]
            user_id = callback.from_user.id
            
            file_manager_state[user_id]['operation'] = None
            file_manager_state[user_id]['operation_files'] = []
            file_manager_state[user_id]['operation_source'] = None
            
            current_path = file_manager_state[user_id]['current_path']
            await show_file_manager(callback, server_id, current_path)
            
        except Exception as e:
            logger.error(f"Cancel operation error: {e}")

    # --- TOGGLE FILE SELECTION ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_toggle_"))
    async def toggle_file_selection(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            file_identifier = parts[3]
            user_id = callback.from_user.id
            
            # Get actual filename
            file_name = get_cached_filename(file_identifier)
            
            if user_id not in selected_files:
                selected_files[user_id] = []
            
            if file_name in selected_files[user_id]:
                selected_files[user_id].remove(file_name)
            else:
                selected_files[user_id].append(file_name)
            
            current_path = file_manager_state[user_id]['current_path']
            await show_file_manager(callback, server_id, current_path)
            
        except Exception as e:
            logger.error(f"Toggle selection error: {e}")

    # --- FILE ACTIONS MENU ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_actions_"))
    async def show_actions_menu(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            user_id = callback.from_user.id
            
            selected_count = len(selected_files.get(user_id, []))
            
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("🗜️ Zip", callback_data=f"fm_action_zip_{server_id}"),
                InlineKeyboardButton("🗑️ Delete", callback_data=f"fm_action_delete_{server_id}")
            )
            kb.add(
                InlineKeyboardButton("📋 Copy", callback_data=f"fm_action_copy_{server_id}"),
                InlineKeyboardButton("📁 Move", callback_data=f"fm_action_move_{server_id}")
            )
            kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"fm_cancel_select_{server_id}"))
            
            await callback.message.edit_text(
                f"🔧 <b>Actions for {selected_count} selected items</b>",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Actions menu error: {e}")

    # --- SINGLE FILE MENU ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_file_"))
    async def show_file_menu(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            file_identifier = parts[3]
            
            # Get actual filename
            file_name = get_cached_filename(file_identifier)
            
            kb = InlineKeyboardMarkup(row_width=2)
            
            # Check if it's an archive file
            is_archive = file_name.lower().endswith(('.zip', '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tar.xz'))
            
            if is_archive:
                kb.add(
                    InlineKeyboardButton("📤 Download", callback_data=f"fm_download_{server_id}_{cache_filename(file_name)}"),
                    InlineKeyboardButton("📦 Extract", callback_data=f"fm_extract_{server_id}_{cache_filename(file_name)}")
                )
            else:
                kb.add(
                    InlineKeyboardButton("📤 Download", callback_data=f"fm_download_{server_id}_{cache_filename(file_name)}"),
                    InlineKeyboardButton("🗜️ Zip", callback_data=f"fm_zip_single_{server_id}_{cache_filename(file_name)}")
                )
            
            kb.add(
                InlineKeyboardButton("✏️ Rename", callback_data=f"fm_rename_{server_id}_{cache_filename(file_name)}"),
                InlineKeyboardButton("🗑️ Delete", callback_data=f"fm_delete_single_{server_id}_{cache_filename(file_name)}")
            )
            kb.add(
                InlineKeyboardButton("📋 Copy", callback_data=f"fm_copy_single_{server_id}_{cache_filename(file_name)}"),
                InlineKeyboardButton("📁 Move", callback_data=f"fm_move_single_{server_id}_{cache_filename(file_name)}")
            )
            
            kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"file_manager_{server_id}"))
            
            # Truncate filename for display
            display_name = file_name[:30] + "..." if len(file_name) > 30 else file_name
            
            await callback.message.edit_text(
                f"📄 <b>{display_name}</b>\n\nChoose an action:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"File menu error: {e}")

    # --- NEW FOLDER ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_newfolder_"))
    async def new_folder_prompt(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            user_id = callback.from_user.id
            
            user_input[user_id] = {
                'action': 'new_folder',
                'server_id': server_id,
                'path': file_manager_state[user_id]['current_path']
            }
            
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"file_manager_{server_id}"))
            
            await bot.send_message(
                user_id,
                "📁 <b>Create New Folder</b>\n\nEnter folder name:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"New folder prompt error: {e}")

    # --- RENAME PROMPT ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_rename_"))
    async def rename_prompt(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            file_identifier = parts[3]
            user_id = callback.from_user.id
            
            # Get actual filename
            file_name = get_cached_filename(file_identifier)
            
            user_input[user_id] = {
                'action': 'rename',
                'server_id': server_id,
                'path': file_manager_state[user_id]['current_path'],
                'old_name': file_name
            }
            
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"fm_file_{server_id}_{cache_filename(file_name)}"))
            
            await bot.send_message(
                user_id,
                f"✏️ <b>Rename File</b>\n\nCurrent name: <code>{file_name}</code>\n\nEnter new name:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Rename prompt error: {e}")

    # --- DOWNLOAD FILE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_download_"))
    async def download_file(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            file_identifier = parts[3]
            user_id = callback.from_user.id
            
            # Get actual filename
            file_name = get_cached_filename(file_identifier)
            
            current_path = file_manager_state[user_id]['current_path']
            
            await callback.message.edit_text("📤 <b>Downloading file...</b>", parse_mode='HTML')
            
            # Download file from server
            file_content = await download_file_from_server(server_id, current_path, file_name, active_sessions)
            
            if file_content:
                # Check file size (Telegram limit is 50MB)
                if len(file_content) > 50 * 1024 * 1024:
                    await callback.message.edit_text("❌ <b>File too large for Telegram (>50MB)</b>", parse_mode='HTML')
                    return
                
                # Send file to user
                with tempfile.NamedTemporaryFile() as temp_file:
                    temp_file.write(file_content)
                    temp_file.flush()
                    
                    with open(temp_file.name, 'rb') as f:
                        await bot.send_document(
                            user_id,
                            types.InputFile(f, filename=file_name),
                            caption=f"📄 <b>{file_name}</b>",
                            parse_mode='HTML'
                        )
                
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("📂 Back to File Manager", callback_data=f"file_manager_{server_id}"))
                await callback.message.edit_text("✅ <b>File downloaded successfully!</b>", parse_mode='HTML', reply_markup=kb)
            else:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("📂 Back to File Manager", callback_data=f"file_manager_{server_id}"))
                await callback.message.edit_text("❌ <b>Failed to download file</b>", parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Download file error: {e}")
            await callback.message.edit_text("❌ Error downloading file.")

    # --- ZIP OPERATIONS ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_zip_single_") or c.data.startswith("fm_action_zip_"))
    async def zip_files(callback: types.CallbackQuery):
        try:
            if callback.data.startswith("fm_zip_single_"):
                parts = callback.data.split('_', 4)
                server_id = parts[3]
                file_identifier = parts[4]
                file_name = get_cached_filename(file_identifier)
                files_to_zip = [file_name]
            else:
                server_id = callback.data.split('_')[3]
                user_id = callback.from_user.id
                files_to_zip = selected_files.get(user_id, [])
            
            if not files_to_zip:
                await callback.message.edit_text("❌ No files selected for zipping.")
                return
            
            user_id = callback.from_user.id
            current_path = file_manager_state[user_id]['current_path']
            
            await callback.message.edit_text("🗜️ <b>Creating zip archive...</b>", parse_mode='HTML')
            
            # Create zip file on server
            zip_name = f"archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            success = await create_zip_on_server(server_id, current_path, files_to_zip, zip_name, active_sessions)
            
            if success:
                # Clear selection if it was bulk operation
                if user_id in selected_files:
                    selected_files[user_id] = []
                    file_manager_state[user_id]['selection_mode'] = False
                
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("📂 Back to File Manager", callback_data=f"file_manager_{server_id}"))
                await callback.message.edit_text(
                    f"✅ <b>Zip created successfully!</b>\n\nFile: <code>{zip_name}</code>",
                    parse_mode='HTML',
                    reply_markup=kb
                )
            else:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("📂 Back to File Manager", callback_data=f"file_manager_{server_id}"))
                await callback.message.edit_text("❌ <b>Failed to create zip archive</b>", parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Zip files error: {e}")
            await callback.message.edit_text("❌ Error creating zip archive.")

    # --- EXTRACT OPERATION ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_extract_"))
    async def extract_file(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            file_identifier = parts[3]
            user_id = callback.from_user.id
            
            # Get actual filename
            file_name = get_cached_filename(file_identifier)
            
            current_path = file_manager_state[user_id]['current_path']
            
            await callback.message.edit_text("📦 <b>Extracting archive...</b>", parse_mode='HTML')
            
            # Extract archive on server
            success = await extract_archive_on_server(server_id, current_path, file_name, active_sessions)
            
            if success:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("📂 Back to File Manager", callback_data=f"file_manager_{server_id}"))
                await callback.message.edit_text(
                    f"✅ <b>Archive extracted successfully!</b>\n\nFile: <code>{file_name}</code>",
                    parse_mode='HTML',
                    reply_markup=kb
                )
            else:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("📂 Back to File Manager", callback_data=f"file_manager_{server_id}"))
                await callback.message.edit_text("❌ <b>Failed to extract archive</b>", parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Extract file error: {e}")
            await callback.message.edit_text("❌ Error extracting archive.")

    # --- COPY OPERATIONS ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_copy_single_") or c.data.startswith("fm_action_copy_"))
    async def copy_files_start(callback: types.CallbackQuery):
        try:
            if callback.data.startswith("fm_copy_single_"):
                parts = callback.data.split('_', 4)
                server_id = parts[3]
                file_identifier = parts[4]
                file_name = get_cached_filename(file_identifier)
                files_to_copy = [file_name]
            else:
                server_id = callback.data.split('_')[3]
                user_id = callback.from_user.id
                files_to_copy = selected_files.get(user_id, [])
            
            if not files_to_copy:
                await callback.message.edit_text("❌ No files selected for copying.")
                return
            
            user_id = callback.from_user.id
            
            # Set operation state
            file_manager_state[user_id]['operation'] = 'copy'
            file_manager_state[user_id]['operation_files'] = files_to_copy
            file_manager_state[user_id]['operation_source'] = file_manager_state[user_id]['current_path']
            file_manager_state[user_id]['selection_mode'] = False
            
            current_path = file_manager_state[user_id]['current_path']
            await show_file_manager(callback, server_id, current_path)
            
        except Exception as e:
            logger.error(f"Copy files start error: {e}")

    # --- MOVE OPERATIONS ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_move_single_") or c.data.startswith("fm_action_move_"))
    async def move_files_start(callback: types.CallbackQuery):
        try:
            if callback.data.startswith("fm_move_single_"):
                parts = callback.data.split('_', 4)
                server_id = parts[3]
                file_identifier = parts[4]
                file_name = get_cached_filename(file_identifier)
                files_to_move = [file_name]
            else:
                server_id = callback.data.split('_')[3]
                user_id = callback.from_user.id
                files_to_move = selected_files.get(user_id, [])
            
            if not files_to_move:
                await callback.message.edit_text("❌ No files selected for moving.")
                return
            
            user_id = callback.from_user.id
            
            # Set operation state
            file_manager_state[user_id]['operation'] = 'move'
            file_manager_state[user_id]['operation_files'] = files_to_move
            file_manager_state[user_id]['operation_source'] = file_manager_state[user_id]['current_path']
            file_manager_state[user_id]['selection_mode'] = False
            
            current_path = file_manager_state[user_id]['current_path']
            await show_file_manager(callback, server_id, current_path)
            
        except Exception as e:
            logger.error(f"Move files start error: {e}")

    # --- EXECUTE COPY/MOVE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_exec_"))
    async def execute_operation(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            operation = parts[2]  # copy or move
            server_id = parts[3]
            user_id = callback.from_user.id
            
            source_path = file_manager_state[user_id]['operation_source']
            dest_path = file_manager_state[user_id]['current_path']
            files = file_manager_state[user_id]['operation_files']
            
            if source_path == dest_path:
                await callback.message.edit_text("❌ Source and destination are the same!")
                return
            
            await callback.message.edit_text(f"🔄 <b>{operation.title()}ing files...</b>", parse_mode='HTML')
            
            if operation == 'copy':
                success = await copy_files_on_server(server_id, source_path, files, dest_path, active_sessions)
            else:  # move
                success = await move_files_on_server(server_id, source_path, files, dest_path, active_sessions)
            
            # Clear operation state
            file_manager_state[user_id]['operation'] = None
            file_manager_state[user_id]['operation_files'] = []
            file_manager_state[user_id]['operation_source'] = None
            
            if user_id in selected_files:
                selected_files[user_id] = []
            
            if success:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("📂 Back to File Manager", callback_data=f"file_manager_{server_id}"))
                await callback.message.edit_text(
                    f"✅ <b>Files {operation}d successfully!</b>\n\n{operation.title()}d {len(files)} items.",
                    parse_mode='HTML',
                    reply_markup=kb
                )
            else:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("📂 Back to File Manager", callback_data=f"file_manager_{server_id}"))
                await callback.message.edit_text(f"❌ <b>Failed to {operation} files</b>", parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Execute operation error: {e}")
            await callback.message.edit_text(f"❌ Error executing {operation} operation.")

    # --- DELETE CONFIRMATION ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_action_delete_") or c.data.startswith("fm_delete_single_"))
    async def delete_confirmation(callback: types.CallbackQuery):
        try:
            if callback.data.startswith("fm_action_delete_"):
                server_id = callback.data.split('_')[3]
                user_id = callback.from_user.id
                files_to_delete = selected_files.get(user_id, [])
                
                kb = InlineKeyboardMarkup(row_width=2)
                kb.add(
                    InlineKeyboardButton("✅ Yes, Delete", callback_data=f"fm_confirm_delete_{server_id}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"fm_actions_{server_id}")
                )
                
                await callback.message.edit_text(
                    f"⚠️ <b>Confirm Deletion</b>\n\nAre you sure you want to delete {len(files_to_delete)} selected items?\n\n<b>This action cannot be undone!</b>",
                    parse_mode='HTML',
                    reply_markup=kb
                )
            else:
                parts = callback.data.split('_', 4)
                server_id = parts[3]
                file_identifier = parts[4]
                file_name = get_cached_filename(file_identifier)
                
                kb = InlineKeyboardMarkup(row_width=2)
                kb.add(
                    InlineKeyboardButton("✅ Yes, Delete", callback_data=f"fm_confirm_delete_single_{server_id}_{cache_filename(file_name)}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"fm_file_{server_id}_{cache_filename(file_name)}")
                )
                
                display_name = file_name[:30] + "..." if len(file_name) > 30 else file_name
                await callback.message.edit_text(
                    f"⚠️ <b>Confirm Deletion</b>\n\nAre you sure you want to delete:\n<code>{display_name}</code>\n\n<b>This action cannot be undone!</b>",
                    parse_mode='HTML',
                    reply_markup=kb
                )
                
        except Exception as e:
            logger.error(f"Delete confirmation error: {e}")

    # --- CONFIRM DELETE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_confirm_delete_"))
    async def confirm_delete(callback: types.CallbackQuery):
        try:
            if callback.data.startswith("fm_confirm_delete_single_"):
                parts = callback.data.split('_', 5)
                server_id = parts[4]
                file_identifier = parts[5]
                file_name = get_cached_filename(file_identifier)
                files_to_delete = [file_name]
            else:
                server_id = callback.data.split('_')[3]
                user_id = callback.from_user.id
                files_to_delete = selected_files.get(user_id, [])
            
            if not files_to_delete:
                await callback.message.edit_text("❌ No files selected for deletion.")
                return
            
            user_id = callback.from_user.id
            current_path = file_manager_state[user_id]['current_path']
            
            await callback.message.edit_text("🗑️ <b>Deleting files...</b>", parse_mode='HTML')
            
            # Delete files on server
            success = await delete_files_on_server(server_id, current_path, files_to_delete, active_sessions)
            
            if success:
                # Clear selection if it was bulk operation
                if user_id in selected_files:
                    selected_files[user_id] = []
                    file_manager_state[user_id]['selection_mode'] = False
                
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("📂 Back to File Manager", callback_data=f"file_manager_{server_id}"))
                await callback.message.edit_text(
                    f"✅ <b>Files deleted successfully!</b>\n\nDeleted {len(files_to_delete)} items.",
                    parse_mode='HTML',
                    reply_markup=kb
                )
            else:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("📂 Back to File Manager", callback_data=f"file_manager_{server_id}"))
                await callback.message.edit_text("❌ <b>Failed to delete some files</b>", parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Confirm delete error: {e}")
            await callback.message.edit_text("❌ Error deleting files.")

    # --- UPLOAD HANDLER ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_upload_"))
    async def upload_prompt(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            user_id = callback.from_user.id
            
            user_input[user_id] = {
                'action': 'upload',
                'server_id': server_id,
                'path': file_manager_state[user_id]['current_path']
            }
            
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"file_manager_{server_id}"))
            
            await bot.send_message(
                user_id,
                "📤 <b>Upload File</b>\n\nSend any file you want to upload to the server:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Upload prompt error: {e}")

    # --- HANDLE TEXT INPUTS ---
    @dp.message_handler(lambda message: message.from_user.id in user_input and user_input[message.from_user.id].get('action') in ['new_folder', 'rename'])
    async def handle_text_input(message: types.Message):
        try:
            user_id = message.from_user.id
            data = user_input[user_id]
            action = data['action']
            server_id = data['server_id']
            
            if action == 'new_folder':
                folder_name = message.text.strip()
                if not folder_name or '/' in folder_name or folder_name in ['.', '..']:
                    await message.answer("❌ Invalid folder name. Please try again.")
                    return
                
                success = await create_folder(server_id, data['path'], folder_name, active_sessions)
                if success:
                    await message.answer("✅ Folder created successfully!")
                else:
                    await message.answer("❌ Failed to create folder.")
                    
            elif action == 'rename':
                new_name = message.text.strip()
                if not new_name or '/' in new_name or new_name in ['.', '..']:
                    await message.answer("❌ Invalid name. Please try again.")
                    return
                
                success = await rename_item(server_id, data['path'], data['old_name'], new_name, active_sessions)
                if success:
                    await message.answer("✅ Renamed successfully!")
                else:
                    await message.answer("❌ Failed to rename.")
            
            user_input.pop(user_id, None)
            
            # Return to file manager
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("📂 Back to File Manager", callback_data=f"file_manager_{server_id}"))
            await message.answer("Choose an option:", reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Handle text input error: {e}")
            await message.answer("❌ Error processing input.")

    # --- HANDLE FILE UPLOADS ---
    @dp.message_handler(content_types=[types.ContentType.DOCUMENT, types.ContentType.PHOTO, types.ContentType.VIDEO, types.ContentType.AUDIO, types.ContentType.VOICE, types.ContentType.VIDEO_NOTE, types.ContentType.STICKER])
    async def handle_file_upload(message: types.Message):
        try:
            user_id = message.from_user.id
            if user_id not in user_input or user_input[user_id].get('action') != 'upload':
                return
            
            data = user_input[user_id]
            server_id = data['server_id']
            
            await message.answer("📤 <b>Uploading file...</b>", parse_mode='HTML')
            
            # Handle different file types
            file_obj = None
            filename = None
            
            if message.document:
                file_obj = message.document
                filename = message.document.file_name or f"document_{message.document.file_id}"
            elif message.photo:
                file_obj = message.photo[-1]  # Get highest resolution
                filename = f"photo_{message.photo[-1].file_id}.jpg"
            elif message.video:
                file_obj = message.video
                filename = message.video.file_name or f"video_{message.video.file_id}.mp4"
            elif message.audio:
                file_obj = message.audio
                filename = message.audio.file_name or f"audio_{message.audio.file_id}.mp3"
            elif message.voice:
                file_obj = message.voice
                filename = f"voice_{message.voice.file_id}.ogg"
            elif message.video_note:
                file_obj = message.video_note
                filename = f"video_note_{message.video_note.file_id}.mp4"
            elif message.sticker:
                file_obj = message.sticker
                filename = f"sticker_{message.sticker.file_id}.webp"
            
            if not file_obj:
                await message.answer("❌ Unsupported file type.")
                return
            
            # Check file size
            if hasattr(file_obj, 'file_size') and file_obj.file_size > 50 * 1024 * 1024:
                await message.answer("❌ File too large (>50MB).")
                return
            
            # Download file from Telegram
            file = await bot.download_file_by_id(file_obj.file_id)
            file_content = file.read()
            
            # Upload to server
            success = await upload_file(server_id, data['path'], filename, file_content, active_sessions)
            
            if success:
                await message.answer(f"✅ <b>File uploaded successfully!</b>\n\nFilename: <code>{filename}</code>", parse_mode='HTML')
            else:
                await message.answer("❌ Failed to upload file.")
            
            user_input.pop(user_id, None)
            
            # Return to file manager
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("📂 Back to File Manager", callback_data=f"file_manager_{server_id}"))
            await message.answer("Choose an option:", reply_markup=kb)
            
        except Exception as e:
            logger.error(f"File upload error: {e}")
            await message.answer("❌ Error uploading file.")

    # --- NO-OP HANDLER ---
    @dp.callback_query_handler(lambda c: c.data == "fm_noop")
    async def noop_handler(callback: types.CallbackQuery):
        await callback.answer()

# --- HELPER FUNCTIONS ---

async def get_current_user(server_id, active_sessions):
    """Get current username for the server"""
    try:
        from db import get_server_by_id
        server = await get_server_by_id(server_id)
        return server['username'] if server else 'user'
    except:
        return 'user'

async def get_file_listing(server_id, path, active_sessions):
    """Get file listing from remote server"""
    try:
        if server_id not in active_sessions:
            return None
        
        ssh = active_sessions[server_id]
        
        # Execute ls command with detailed info
        command = f"ls -la '{path}' 2>/dev/null"
        stdin, stdout, stderr = ssh.exec_command(command)
        output = stdout.read().decode().strip()
        error = stderr.read().decode().strip()
        
        if error:
            logger.error(f"ls command error: {error}")
            return None
        
        files = []
        lines = output.split('\n')[1:]  # Skip total line
        
        for line in lines:
            if not line.strip():
                continue
                
            parts = line.split()
            if len(parts) < 9:
                continue
            
            permissions = parts[0]
            name = ' '.join(parts[8:])
            
            if name in ['.']:
                continue
            
            file_type = 'directory' if permissions.startswith('d') else 'file'
            files.append({
                'name': name,
                'type': file_type,
                'permissions': permissions,
                'size': parts[4] if file_type == 'file' else None
            })
        
        # Sort: directories first, then files
        files.sort(key=lambda x: (x['type'] == 'file', x['name'].lower()))
        
        return files
        
    except Exception as e:
        logger.error(f"Get file listing error: {e}")
        return None

async def create_folder(server_id, path, folder_name, active_sessions):
    """Create a new folder"""
    try:
        if server_id not in active_sessions:
            return False
        
        ssh = active_sessions[server_id]
        folder_path = os.path.join(path, folder_name).replace('\\', '/')
        
        command = f"mkdir '{folder_path}'"
        stdin, stdout, stderr = ssh.exec_command(command)
        error = stderr.read().decode().strip()
        
        return not error
        
    except Exception as e:
        logger.error(f"Create folder error: {e}")
        return False

async def upload_file(server_id, path, filename, content, active_sessions):
    """Upload file to server"""
    try:
        if server_id not in active_sessions:
            return False
        
        ssh = active_sessions[server_id]
        sftp = ssh.open_sftp()
        
        remote_path = os.path.join(path, filename).replace('\\', '/')
        
        with tempfile.NamedTemporaryFile() as temp_file:
            temp_file.write(content)
            temp_file.flush()
            sftp.put(temp_file.name, remote_path)
        
        sftp.close()
        return True
        
    except Exception as e:
        logger.error(f"Upload file error: {e}")
        return False

async def download_file_from_server(server_id, path, filename, active_sessions):
    """Download file from server"""
    try:
        if server_id not in active_sessions:
            return None
        
        ssh = active_sessions[server_id]
        sftp = ssh.open_sftp()
        
        remote_path = os.path.join(path, filename).replace('\\', '/')
        
        with tempfile.NamedTemporaryFile() as temp_file:
            sftp.get(remote_path, temp_file.name)
            temp_file.seek(0)
            content = temp_file.read()
        
        sftp.close()
        return content
        
    except Exception as e:
        logger.error(f"Download file error: {e}")
        return None

async def rename_item(server_id, path, old_name, new_name, active_sessions):
    """Rename file or folder"""
    try:
        if server_id not in active_sessions:
            return False
        
        ssh = active_sessions[server_id]
        old_path = os.path.join(path, old_name).replace('\\', '/')
        new_path = os.path.join(path, new_name).replace('\\', '/')
        
        command = f"mv '{old_path}' '{new_path}'"
        stdin, stdout, stderr = ssh.exec_command(command)
        error = stderr.read().decode().strip()
        
        return not error
        
    except Exception as e:
        logger.error(f"Rename item error: {e}")
        return False

async def delete_files_on_server(server_id, path, filenames, active_sessions):
    """Delete files on server"""
    try:
        if server_id not in active_sessions:
            return False
        
        ssh = active_sessions[server_id]
        
        for filename in filenames:
            file_path = os.path.join(path, filename).replace('\\', '/')
            command = f"rm -rf '{file_path}'"
            stdin, stdout, stderr = ssh.exec_command(command)
            error = stderr.read().decode().strip()
            
            if error:
                logger.error(f"Delete error for {filename}: {error}")
                return False
        
        return True
        
    except Exception as e:
        logger.error(f"Delete files error: {e}")
        return False

async def create_zip_on_server(server_id, path, filenames, zip_name, active_sessions):
    """Create zip archive on server"""
    try:
        if server_id not in active_sessions:
            return False
        
        ssh = active_sessions[server_id]
        
        # Create list of files to zip
        files_str = ' '.join([f"'{f}'" for f in filenames])
        zip_path = os.path.join(path, zip_name).replace('\\', '/')
        
        command = f"cd '{path}' && zip -r '{zip_name}' {files_str}"
        stdin, stdout, stderr = ssh.exec_command(command)
        stdout_output = stdout.read().decode()
        error = stderr.read().decode().strip()
        
        # Check if zip command succeeded
        if "adding:" in stdout_output or not error:
            return True
        
        logger.error(f"Zip creation error: {error}")
        return False
        
    except Exception as e:
        logger.error(f"Create zip error: {e}")
        return False

async def extract_archive_on_server(server_id, path, archive_filename, active_sessions):
    """Extract archive on server"""
    try:
        if server_id not in active_sessions:
            return False
        
        ssh = active_sessions[server_id]
        
        archive_path = os.path.join(path, archive_filename).replace('\\', '/')
        
        # Create extraction directory
        extract_dir = os.path.splitext(archive_filename)[0]
        if extract_dir.endswith('.tar'):
            extract_dir = os.path.splitext(extract_dir)[0]
        
        extract_path = os.path.join(path, extract_dir).replace('\\', '/')
        
        # Determine archive type and extract
        if archive_filename.lower().endswith('.zip'):
            command = f"cd '{path}' && mkdir -p '{extract_dir}' && unzip '{archive_filename}' -d '{extract_dir}'"
        elif archive_filename.lower().endswith(('.tar.gz', '.tgz')):
            command = f"cd '{path}' && mkdir -p '{extract_dir}' && tar -xzf '{archive_filename}' -C '{extract_dir}'"
        elif archive_filename.lower().endswith(('.tar.bz2', '.tbz2')):
            command = f"cd '{path}' && mkdir -p '{extract_dir}' && tar -xjf '{archive_filename}' -C '{extract_dir}'"
        elif archive_filename.lower().endswith(('.tar.xz', '.txz')):
            command = f"cd '{path}' && mkdir -p '{extract_dir}' && tar -xJf '{archive_filename}' -C '{extract_dir}'"
        elif archive_filename.lower().endswith('.tar'):
            command = f"cd '{path}' && mkdir -p '{extract_dir}' && tar -xf '{archive_filename}' -C '{extract_dir}'"
        else:
            return False
        
        stdin, stdout, stderr = ssh.exec_command(command)
        stdout_output = stdout.read().decode()
        error = stderr.read().decode().strip()
        
        # Check if extraction succeeded
        if not error or "inflating:" in stdout_output or "extracting:" in stdout_output:
            return True
        
        logger.error(f"Extract error: {error}")
        return False
        
    except Exception as e:
        logger.error(f"Extract archive error: {e}")
        return False

async def copy_files_on_server(server_id, source_path, filenames, dest_path, active_sessions):
    """Copy files on server"""
    try:
        if server_id not in active_sessions:
            return False
        
        ssh = active_sessions[server_id]
        
        # Create destination directory if it doesn't exist
        command = f"mkdir -p '{dest_path}'"
        stdin, stdout, stderr = ssh.exec_command(command)
        
        # Copy each file
        for filename in filenames:
            source_file = os.path.join(source_path, filename).replace('\\', '/')
            dest_file = os.path.join(dest_path, filename).replace('\\', '/')
            
            command = f"cp -r '{source_file}' '{dest_file}'"
            stdin, stdout, stderr = ssh.exec_command(command)
            error = stderr.read().decode().strip()
            
            if error:
                logger.error(f"Copy error for {filename}: {error}")
                return False
        
        return True
        
    except Exception as e:
        logger.error(f"Copy files error: {e}")
        return False

async def move_files_on_server(server_id, source_path, filenames, dest_path, active_sessions):
    """Move files on server"""
    try:
        if server_id not in active_sessions:
            return False
        
        ssh = active_sessions[server_id]
        
        # Create destination directory if it doesn't exist
        command = f"mkdir -p '{dest_path}'"
        stdin, stdout, stderr = ssh.exec_command(command)
        
        # Move each file
        for filename in filenames:
            source_file = os.path.join(source_path, filename).replace('\\', '/')
            dest_file = os.path.join(dest_path, filename).replace('\\', '/')
            
            command = f"mv '{source_file}' '{dest_file}'"
            stdin, stdout, stderr = ssh.exec_command(command)
            error = stderr.read().decode().strip()
            
            if error:
                logger.error(f"Move error for {filename}: {error}")
                return False
        
        return True
        
    except Exception as e:
        logger.error(f"Move files error: {e}")
        return False
