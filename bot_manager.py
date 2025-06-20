import logging
import asyncio
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# Bot management state
bot_states = {}

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
            InlineKeyboardButton("➕ Add Bot Service", callback_data=f"add_bot_service_{server_id}")       
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
            InlineKeyboardButton("🔧 Update", callback_data=f"bot_update_{server_id}_{bot_id}")
        )
        kb.add(
            InlineKeyboardButton("🗑️ Remove", callback_data=f"bot_remove_{server_id}_{bot_id}"),
            InlineKeyboardButton("⚙️ Settings", callback_data=f"bot_settings_{server_id}_{bot_id}")
        )
        kb.add(InlineKeyboardButton("⬅️ Back to Bots", callback_data=f"bot_manager_{server_id}"))
        
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
    
    async def discover_bots(server_id):
        """Discover all bots on the server"""
        try:
            ssh = await get_ssh_session(server_id)
            if not ssh:
                return []
            
            bots = []
            
            # 1. Discover system services
            try:
                stdin, stdout, stderr = ssh.exec_command("systemctl list-units --type=service --state=active --no-pager | grep -E '(bot|telegram|discord|slack)' || true")
                services_output = stdout.read().decode().strip()
                
                for line in services_output.splitlines():
                    if line.strip():
                        parts = line.split()
                        if len(parts) >= 1:
                            service_name = parts[0].replace('.service', '')
                            bots.append({
                                'id': f"service_{service_name}",
                                'name': service_name,
                                'type': 'systemd',
                                'status': 'running',
                                'path': f"/etc/systemd/system/{service_name}.service"
                            })
            except Exception as e:
                logger.error(f"Error discovering services: {e}")
            
            # 2. Discover Docker containers
            try:
                stdin, stdout, stderr = ssh.exec_command("docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' 2>/dev/null || true")
                docker_output = stdout.read().decode().strip()
                
                for line in docker_output.splitlines()[1:]:  # Skip header
                    if line.strip():
                        parts = line.split('\t')
                        if len(parts) >= 3:
                            container_name = parts[0]
                            status = 'running' if 'Up' in parts[1] else 'stopped'
                            image = parts[2]
                            
                            bots.append({
                                'id': f"docker_{container_name}",
                                'name': container_name,
                                'type': 'docker',
                                'status': status,
                                'image': image
                            })
            except Exception as e:
                logger.error(f"Error discovering Docker containers: {e}")
            
            # 3. Discover running processes (Python, Node.js, etc.)
            try:
                stdin, stdout, stderr = ssh.exec_command("ps aux | grep -E '(python|node|npm|yarn|pm2)' | grep -v grep | grep -E '(bot|telegram|discord|slack)' || true")
                processes_output = stdout.read().decode().strip()
                
                for line in processes_output.splitlines():
                    if line.strip():
                        parts = line.split()
                        if len(parts) >= 11:
                            pid = parts[1]
                            command = ' '.join(parts[10:])
                            
                            # Extract bot name from command
                            bot_name = "unknown_process"
                            if 'python' in command:
                                if 'main.py' in command or 'bot.py' in command:
                                    bot_name = command.split('/')[-1] if '/' in command else command.split()[-1]
                            elif 'node' in command:
                                if 'index.js' in command or 'bot.js' in command:
                                    bot_name = command.split('/')[-1] if '/' in command else command.split()[-1]
                            
                            bots.append({
                                'id': f"process_{pid}",
                                'name': f"{bot_name} (PID: {pid})",
                                'type': 'process',
                                'status': 'running',
                                'command': command
                            })
            except Exception as e:
                logger.error(f"Error discovering processes: {e}")
            
            # 4. Discover PM2 processes
            try:
                stdin, stdout, stderr = ssh.exec_command("pm2 list --no-color 2>/dev/null | grep -E '(online|stopped|errored)' || true")
                pm2_output = stdout.read().decode().strip()
                
                for line in pm2_output.splitlines():
                    if '│' in line:
                        parts = [p.strip() for p in line.split('│')]
                        if len(parts) >= 4:
                            name = parts[1]
                            status = 'running' if 'online' in parts[3] else 'stopped'
                            
                            bots.append({
                                'id': f"pm2_{name}",
                                'name': name,
                                'type': 'pm2',
                                'status': status
                            })
            except Exception as e:
                logger.error(f"Error discovering PM2 processes: {e}")
            
            return bots
            
        except Exception as e:
            logger.error(f"Error discovering bots: {e}")
            return []
    
    async def get_bot_details(server_id, bot_id):
        """Get detailed information about a bot"""
        try:
            ssh = await get_ssh_session(server_id)
            if not ssh:
                return None

            # Parse bot type and name from bot_id
            bot_type = bot_id.split('_')[0]
            bot_name = '_'.join(bot_id.split('_')[1:])
            
            details = {
                'id': bot_id,
                'name': bot_name,
                'type': bot_type,
                'status': 'unknown'
            }
            
            if bot_type == 'service':
                # Get systemd service status
                stdin, stdout, stderr = ssh.exec_command(f"systemctl is-active {bot_name} 2>/dev/null || echo 'inactive'")
                status = stdout.read().decode().strip()
                details['status'] = 'running' if status == 'active' else 'stopped'
                
            elif bot_type == 'docker':
                # Get Docker container status
                stdin, stdout, stderr = ssh.exec_command(f"docker inspect --format='{{{{.State.Status}}}}' {bot_name} 2>/dev/null || echo 'not found'")
                status = stdout.read().decode().strip()
                details['status'] = 'running' if status == 'running' else 'stopped'
                
            elif bot_type == 'pm2':
                # Get PM2 process status
                stdin, stdout, stderr = ssh.exec_command(f"pm2 describe {bot_name} --no-color 2>/dev/null | grep 'status' || echo 'status: stopped'")
                output = stdout.read().decode().strip()
                details['status'] = 'running' if 'online' in output else 'stopped'
                
            elif bot_type == 'process':
                # Check if process is still running
                stdin, stdout, stderr = ssh.exec_command(f"ps -p {bot_name} > /dev/null 2>&1 && echo 'running' || echo 'stopped'")
                status = stdout.read().decode().strip()
                details['status'] = status
            
            return details

        except Exception as e:
            logger.error(f"Error getting bot details for {bot_id}: {e}")
            return None
    
    async def control_bot(server_id, bot_id, action):
        """Control bot (start/stop/restart)"""
        try:
            ssh = await get_ssh_session(server_id)
            if not ssh:
                return False, "SSH connection not available"
            
            bot_type = bot_id.split('_')[0]
            bot_name = '_'.join(bot_id.split('_')[1:])
            
            if bot_type == 'service':
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
                    stdin, stdout, stderr = ssh.exec_command(f"kill {bot_name}")
                else:
                    return False, "Cannot start/restart individual processes"
            
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
            
            await callback.message.edit_text("🔄 <b>Discovering bots...</b>", parse_mode='HTML')
            
            # Discover bots on the server
            bots = await discover_bots(server_id)
            
            if not bots:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("➕ Add Bot Service", callback_data=f"add_bot_service_{server_id}"))
                kb.add(InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}"))
                
                await callback.message.edit_text(
                    f"🤖 <b>Bot Manager</b>\n\n"
                    f"Server: <b>{server['name']}</b>\n\n"
                    f"No bots found on this server.\n"
                    f"You can add a new bot service or check back later.",
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
                    f"Found {len(bots)} bot(s):\n"
                    f"{bot_list}\n\n"
                    f"Select a bot to manage:",
                    parse_mode='HTML',
                    reply_markup=kb
                )
                
        except Exception as e:
            logger.error(f"Bot manager menu error: {e}")
            await callback.message.edit_text("❌ Error loading bot manager.")
    
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
            
            bot_type = bot_id.split('_')[0]
            bot_name = '_'.join(bot_id.split('_')[1:])
            
            logs = ""
            
            if bot_type == 'service':
                stdin, stdout, stderr = ssh.exec_command(f"journalctl -u {bot_name} --no-pager -n 20")
                logs = stdout.read().decode().strip()
            elif bot_type == 'docker':
                stdin, stdout, stderr = ssh.exec_command(f"docker logs --tail 20 {bot_name}")
                logs = stdout.read().decode().strip()
            elif bot_type == 'pm2':
                stdin, stdout, stderr = ssh.exec_command(f"pm2 logs {bot_name} --lines 20 --nostream")
                logs = stdout.read().decode().strip()
            else:
                logs = "Logs not available for this bot type"
            
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
    
    @dp.callback_query_handler(lambda c: c.data.startswith("add_bot_service_"))
    async def add_bot_service(callback: types.CallbackQuery):
        """Add new bot service placeholder"""
        try:
            server_id = callback.data.split('_')[3]
            
            await callback.message.edit_text(
                "🚧 <b>Add Bot Service</b>\n\n"
                "This feature is coming soon!\n\n"
                "You'll be able to:\n"
                "• Deploy new bot projects\n"
                "• Configure systemd services\n"
                "• Set up Docker containers\n"
                "• Manage PM2 processes",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("⬅️ Back to Bots", callback_data=f"bot_manager_{server_id}")
                )
            )
            
        except Exception as e:
            logger.error(f"Add bot service error: {e}")
            await callback.message.edit_text("❌ Error loading add service menu.")
    
    # Placeholder handlers for other bot actions
    @dp.callback_query_handler(lambda c: c.data.startswith("bot_update_"))
    async def bot_update(callback: types.CallbackQuery):
        """Update bot placeholder"""
        parts = callback.data.split('_')
        server_id = parts[2]
        bot_id = '_'.join(parts[3:])
        
        await callback.message.edit_text(
            "🚧 <b>Bot Update</b>\n\n"
            "This feature is coming soon!",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("⬅️ Back to Bot", callback_data=f"bot_detail_{server_id}_{bot_id}")
            )
        )
    
    @dp.callback_query_handler(lambda c: c.data.startswith("bot_remove_"))
    async def bot_remove(callback: types.CallbackQuery):
        """Remove bot placeholder"""
        parts = callback.data.split('_')
        server_id = parts[2]
        bot_id = '_'.join(parts[3:])
        
        await callback.message.edit_text(
            "🚧 <b>Remove Bot</b>\n\n"
            "This feature is coming soon!",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("⬅️ Back to Bot", callback_data=f"bot_detail_{server_id}_{bot_id}")
            )
        )
    
    @dp.callback_query_handler(lambda c: c.data.startswith("bot_settings_"))
    async def bot_settings(callback: types.CallbackQuery):
        """Bot settings placeholder"""
        parts = callback.data.split('_')
        server_id = parts[2]
        bot_id = '_'.join(parts[3:])
        
        await callback.message.edit_text(
            "🚧 <b>Bot Settings</b>\n\n"
            "This feature is coming soon!",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton("⬅️ Back to Bot", callback_data=f"bot_detail_{server_id}_{bot_id}")
            )
        )
    
    logger.info("✅ Bot manager handlers initialized")
