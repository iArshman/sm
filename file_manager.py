import logging
import os
import zipfile
import tempfile
import shutil
from datetime import datetime
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import paramiko

logger = logging.getLogger(__name__)

# Global variables for file manager state
file_manager_state = {}
selected_files = {}

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
            file_manager_state[user_id]['page'] = 0
            
            # Clear selections
            if user_id in selected_files:
                selected_files[user_id] = []
            
            await show_file_manager(callback, server_id, file_manager_state[user_id]['current_path'])
            
        except Exception as e:
            logger.error(f"File manager main error: {e}")
            await callback.message.edit_text("❌ Error accessing file manager.")

    # --- SHOW FILE MANAGER ---
    async def show_file_manager(callback, server_id, path, page=0):
        try:
            user_id = callback.from_user.id
            
            # Get file listing
            all_files = await get_file_listing(server_id, path, active_sessions)
            
            if all_files is None:
                await callback.message.edit_text("❌ Error accessing directory.")
                return
            
            # Pagination logic
            items_per_page = 12
            total_files = len(all_files)
            start_idx = page * items_per_page
            end_idx = start_idx + items_per_page
            files = all_files[start_idx:end_idx]
            
            # Store current page
            file_manager_state[user_id]['page'] = page
            file_manager_state[user_id]['total_files'] = total_files
            
            # Create header buttons
            kb = InlineKeyboardMarkup(row_width=3)
            kb.add(
                InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}"),
                InlineKeyboardButton("📤 Upload", callback_data=f"fm_upload_{server_id}"),
                InlineKeyboardButton("📁 New Folder", callback_data=f"fm_newfolder_{server_id}")
            )
            
            # Add select/deselect all button
            selection_mode = file_manager_state.get(user_id, {}).get('selection_mode', False)
            if selection_mode:
                kb.add(InlineKeyboardButton("❌ Cancel Selection", callback_data=f"fm_cancel_select_{server_id}"))
            else:
                kb.add(InlineKeyboardButton("☑️ Select", callback_data=f"fm_select_mode_{server_id}"))
            
            # Add files and folders
            for file_info in files:
                if file_info['name'] == '..':
                    continue  # Skip parent directory here, we'll add it at bottom
                    
                icon = "📁" if file_info['type'] == 'directory' else "📄"
                name = file_info['name']
                
                # Show selection indicator
                if selection_mode and user_id in selected_files and name in selected_files[user_id]:
                    icon = "✅"
                
                # Truncate long names
                display_name = name[:25] + "..." if len(name) > 25 else name
                
                if selection_mode:
                    kb.add(InlineKeyboardButton(f"{icon} {display_name}", 
                                              callback_data=f"fm_toggle_{server_id}_{name}"))
                else:
                    if file_info['type'] == 'directory':
                        kb.add(InlineKeyboardButton(f"{icon} {display_name}", 
                                                  callback_data=f"fm_enter_{server_id}_{name}"))
                    else:
                        kb.add(InlineKeyboardButton(f"{icon} {display_name}", 
                                                  callback_data=f"fm_file_{server_id}_{name}"))
            
            # Show selected count and actions if in selection mode
            if selection_mode and user_id in selected_files and selected_files[user_id]:
                selected_count = len(selected_files[user_id])
                kb.add(InlineKeyboardButton(f"📋 Selected ({selected_count})", callback_data="fm_noop"))
                kb.add(
                    InlineKeyboardButton("📥 Download", callback_data=f"fm_download_selected_{server_id}"),
                    InlineKeyboardButton("🗑️ Delete", callback_data=f"fm_action_delete_{server_id}")
                )
                kb.add(
                    InlineKeyboardButton("📋 Copy", callback_data=f"fm_action_copy_{server_id}"),
                    InlineKeyboardButton("📁 Move", callback_data=f"fm_action_move_{server_id}")
                )
                kb.add(InlineKeyboardButton("🗜️ Zip", callback_data=f"fm_action_zip_{server_id}"))
            
            # Add pagination buttons if needed
            if total_files > items_per_page:
                pagination_buttons = []
                if page > 0:
                    pagination_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"fm_page_{server_id}_{page-1}"))
                if end_idx < total_files:
                    pagination_buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f"fm_page_{server_id}_{page+1}"))
                
                if pagination_buttons:
                    if len(pagination_buttons) == 2:
                        kb.add(pagination_buttons[0], pagination_buttons[1])
                    else:
                        kb.add(pagination_buttons[0])
            
            # Add parent directory button at bottom
            current_path = file_manager_state[user_id]['current_path']
            home_path = f"/home/{await get_current_user(server_id, active_sessions)}"
            if current_path != home_path and current_path != '/':
                kb.add(InlineKeyboardButton("📁 .. (Parent Directory)", callback_data=f"fm_parent_{server_id}"))
            
            # Path and pagination display
            path_display = path.replace('/home/', '~/')
            page_info = f" | Page {page + 1}/{((total_files - 1) // items_per_page) + 1}" if total_files > items_per_page else ""
            text = f"📂 <b>File Manager</b>\n📍 Path: <code>{path_display}</code>\n📊 Items: {total_files}{page_info}"
            
            await callback.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Show file manager error: {e}")
            await callback.message.edit_text("❌ Error displaying file manager.")

    # --- PAGINATION HANDLER ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_page_"))
    async def handle_pagination(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            page = int(parts[3])
            user_id = callback.from_user.id
            
            current_path = file_manager_state[user_id]['current_path']
            await show_file_manager(callback, server_id, current_path, page)
            
        except Exception as e:
            logger.error(f"Pagination error: {e}")

    # --- ENTER DIRECTORY ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_enter_"))
    async def enter_directory(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            folder_name = parts[3]
            user_id = callback.from_user.id
            
            current_path = file_manager_state[user_id]['current_path']
            new_path = os.path.join(current_path, folder_name).replace('\\', '/')
            file_manager_state[user_id]['current_path'] = new_path
            file_manager_state[user_id]['page'] = 0  # Reset to first page
            
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
            file_manager_state[user_id]['page'] = 0  # Reset to first page
            
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
            current_page = file_manager_state[user_id].get('page', 0)
            await show_file_manager(callback, server_id, current_path, current_page)
            
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
            current_page = file_manager_state[user_id].get('page', 0)
            await show_file_manager(callback, server_id, current_path, current_page)
            
        except Exception as e:
            logger.error(f"Cancel selection error: {e}")

    # --- TOGGLE FILE SELECTION ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_toggle_"))
    async def toggle_file_selection(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            file_name = parts[3]
            user_id = callback.from_user.id
            
            if user_id not in selected_files:
                selected_files[user_id] = []
            
            if file_name in selected_files[user_id]:
                selected_files[user_id].remove(file_name)
            else:
                selected_files[user_id].append(file_name)
            
            current_path = file_manager_state[user_id]['current_path']
            current_page = file_manager_state[user_id].get('page', 0)
            await show_file_manager(callback, server_id, current_path, current_page)
            
        except Exception as e:
            logger.error(f"Toggle selection error: {e}")

    # --- SINGLE FILE MENU ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_file_"))
    async def show_file_menu(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            file_name = parts[3]
            
            kb = InlineKeyboardMarkup(row_width=2)
            
            # Check if it's a zip file
            if file_name.lower().endswith(('.zip', '.tar', '.tar.gz', '.tgz')):
                kb.add(
                    InlineKeyboardButton("📥 Download", callback_data=f"fm_download_{server_id}_{file_name}"),
                    InlineKeyboardButton("📦 Unzip", callback_data=f"fm_unzip_{server_id}_{file_name}")
                )
                kb.add(
                    InlineKeyboardButton("✏️ Rename", callback_data=f"fm_rename_{server_id}_{file_name}"),
                    InlineKeyboardButton("🗑️ Delete", callback_data=f"fm_delete_single_{server_id}_{file_name}")
                )
                kb.add(
                    InlineKeyboardButton("📋 Copy", callback_data=f"fm_copy_single_{server_id}_{file_name}"),
                    InlineKeyboardButton("📁 Move", callback_data=f"fm_move_single_{server_id}_{file_name}")
                )
            else:
                kb.add(
                    InlineKeyboardButton("📥 Download", callback_data=f"fm_download_{server_id}_{file_name}"),
                    InlineKeyboardButton("🗜️ Zip", callback_data=f"fm_zip_single_{server_id}_{file_name}")
                )
                kb.add(
                    InlineKeyboardButton("✏️ Rename", callback_data=f"fm_rename_{server_id}_{file_name}"),
                    InlineKeyboardButton("🗑️ Delete", callback_data=f"fm_delete_single_{server_id}_{file_name}")
                )
                kb.add(
                    InlineKeyboardButton("📋 Copy", callback_data=f"fm_copy_single_{server_id}_{file_name}"),
                    InlineKeyboardButton("📁 Move", callback_data=f"fm_move_single_{server_id}_{file_name}")
                )
            
            kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"file_manager_{server_id}"))
            
            await callback.message.edit_text(
                f"📄 <b>{file_name}</b>\n\nChoose an action:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"File menu error: {e}")

    # --- DOWNLOAD HANDLERS ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_download_"))
    async def download_file(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            
            if parts[2] == "selected":
                # Download selected files
                server_id = parts[3]
                user_id = callback.from_user.id
                selected = selected_files.get(user_id, [])
                
                if not selected:
                    await callback.answer("❌ No files selected!")
                    return
                
                await callback.message.edit_text("📥 Preparing download...")
                
                # Create zip with selected files
                current_path = file_manager_state[user_id]['current_path']
                zip_name = f"selected_files_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
                
                success = await create_zip_from_selected(server_id, current_path, selected, zip_name, active_sessions)
                if success:
                    # Download the created zip
                    await download_single_file(callback, server_id, zip_name, bot, active_sessions, file_manager_state)
                else:
                    await callback.message.edit_text("❌ Failed to create archive.")
            else:
                # Download single file
                file_name = parts[3]
                await download_single_file(callback, server_id, file_name, bot, active_sessions, file_manager_state)
                
        except Exception as e:
            logger.error(f"Download error: {e}")
            await callback.message.edit_text("❌ Download failed.")

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
            
            await callback.message.edit_text(
                "📤 <b>Upload File</b>\n\nSend the file you want to upload:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Upload prompt error: {e}")

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
            
            await callback.message.edit_text(
                "📁 <b>Create New Folder</b>\n\nEnter folder name:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"New folder prompt error: {e}")

    # --- RENAME HANDLER ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_rename_"))
    async def rename_prompt(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            file_name = parts[3]
            user_id = callback.from_user.id
            
            user_input[user_id] = {
                'action': 'rename',
                'server_id': server_id,
                'path': file_manager_state[user_id]['current_path'],
                'old_name': file_name
            }
            
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"fm_file_{server_id}_{file_name}"))
            
            await callback.message.edit_text(
                f"✏️ <b>Rename</b>\n\nCurrent name: <code>{file_name}</code>\n\nEnter new name:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Rename prompt error: {e}")

    # --- DELETE CONFIRMATION ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_action_delete_") or c.data.startswith("fm_delete_single_"))
    async def delete_confirmation(callback: types.CallbackQuery):
        try:
            if callback.data.startswith("fm_action_delete_"):
                server_id = callback.data.split('_')[3]
                user_id = callback.from_user.id
                selected_count = len(selected_files.get(user_id, []))
                
                kb = InlineKeyboardMarkup(row_width=2)
                kb.add(
                    InlineKeyboardButton("✅ Yes, Delete", callback_data=f"fm_confirm_delete_{server_id}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"file_manager_{server_id}")
                )
                
                await callback.message.edit_text(
                    f"⚠️ <b>Confirm Deletion</b>\n\nAre you sure you want to delete {selected_count} selected items?\n\n<b>This action cannot be undone!</b>",
                    parse_mode='HTML',
                    reply_markup=kb
                )
            else:
                parts = callback.data.split('_', 4)
                server_id = parts[3]
                file_name = parts[4]
                
                kb = InlineKeyboardMarkup(row_width=2)
                kb.add(
                    InlineKeyboardButton("✅ Yes, Delete", callback_data=f"fm_confirm_delete_single_{server_id}_{file_name}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"fm_file_{server_id}_{file_name}")
                )
                
                await callback.message.edit_text(
                    f"⚠️ <b>Confirm Deletion</b>\n\nAre you sure you want to delete:\n<code>{file_name}</code>\n\n<b>This action cannot be undone!</b>",
                    parse_mode='HTML',
                    reply_markup=kb
                )
                
        except Exception as e:
            logger.error(f"Delete confirmation error: {e}")

    # --- CONFIRM DELETE ---
    @dp.callback_query_handler(lambda c: c.data.startswith("fm_confirm_delete_"))
    async def confirm_delete(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_')
            if len(parts) == 4:  # Multiple files
                server_id = parts[3]
                user_id = callback.from_user.id
                selected = selected_files.get(user_id, [])
                
                await callback.message.edit_text("🗑️ Deleting files...")
                
                success_count = 0
                for file_name in selected:
                    current_path = file_manager_state[user_id]['current_path']
                    if await delete_item(server_id, current_path, file_name, active_sessions):
                        success_count += 1
                
                # Clear selections and return to file manager
                selected_files[user_id] = []
                file_manager_state[user_id]['selection_mode'] = False
                
                result_text = f"✅ Deleted {success_count}/{len(selected)} items successfully!"
                await callback.message.edit_text(result_text)
                
                # Return to file manager after 2 seconds
                import asyncio
                await asyncio.sleep(2)
                current_path = file_manager_state[user_id]['current_path']
                await show_file_manager(callback, server_id, current_path)
                
            else:  # Single file
                server_id = parts[4]
                file_name = parts[5]
                user_id = callback.from_user.id
                
                await callback.message.edit_text("🗑️ Deleting...")
                
                current_path = file_manager_state[user_id]['current_path']
                success = await delete_item(server_id, current_path, file_name, active_sessions)
                
                if success:
                    await callback.message.edit_text("✅ File deleted successfully!")
                else:
                    await callback.message.edit_text("❌ Failed to delete file.")
                
                # Return to file manager after 2 seconds
                import asyncio
                await asyncio.sleep(2)
                await show_file_manager(callback, server_id, current_path)
                
        except Exception as e:
            logger.error(f"Confirm delete error: {e}")

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
            current_path = file_manager_state[user_id]['current_path']
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("📂 Back to File Manager", callback_data=f"file_manager_{server_id}"))
            await message.answer("Choose an option:", reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Handle text input error: {e}")
            await message.answer("❌ Error processing input.")

    # --- HANDLE FILE UPLOADS ---
    @dp.message_handler(content_types=types.ContentType.DOCUMENT)
    async def handle_file_upload(message: types.Message):
        try:
            user_id = message.from_user.id
            if user_id not in user_input or user_input[user_id].get('action') != 'upload':
                return
            
            data = user_input[user_id]
            server_id = data['server_id']
            
            await message.answer("📤 Uploading file...")
            
            # Download file from Telegram
            file_info = await bot.get_file(message.document.file_id)
            file_content = await bot.download_file(file_info.file_path)
            
            # Upload to server
            success = await upload_file(server_id, data['path'], message.document.file_name, file_content.read(), active_sessions)
            
            if success:
                await message.answer("✅ File uploaded successfully!")
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

async def download_single_file(callback, server_id, file_name, bot, active_sessions, file_manager_state):
    """Download a single file"""
    try:
        user_id = callback.from_user.id
        current_path = file_manager_state[user_id]['current_path']
        
        if server_id not in active_sessions:
            await callback.message.edit_text("❌ Server connection lost.")
            return
        
        ssh = active_sessions[server_id]
        sftp = ssh.open_sftp()
        
        remote_path = os.path.join(current_path, file_name).replace('\\', '/')
        
        # Check file size
        file_stat = sftp.stat(remote_path)
        file_size = file_stat.st_size
        
        # Telegram file size limit is 50MB
        if file_size > 50 * 1024 * 1024:
            await callback.message.edit_text("❌ File too large (>50MB). Please zip it first.")
            return
        
        with tempfile.NamedTemporaryFile() as temp_file:
            sftp.get(remote_path, temp_file.name)
            
            with open(temp_file.name, 'rb') as file_to_send:
                await bot.send_document(
                    user_id,
                    document=types.InputFile(file_to_send, filename=file_name),
                    caption=f"📥 Downloaded: {file_name}"
                )
        
        sftp.close()
        
        # Return to file manager
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("📂 Back to File Manager", callback_data=f"file_manager_{server_id}"))
        await callback.message.edit_text("✅ File downloaded successfully!", reply_markup=kb)
        
    except Exception as e:
        logger.error(f"Download file error: {e}")
        await callback.message.edit_text("❌ Download failed.")

async def delete_item(server_id, path, item_name, active_sessions):
    """Delete file or folder"""
    try:
        if server_id not in active_sessions:
            return False
        
        ssh = active_sessions[server_id]
        item_path = os.path.join(path, item_name).replace('\\', '/')
        
        # Use rm -rf to handle both files and directories
        command = f"rm -rf '{item_path}'"
        stdin, stdout, stderr = ssh.exec_command(command)
        error = stderr.read().decode().strip()
        
        return not error
        
    except Exception as e:
        logger.error(f"Delete item error: {e}")
        return False

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

async def create_zip_from_selected(server_id, path, selected_files, zip_name, active_sessions):
    """Create zip file from selected files"""
    try:
        if server_id not in active_sessions:
            return False
        
        ssh = active_sessions[server_id]
        zip_path = os.path.join(path, zip_name).replace('\\', '/')
        
        # Create file list for zip command
        file_list = " ".join([f"'{f}'" for f in selected_files])
        command = f"cd '{path}' && zip -r '{zip_name}' {file_list}"
        
        stdin, stdout, stderr = ssh.exec_command(command)
        error = stderr.read().decode().strip()
        
        return not error
        
    except Exception as e:
        logger.error(f"Create zip error: {e}")
        return False
