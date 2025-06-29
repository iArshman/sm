import logging
import asyncio
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# Bot management state
bot_states = {}
managed_bots = {}  # Store manually added bots: {server_id: [bot_list]}

def init_bot_manager(dp, bot, active_sessions, user_input):
    """Initialize bot manager handlers"""
    
    # --- UTILITY FUNCTIONS ---
    
    def create_bot_keyboard(bots, server_id):
        """Create keyboard for bot list"""
        kb = InlineKeyboardMarkup(row_width=1)
        
        for bot_info in bots:
            status_icon = "🟢" if bot_info['status'] == 'running' else "🔴"
            kb.add(InlineKeyboardButton(
                f"{status_icon} {bot_info['name']} ({bot_info['type']})",
                callback_data=f"bot_detail_{server_id}_{bot_info['id']}"
            ))
        
        kb.add(
            InlineKeyboardButton("➕ Add Bot", callback_data=f"add_bot_{server_id}")       
        )
        kb.add(InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}"))
        
        return kb
    
    def create_bot_detail_keyboard(server_id, bot_id, bot_status):
        """Create keyboard for individual bot management"""
        kb = InlineKeyboardMarkup(row_width=2)
        
        if bot_status == 'running':
            kb.add(
                InlineKeyboardButton("⏹️ Stop", callback_data=f"bot_stop_{server_id}_{bot_id}"),
                InlineKeyboardButton("🔄 Restart", callback_data=f"bot_restart_{server_id}_{bot_id}")
            )
        else:
            kb.add(
                InlineKeyboardButton("▶️ Start", callback_data=f"bot_start_{server_id}_{bot_id}"),
                InlineKeyboardButton("🔄 Restart", callback_data=f"bot_restart_{server_id}_{bot_id}")
            )
        
        kb.add(
            InlineKeyboardButton("📊 Logs", callback_data=f"bot_logs_{server_id}_{bot_id}"),
            InlineKeyboardButton("⚙️ Settings", callback_data=f"bot_settings_{server_id}_{bot_id}")
        )
        kb.add(
            InlineKeyboardButton("🗑️ Remove", callback_data=f"bot_remove_{server_id}_{bot_id}"),
            InlineKeyboardButton("⬅️ Back to Bots", callback_data=f"bot_manager_{server_id}")
        )
        
        return kb
    
    async def get_ssh_session(server_id):
        """Get SSH session for server"""
        from db import get_server_by_id
        
        server = await get_server_by_id(server_id)
        if not server:
            return None
        
        # Use the session from active_sessions if available
        if server_id in active_sessions:
            return active_sessions[server_id]
        
        return None
    
    def get_managed_bots(server_id):
        """Get manually managed bots for server"""
        return managed_bots.get(server_id, [])
    
    def add_managed_bot(server_id, bot_info):
        """Add a bot to managed list"""
        if server_id not in managed_bots:
            managed_bots[server_id] = []
        
        # Check if bot already exists
        for existing_bot in managed_bots[server_id]:
            if existing_bot['id'] == bot_info['id']:
                return False
        
        managed_bots[server_id].append(bot_info)
        return True
    
    def remove_managed_bot(server_id, bot_id):
        """Remove a bot from managed list"""
        if server_id not in managed_bots:
            return False
        
        managed_bots[server_id] = [bot for bot in managed_bots[server_id] if bot['id'] != bot_id]
        return True
    
    async def get_bot_details(server_id, bot_id):
        """Get detailed information about a managed bot"""
        try:
            # Find bot in managed list
            bots = get_managed_bots(server_id)
            for bot_info in bots:
                if bot_info['id'] == bot_id:
                    # Check current status
                    ssh = await get_ssh_session(server_id)
                    if not ssh:
                        return bot_info
                    
                    bot_type = bot_info['type']
                    bot_name = bot_info['name']
                    
                    if bot_type == 'systemd':
                        stdin, stdout, stderr = ssh.exec_command(f"systemctl is-active {bot_name} 2>/dev/null || echo 'inactive'")
                        status = stdout.read().decode().strip()
                        bot_info['status'] = 'running' if status == 'active' else 'stopped'
                        
                    elif bot_type == 'docker':
                        stdin, stdout, stderr = ssh.exec_command(f"docker inspect --format='{{{{.State.Status}}}}' {bot_name} 2>/dev/null || echo 'not found'")
                        status = stdout.read().decode().strip()
                        bot_info['status'] = 'running' if status == 'running' else 'stopped'
                        
                    elif bot_type == 'pm2':
                        stdin, stdout, stderr = ssh.exec_command(f"pm2 describe {bot_name} --no-color 2>/dev/null | grep 'status' || echo 'status: stopped'")
                        output = stdout.read().decode().strip()
                        bot_info['status'] = 'running' if 'online' in output else 'stopped'
                        
                    elif bot_type == 'process':
                        stdin, stdout, stderr = ssh.exec_command(f"ps aux | grep '{bot_name}' | grep -v grep | wc -l")
                        count = stdout.read().decode().strip()
                        bot_info['status'] = 'running' if int(count) > 0 else 'stopped'
                    
                    return bot_info
            
            return None

        except Exception as e:
            logger.error(f"Error getting bot details for {bot_id}: {e}")
            return None
    
    async def control_bot(server_id, bot_id, action):
        """Control bot (start/stop/restart)"""
        try:
            ssh = await get_ssh_session(server_id)
            if not ssh:
                return False, "SSH connection not available"
            
            # Find bot in managed list
            bots = get_managed_bots(server_id)
            bot_info = None
            for bot in bots:
                if bot['id'] == bot_id:
                    bot_info = bot
                    break
            
            if not bot_info:
                return False, "Bot not found in managed list"
            
            bot_type = bot_info['type']
            bot_name = bot_info['name']
            
            if bot_type == 'systemd':
                if action == 'start':
                    stdin, stdout, stderr = ssh.exec_command(f"sudo systemctl start {bot_name}")
                elif action == 'stop':
                    stdin, stdout, stderr = ssh.exec_command(f"sudo systemctl stop {bot_name}")
                elif action == 'restart':
                    stdin, stdout, stderr = ssh.exec_command(f"sudo systemctl restart {bot_name}")
                    
            elif bot_type == 'docker':
                if action == 'start':
                    stdin, stdout, stderr = ssh.exec_command(f"docker start {bot_name}")
                elif action == 'stop':
                    stdin, stdout, stderr = ssh.exec_command(f"docker stop {bot_name}")
                elif action == 'restart':
                    stdin, stdout, stderr = ssh.exec_command(f"docker restart {bot_name}")
                    
            elif bot_type == 'pm2':
                if action == 'start':
                    stdin, stdout, stderr = ssh.exec_command(f"pm2 start {bot_name}")
                elif action == 'stop':
                    stdin, stdout, stderr = ssh.exec_command(f"pm2 stop {bot_name}")
                elif action == 'restart':
                    stdin, stdout, stderr = ssh.exec_command(f"pm2 restart {bot_name}")
                    
            elif bot_type == 'process':
                if action == 'stop':
                    stdin, stdout, stderr = ssh.exec_command(f"pkill -f '{bot_name}'")
                elif action == 'start':
                    # For process type, we need the command to start
                    if 'command' in bot_info:
                        stdin, stdout, stderr = ssh.exec_command(f"nohup {bot_info['command']} > /dev/null 2>&1 &")
                    else:
                        return False, "No start command available for this process"
                elif action == 'restart':
                    # Stop then start
                    stdin, stdout, stderr = ssh.exec_command(f"pkill -f '{bot_name}'")
                    await asyncio.sleep(2)
                    if 'command' in bot_info:
                        stdin, stdout, stderr = ssh.exec_command(f"nohup {bot_info['command']} > /dev/null 2>&1 &")
                    else:
                        return False, "No start command available for this process"
            
            # Wait for command to complete
            exit_status = stdout.channel.recv_exit_status()
            error_output = stderr.read().decode().strip()
            
            if exit_status == 0:
                return True, f"Bot {action} successful"
            else:
                return False, f"Command failed: {error_output}"
                
        except Exception as e:
            logger.error(f"Error controlling bot {bot_id}: {e}")
            return False, str(e)
    
    # --- CALLBACK HANDLERS ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("bot_manager_"))
    async def bot_manager_menu(callback: types.CallbackQuery):
        """Show bot manager main menu"""
        try:
            server_id = callback.data.split('_')[2]
            
            from db import get_server_by_id
            server = await get_server_by_id(server_id)
            
            if not server:
                await callback.message.edit_text("❌ Server not found.")
                return
            
            # Get manually managed bots
            bots = get_managed_bots(server_id)
            
            if not bots:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("➕ Add Bot", callback_data=f"add_bot_{server_id}"))
                kb.add(InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}"))
                
                await callback.message.edit_text(
                    f"🤖 <b>Bot Manager</b>\n\n"
                    f"Server: <b>{server['name']}</b>\n\n"
                    f"No bots configured yet.\n"
                    f"Add a bot to start managing it.",
                    parse_mode='HTML',
                    reply_markup=kb
                )
            else:
                kb = create_bot_keyboard(bots, server_id)
                
                bot_list = "\n".join([
                    f"{'🟢' if bot['status'] == 'running' else '🔴'} {bot['name']} ({bot['type']})"
                    for bot in bots
                ])
                
                await callback.message.edit_text(
                    f"🤖 <b>Bot Manager</b>\n\n"
                    f"Server: <b>{server['name']}</b>\n\n"
                    f"Managed bots ({len(bots)}):\n"
                    f"{bot_list}\n\n"
                    f"Select a bot to manage:",
                    parse_mode='HTML',
                    reply_markup=kb
                )
                
        except Exception as e:
            logger.error(f"Bot manager menu error: {e}")
            await callback.message.edit_text("❌ Error loading bot manager.")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("add_bot_"))
    async def add_bot_menu(callback: types.CallbackQuery):
        """Show add bot menu"""
        try:
            server_id = callback.data.split('_')[2]
            
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("🔧 Systemd Service", callback_data=f"add_systemd_{server_id}"),
                InlineKeyboardButton("🐳 Docker Container", callback_data=f"add_docker_{server_id}")
            )
            kb.add(
                InlineKeyboardButton("📦 PM2 Process", callback_data=f"add_pm2_{server_id}"),
                InlineKeyboardButton("⚙️ Custom Process", callback_data=f"add_process_{server_id}")
            )
            kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"bot_manager_{server_id}"))
            
            await callback.message.edit_text(
                "➕ <b>Add Bot</b>\n\n"
                "Choose the type of bot to add:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Add bot menu error: {e}")
            await callback.message.edit_text("❌ Error loading add bot menu.")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("add_systemd_"))
    async def add_systemd_bot(callback: types.CallbackQuery):
        """Add systemd service bot"""
        try:
            server_id = callback.data.split('_')[2]
            user_id = callback.from_user.id
            
            user_input[user_id] = {
                'action': 'add_bot',
                'server_id': server_id,
                'bot_type': 'systemd',
                'step': 'name'
            }
            
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"add_bot_{server_id}"))
            
            await bot.send_message(
                user_id,
                "🔧 <b>Add Systemd Service</b>\n\n"
                "Enter the service name (without .service extension):\n"
                "Example: <code>mybot</code>",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Add systemd bot error: {e}")
            await callback.message.edit_text("❌ Error adding systemd bot.")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("add_docker_"))
    async def add_docker_bot(callback: types.CallbackQuery):
        """Add docker container bot"""
        try:
            server_id = callback.data.split('_')[2]
            user_id = callback.from_user.id
            
            user_input[user_id] = {
                'action': 'add_bot',
                'server_id': server_id,
                'bot_type': 'docker',
                'step': 'name'
            }
            
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"add_bot_{server_id}"))
            
            await bot.send_message(
                user_id,
                "🐳 <b>Add Docker Container</b>\n\n"
                "Enter the container name:\n"
                "Example: <code>my-bot-container</code>",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Add docker bot error: {e}")
            await callback.message.edit_text("❌ Error adding docker bot.")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("add_pm2_"))
    async def add_pm2_bot(callback: types.CallbackQuery):
        """Add PM2 process bot"""
        try:
            server_id = callback.data.split('_')[2]
            user_id = callback.from_user.id
            
            user_input[user_id] = {
                'action': 'add_bot',
                'server_id': server_id,
                'bot_type': 'pm2',
                'step': 'name'
            }
            
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"add_bot_{server_id}"))
            
            await bot.send_message(
                user_id,
                "📦 <b>Add PM2 Process</b>\n\n"
                "Enter the PM2 process name:\n"
                "Example: <code>my-bot</code>",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Add PM2 bot error: {e}")
            await callback.message.edit_text("❌ Error adding PM2 bot.")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("add_process_"))
    async def add_process_bot(callback: types.CallbackQuery):
        """Add custom process bot"""
        try:
            server_id = callback.data.split('_')[2]
            user_id = callback.from_user.id
            
            user_input[user_id] = {
                'action': 'add_bot',
                'server_id': server_id,
                'bot_type': 'process',
                'step': 'name'
            }
            
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"add_bot_{server_id}"))
            
            await bot.send_message(
                user_id,
                "⚙️ <b>Add Custom Process</b>\n\n"
                "Enter a name for this bot:\n"
                "Example: <code>My Python Bot</code>",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Add process bot error: {e}")
            await callback.message.edit_text("❌ Error adding process bot.")
    
    @dp.message_handler(lambda message: message.from_user.id in user_input and user_input[message.from_user.id].get('action') == 'add_bot')
    async def handle_add_bot_input(message: types.Message):
        """Handle add bot input"""
        try:
            user_id = message.from_user.id
            data = user_input[user_id]
            server_id = data['server_id']
            bot_type = data['bot_type']
            step = data['step']
            
            if step == 'name':
                data['bot_name'] = message.text.strip()
                
                if bot_type == 'process':
                    data['step'] = 'command'
                    await message.answer(
                        "⚙️ <b>Custom Process Command</b>\n\n"
                        "Enter the command to start this process:\n"
                        "Example: <code>python3 /path/to/bot.py</code>",
                        parse_mode='HTML'
                    )
                    return
                else:
                    # For other types, we can add directly
                    await add_bot_to_manager(message, data)
                    
            elif step == 'command':
                data['command'] = message.text.strip()
                await add_bot_to_manager(message, data)
                
        except Exception as e:
            logger.error(f"Handle add bot input error: {e}")
            await message.answer("❌ Error processing input.")
    
    async def add_bot_to_manager(message, data):
        """Add bot to manager"""
        try:
            server_id = data['server_id']
            bot_type = data['bot_type']
            bot_name = data['bot_name']
            
            # Create bot info
            bot_info = {
                'id': f"{bot_type}_{bot_name}",
                'name': bot_name,
                'type': bot_type,
                'status': 'stopped'
            }
            
            if 'command' in data:
                bot_info['command'] = data['command']
            
            # Add to managed bots
            if add_managed_bot(server_id, bot_info):
                await message.answer(
                    f"✅ <b>Bot Added Successfully!</b>\n\n"
                    f"Name: <b>{bot_name}</b>\n"
                    f"Type: <b>{bot_type}</b>\n\n"
                    f"You can now manage this bot from the Bot Manager.",
                    parse_mode='HTML'
                )
            else:
                await message.answer("❌ Bot with this name already exists.")
            
            user_input.pop(message.from_user.id, None)
            
            # Return to bot manager
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("🤖 Bot Manager", callback_data=f"bot_manager_{server_id}"))
            await message.answer("Choose an option:", reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Add bot to manager error: {e}")
            await message.answer("❌ Error adding bot.")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("bot_detail_"))
    async def bot_detail_menu(callback: types.CallbackQuery):
        """Show individual bot detail menu"""
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            bot_id = '_'.join(parts[3:])
            
            await callback.message.edit_text("🔄 <b>Loading bot details...</b>", parse_mode='HTML')
            
            bot_details = await get_bot_details(server_id, bot_id)
            
            if not bot_details:
                await callback.message.edit_text(
                    "❌ Bot not found or error loading details.",
                    reply_markup=InlineKeyboardMarkup().add(
                        InlineKeyboardButton("⬅️ Back to Bots", callback_data=f"bot_manager_{server_id}")
                    )
                )
                return
            
            kb = create_bot_detail_keyboard(server_id, bot_id, bot_details['status'])
            
            status_icon = "🟢" if bot_details['status'] == 'running' else "🔴"
            
            await callback.message.edit_text(
                f"🤖 <b>Bot Details</b>\n\n"
                f"📝 Name: <b>{bot_details['name']}</b>\n"
                f"🔧 Type: <b>{bot_details['type']}</b>\n"
                f"{status_icon} Status: <b>{bot_details['status']}</b>\n\n"
                f"Choose an action:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Bot detail error: {e}")
            await callback.message.edit_text("❌ Error loading bot details.")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("bot_start_"))
    async def bot_start(callback: types.CallbackQuery):
        """Start a bot"""
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            bot_id = '_'.join(parts[3:])
            
            await callback.message.edit_text("🔄 <b>Starting bot...</b>", parse_mode='HTML')
            
            success, message = await control_bot(server_id, bot_id, 'start')
            
            if success:
                await callback.message.edit_text(
                    f"✅ <b>Bot Started</b>\n\n{message}",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup().add(
                        InlineKeyboardButton("⬅️ Back to Bot", callback_data=f"bot_detail_{server_id}_{bot_id}")
                    )
                )
            else:
                await callback.message.edit_text(
                    f"❌ <b>Failed to Start Bot</b>\n\n{message}",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup().add(
                        InlineKeyboardButton("⬅️ Back to Bot", callback_data=f"bot_detail_{server_id}_{bot_id}")
                    )
                )
                
        except Exception as e:
            logger.error(f"Bot start error: {e}")
            await callback.message.edit_text("❌ Error starting bot.")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("bot_stop_"))
    async def bot_stop(callback: types.CallbackQuery):
        """Stop a bot"""
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            bot_id = '_'.join(parts[3:])
            
            await callback.message.edit_text("🔄 <b>Stopping bot...</b>", parse_mode='HTML')
            
            success, message = await control_bot(server_id, bot_id, 'stop')
            
            if success:
                await callback.message.edit_text(
                    f"✅ <b>Bot Stopped</b>\n\n{message}",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup().add(
                        InlineKeyboardButton("⬅️ Back to Bot", callback_data=f"bot_detail_{server_id}_{bot_id}")
                    )
                )
            else:
                await callback.message.edit_text(
                    f"❌ <b>Failed to Stop Bot</b>\n\n{message}",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup().add(
                        InlineKeyboardButton("⬅️ Back to Bot", callback_data=f"bot_detail_{server_id}_{bot_id}")
                    )
                )
                
        except Exception as e:
            logger.error(f"Bot stop error: {e}")
            await callback.message.edit_text("❌ Error stopping bot.")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("bot_restart_"))
    async def bot_restart(callback: types.CallbackQuery):
        """Restart a bot"""
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            bot_id = '_'.join(parts[3:])
            
            await callback.message.edit_text("🔄 <b>Restarting bot...</b>", parse_mode='HTML')
            
            success, message = await control_bot(server_id, bot_id, 'restart')
            
            if success:
                await callback.message.edit_text(
                    f"✅ <b>Bot Restarted</b>\n\n{message}",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup().add(
                        InlineKeyboardButton("⬅️ Back to Bot", callback_data=f"bot_detail_{server_id}_{bot_id}")
                    )
                )
            else:
                await callback.message.edit_text(
                    f"❌ <b>Failed to Restart Bot</b>\n\n{message}",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup().add(
                        InlineKeyboardButton("⬅️ Back to Bot", callback_data=f"bot_detail_{server_id}_{bot_id}")
                    )
                )
                
        except Exception as e:
            logger.error(f"Bot restart error: {e}")
            await callback.message.edit_text("❌ Error restarting bot.")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("bot_logs_"))
    async def bot_logs(callback: types.CallbackQuery):
        """Show bot logs"""
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            bot_id = '_'.join(parts[3:])
            
            await callback.message.edit_text("🔄 <b>Fetching logs...</b>", parse_mode='HTML')
            
            ssh = await get_ssh_session(server_id)
            if not ssh:
                await callback.message.edit_text("❌ SSH connection not available.")
                return
            
            # Find bot in managed list
            bots = get_managed_bots(server_id)
            bot_info = None
            for bot in bots:
                if bot['id'] == bot_id:
                    bot_info = bot
                    break
            
            if not bot_info:
                await callback.message.edit_text("❌ Bot not found.")
                return
            
            bot_type = bot_info['type']
            bot_name = bot_info['name']
            
            logs = ""
            
            if bot_type == 'systemd':
                stdin, stdout, stderr = ssh.exec_command(f"journalctl -u {bot_name} --no-pager -n 20")
                logs = stdout.read().decode().strip()
            elif bot_type == 'docker':
                stdin, stdout, stderr = ssh.exec_command(f"docker logs --tail 20 {bot_name}")
                logs = stdout.read().decode().strip()
            elif bot_type == 'pm2':
                stdin, stdout, stderr = ssh.exec_command(f"pm2 logs {bot_name} --lines 20 --nostream")
                logs = stdout.read().decode().strip()
            elif bot_type == 'process':
                logs = "Process logs not available. Check system logs or application-specific log files."
            
            # Truncate logs if too long
            if len(logs) > 3000:
                logs = logs[-3000:] + "\n\n... (truncated)"
            
            if not logs.strip():
                logs = "No logs available"
            
            await callback.message.edit_text(
                f"📊 <b>Bot Logs</b>\n\n"
                f"<code>{logs}</code>",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("⬅️ Back to Bot", callback_data=f"bot_detail_{server_id}_{bot_id}")
                )
            )
            
        except Exception as e:
            logger.error(f"Bot logs error: {e}")
            await callback.message.edit_text("❌ Error fetching logs.")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("bot_remove_"))
    async def bot_remove_confirm(callback: types.CallbackQuery):
        """Confirm bot removal"""
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            bot_id = '_'.join(parts[3:])
            
            # Find bot in managed list
            bots = get_managed_bots(server_id)
            bot_info = None
            for bot in bots:
                if bot['id'] == bot_id:
                    bot_info = bot
                    break
            
            if not bot_info:
                await callback.message.edit_text("❌ Bot not found.")
                return
            
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("✅ Yes, Remove", callback_data=f"bot_remove_confirm_{server_id}_{bot_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"bot_detail_{server_id}_{bot_id}")
            )
            
            await callback.message.edit_text(
                f"⚠️ <b>Confirm Removal</b>\n\n"
                f"Are you sure you want to remove bot:\n"
                f"<b>{bot_info['name']}</b> ({bot_info['type']})\n\n"
                f"This will only remove it from the bot manager.\n"
                f"The actual service/container will not be affected.",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Bot remove confirm error: {e}")
            await callback.message.edit_text("❌ Error confirming removal.")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("bot_remove_confirm_"))
    async def bot_remove_execute(callback: types.CallbackQuery):
        """Execute bot removal"""
        try:
            parts = callback.data.split('_')
            server_id = parts[3]
            bot_id = '_'.join(parts[4:])
            
            if remove_managed_bot(server_id, bot_id):
                await callback.message.edit_text(
                    "✅ <b>Bot Removed</b>\n\n"
                    "Bot has been removed from the manager.",
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup().add(
                        InlineKeyboardButton("⬅️ Back to Bots", callback_data=f"bot_manager_{server_id}")
                    )
                )
            else:
                await callback.message.edit_text("❌ Failed to remove bot.")
                
        except Exception as e:
            logger.error(f"Bot remove execute error: {e}")
            await callback.message.edit_text("❌ Error removing bot.")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("bot_settings_"))
    async def bot_settings(callback: types.CallbackQuery):
        """Bot settings placeholder"""
        parts = callback.data.split('_')
        server_id = parts[2]
        bot_id = '_'.join(parts[3:])
        
        await callback.message.edit_text(
            "🚧 <b>Bot Settings</b>\n\n"
            "This feature is coming soon!\n\n"
            "You'll be able to:\n"
            "• Edit bot configuration\n"
            "• Set environment variables\n"
            "• Configure auto-restart\n"
            "• Set up monitoring",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("⬅️ Back to Bot", callback_data=f"bot_detail_{server_id}_{bot_id}")
            )
        )
    
    logger.info("✅ Bot manager handlers initialized")
