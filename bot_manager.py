import logging
import os
import json
import tempfile
import zipfile
import tarfile
import re
import asyncio
from datetime import datetime
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# Bot management state
bot_states = {}
deployment_states = {}

def init_bot_manager(dp, bot, active_sessions, user_input):
    """Initialize bot manager handlers"""
    
    # --- UTILITY FUNCTIONS ---
    
    def create_bot_keyboard(bots, server_id):
        """Create keyboard for bot list"""
        kb = InlineKeyboardMarkup(row_width=1)
        
        for bot_info in bots:
            status_icon = "🟢" if bot_info['status'] == 'running' else "🔴"
            # Truncate long names for display
            display_name = bot_info['name'][:30] + "..." if len(bot_info['name']) > 30 else bot_info['name']
            
            kb.add(InlineKeyboardButton(
                f"{status_icon} {display_name} ({bot_info['type']})",
                callback_data=f"bot_detail_{server_id}_{bot_info['id'][:20]}"  # Limit callback data length
            ))
        
        kb.add(
            InlineKeyboardButton("➕ Add Existing Bot", callback_data=f"add_bot_{server_id}"),
            InlineKeyboardButton("🚀 Deploy New Bot", callback_data=f"deploy_bot_{server_id}")
        )
        kb.add(InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}"))
        
        return kb
    
    def create_bot_detail_keyboard(server_id, bot_id, bot_status):
        """Create keyboard for individual bot management"""
        kb = InlineKeyboardMarkup(row_width=2)
        
        if bot_status == 'running':
            kb.add(
                InlineKeyboardButton("⏹️ Stop", callback_data=f"bot_stop_{server_id}_{bot_id[:15]}"),
                InlineKeyboardButton("🔄 Restart", callback_data=f"bot_restart_{server_id}_{bot_id[:15]}")
            )
        else:
            kb.add(
                InlineKeyboardButton("▶️ Start", callback_data=f"bot_start_{server_id}_{bot_id[:15]}"),
                InlineKeyboardButton("🔄 Restart", callback_data=f"bot_restart_{server_id}_{bot_id[:15]}")
            )
        
        kb.add(
            InlineKeyboardButton("📊 Logs", callback_data=f"bot_logs_{server_id}_{bot_id[:15]}"),
            InlineKeyboardButton("🔧 Update", callback_data=f"bot_update_{server_id}_{bot_id[:15]}")
        )
        kb.add(
            InlineKeyboardButton("🗑️ Remove", callback_data=f"bot_remove_{server_id}_{bot_id[:15]}"),
            InlineKeyboardButton("⚙️ Settings", callback_data=f"bot_settings_{server_id}_{bot_id[:15]}")
        )
        kb.add(InlineKeyboardButton("⬅️ Back to Bots", callback_data=f"bot_manager_{server_id}"))
        
        return kb
    
    async def get_ssh_session(server_id):
        """Get SSH session for server"""
        from db import get_server_by_id
        from main import get_ssh_session as main_get_ssh_session
        
        server = await get_server_by_id(server_id)
        if not server:
            return None
        
        return main_get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
    
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
                stdin, stdout, stderr = ssh.exec_command("ps aux | grep -E '(python|node|npm|yarn)' | grep -v grep | grep -E '(bot|telegram|discord|slack|main\\.py|bot\\.py|index\\.js)' || true")
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
            
            bot_type, bot_name = bot_id.split('_', 1)
            details = {'id': bot_id, 'type': bot_type, 'name': bot_name}
            
            if bot_type == 'service':
                # Get systemd service details
                stdin, stdout, stderr = ssh.exec_command(f"systemctl status {bot_name} --no-pager")
                status_output = stdout.read().decode()
                details['status_info'] = status_output
                
            elif bot_type == 'docker':
                # Get Docker container details
                stdin, stdout, stderr = ssh.exec_command(f"docker inspect {bot_name} 2>/dev/null || echo 'Container not found'")
                inspect_output = stdout.read().decode()
                details['inspect_info'] = inspect_output
                
            elif bot_type == 'process':
                # Get process details
                stdin, stdout, stderr = ssh.exec_command(f"ps -p {bot_name} -o pid,ppid,cmd --no-headers 2>/dev/null || echo 'Process not found'")
                process_output = stdout.read().decode()
                details['process_info'] = process_output
                
            elif bot_type == 'pm2':
                # Get PM2 process details
                stdin, stdout, stderr = ssh.exec_command(f"pm2 describe {bot_name} --no-color 2>/dev/null || echo 'PM2 process not found'")
                pm2_output = stdout.read().decode()
                details['pm2_info'] = pm2_output
            
            return details
            
        except Exception as e:
            logger.error(f"Error getting bot details: {e}")
            return None
    
    async def control_bot(server_id, bot_id, action):
        """Control bot (start, stop, restart)"""
        try:
            ssh = await get_ssh_session(server_id)
            if not ssh:
                return False, "SSH connection failed"
            
            bot_type, bot_name = bot_id.split('_', 1)
            
            if bot_type == 'service':
                command = f"sudo systemctl {action} {bot_name}"
            elif bot_type == 'docker':
                if action == 'start':
                    command = f"docker start {bot_name}"
                elif action == 'stop':
                    command = f"docker stop {bot_name}"
                elif action == 'restart':
                    command = f"docker restart {bot_name}"
            elif bot_type == 'process':
                if action == 'stop':
                    command = f"kill {bot_name}"
                elif action == 'restart':
                    command = f"kill -HUP {bot_name}"
                else:
                    return False, "Start not supported for processes"
            elif bot_type == 'pm2':
                command = f"pm2 {action} {bot_name}"
            else:
                return False, "Unsupported bot type"
            
            stdin, stdout, stderr = ssh.exec_command(command)
            stdout_output = stdout.read().decode()
            stderr_output = stderr.read().decode()
            
            if stderr_output and "error" in stderr_output.lower():
                return False, stderr_output
            
            return True, f"Bot {action} command executed successfully"
            
        except Exception as e:
            logger.error(f"Error controlling bot: {e}")
            return False, str(e)
    
    async def get_bot_logs(server_id, bot_id, lines=50):
        """Get bot logs"""
        try:
            ssh = await get_ssh_session(server_id)
            if not ssh:
                return "SSH connection failed"
            
            bot_type, bot_name = bot_id.split('_', 1)
            
            if bot_type == 'service':
                command = f"journalctl -u {bot_name} -n {lines} --no-pager"
            elif bot_type == 'docker':
                command = f"docker logs --tail {lines} {bot_name}"
            elif bot_type == 'pm2':
                command = f"pm2 logs {bot_name} --lines {lines} --nostream"
            else:
                return "Logs not available for this bot type"
            
            stdin, stdout, stderr = ssh.exec_command(command)
            logs = stdout.read().decode()
            
            if not logs.strip():
                logs = "No logs available"
            
            # Truncate if too long for Telegram
            if len(logs) > 4000:
                logs = logs[-4000:] + "\n\n... (truncated)"
            
            return logs
            
        except Exception as e:
            logger.error(f"Error getting logs: {e}")
            return f"Error getting logs: {str(e)}"
    
    # --- MAIN BOT MANAGER HANDLER ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("bot_manager_"))
    async def bot_manager_main(callback: types.CallbackQuery):
        """Main bot manager interface"""
        try:
            server_id = callback.data.split('_')[2]
            
            await callback.message.edit_text("🤖 <b>Discovering bots...</b>", parse_mode='HTML')
            
            bots = await discover_bots(server_id)
            
            if not bots:
                kb = InlineKeyboardMarkup()
                kb.add(
                    InlineKeyboardButton("➕ Add Existing Bot", callback_data=f"add_bot_{server_id}"),
                    InlineKeyboardButton("🚀 Deploy New Bot", callback_data=f"deploy_bot_{server_id}")
                )
                kb.add(InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}"))
                
                await callback.message.edit_text(
                    "🤖 <b>Bot Manager</b>\n\n"
                    "No bots found on this server.\n"
                    "Add existing bots or deploy new ones.",
                    parse_mode='HTML',
                    reply_markup=kb
                )
                return
            
            kb = create_bot_keyboard(bots, server_id)
            
            text = (
                f"🤖 <b>Bot Manager</b>\n\n"
                f"Found {len(bots)} bot(s):\n\n"
            )
            
            for bot_info in bots:
                status_icon = "🟢" if bot_info['status'] == 'running' else "🔴"
                display_name = bot_info['name'][:25] + "..." if len(bot_info['name']) > 25 else bot_info['name']
                text += f"{status_icon} <b>{display_name}</b> ({bot_info['type']})\n"
            
            await callback.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Bot manager error: {e}")
            await callback.message.edit_text("❌ Error loading bot manager.")
    
    # --- BOT DETAIL HANDLER ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("bot_detail_"))
    async def bot_detail_handler(callback: types.CallbackQuery):
        """Show individual bot details and controls"""
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            bot_id_part = '_'.join(parts[3:])
            
            # Find the full bot ID from discovered bots
            bots = await discover_bots(server_id)
            bot_id = None
            current_bot = None
            
            for bot in bots:
                if bot['id'].startswith(bot_id_part) or bot_id_part in bot['id']:
                    bot_id = bot['id']
                    current_bot = bot
                    break
            
            if not bot_id or not current_bot:
                await callback.message.edit_text("❌ Bot not found.")
                return
            
            bot_type, bot_name = bot_id.split('_', 1)
            status = current_bot['status']
            
            status_icon = "🟢" if status == 'running' else "🔴"
            
            text = (
                f"🤖 <b>{current_bot['name']}</b>\n\n"
                f"📋 Type: <code>{bot_type}</code>\n"
                f"{status_icon} Status: <b>{status}</b>\n"
            )
            
            if bot_type == 'docker' and current_bot:
                text += f"🐳 Image: <code>{current_bot.get('image', 'unknown')}</code>\n"
            elif bot_type == 'process' and current_bot:
                command = current_bot.get('command', 'unknown')
                text += f"💻 Command: <code>{command[:50]}...</code>\n"
            
            kb = create_bot_detail_keyboard(server_id, bot_id_part, status)
            
            await callback.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Bot detail error: {e}")
            await callback.message.edit_text("❌ Error loading bot details.")
    
    # --- BOT CONTROL HANDLERS ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("bot_start_") or c.data.startswith("bot_stop_") or c.data.startswith("bot_restart_"))
    async def bot_control_handler(callback: types.CallbackQuery):
        """Handle bot start/stop/restart"""
        try:
            parts = callback.data.split('_')
            action = parts[1]
            server_id = parts[2]
            bot_id_part = '_'.join(parts[3:])
            
            # Find the full bot ID
            bots = await discover_bots(server_id)
            bot_id = None
            
            for bot in bots:
                if bot['id'].startswith(bot_id_part) or bot_id_part in bot['id']:
                    bot_id = bot['id']
                    break
            
            if not bot_id:
                await callback.message.edit_text("❌ Bot not found.")
                return
            
            await callback.message.edit_text(f"🔄 <b>{action.capitalize()}ing bot...</b>", parse_mode='HTML')
            
            success, message = await control_bot(server_id, bot_id, action)
            
            if success:
                await callback.message.edit_text(
                    f"✅ <b>Bot {action} successful!</b>\n\n{message}",
                    parse_mode='HTML'
                )
                
                # Wait a moment then show bot details again
                await asyncio.sleep(2)
                await bot_detail_handler(types.CallbackQuery(
                    id=callback.id,
                    from_user=callback.from_user,
                    message=callback.message,
                    data=f"bot_detail_{server_id}_{bot_id_part}"
                ))
            else:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"bot_detail_{server_id}_{bot_id_part}"))
                
                await callback.message.edit_text(
                    f"❌ <b>Bot {action} failed!</b>\n\n<code>{message}</code>",
                    parse_mode='HTML',
                    reply_markup=kb
                )
            
        except Exception as e:
            logger.error(f"Bot control error: {e}")
            await callback.message.edit_text("❌ Error controlling bot.")
    
    # --- BOT LOGS HANDLER ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("bot_logs_"))
    async def bot_logs_handler(callback: types.CallbackQuery):
        """Show bot logs"""
        try:
            parts = callback.data.split('_')
            server_id = parts[2]
            bot_id_part = '_'.join(parts[3:])
            
            # Find the full bot ID
            bots = await discover_bots(server_id)
            bot_id = None
            
            for bot in bots:
                if bot['id'].startswith(bot_id_part) or bot_id_part in bot['id']:
                    bot_id = bot['id']
                    break
            
            if not bot_id:
                await callback.message.edit_text("❌ Bot not found.")
                return
            
            await callback.message.edit_text("📊 <b>Fetching logs...</b>", parse_mode='HTML')
            
            logs = await get_bot_logs(server_id, bot_id)
            
            kb = InlineKeyboardMarkup()
            kb.add(
                InlineKeyboardButton("🔄 Refresh", callback_data=f"bot_logs_{server_id}_{bot_id_part}"),
                InlineKeyboardButton("⬅️ Back", callback_data=f"bot_detail_{server_id}_{bot_id_part}")
            )
            
            text = f"📊 <b>Bot Logs</b>\n\n<pre>{logs}</pre>"
            
            await callback.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Bot logs error: {e}")
            await callback.message.edit_text("❌ Error fetching logs.")
    
    # --- ADD BOT HANDLER ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("add_bot_"))
    async def add_bot_handler(callback: types.CallbackQuery):
        """Add existing bot menu"""
        try:
            server_id = callback.data.split('_')[2]
            
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(
                InlineKeyboardButton("🐳 Docker Container", callback_data=f"add_docker_{server_id}"),
                InlineKeyboardButton("⚙️ System Service", callback_data=f"add_service_{server_id}"),
                InlineKeyboardButton("📁 Folder/Script", callback_data=f"add_folder_{server_id}")
            )
            kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"bot_manager_{server_id}"))
            
            await callback.message.edit_text(
                "➕ <b>Add Existing Bot</b>\n\n"
                "Choose the type of bot to add:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Add bot error: {e}")
            await callback.message.edit_text("❌ Error loading add bot menu.")
    
    # --- DEPLOY BOT HANDLER ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("deploy_bot_"))
    async def deploy_bot_handler(callback: types.CallbackQuery):
        """Deploy new bot menu"""
        try:
            server_id = callback.data.split('_')[2]
            
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(
                InlineKeyboardButton("🐙 Deploy from GitHub", callback_data=f"deploy_github_{server_id}"),
                InlineKeyboardButton("🐳 Deploy Docker", callback_data=f"deploy_docker_{server_id}")
            )
            kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"bot_manager_{server_id}"))
            
            await callback.message.edit_text(
                "🚀 <b>Deploy New Bot</b>\n\n"
                "Choose deployment method:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Deploy bot error: {e}")
            await callback.message.edit_text("❌ Error loading deploy menu.")
    
    # --- GITHUB DEPLOYMENT ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("deploy_github_"))
    async def deploy_github_handler(callback: types.CallbackQuery):
        """Start GitHub deployment"""
        try:
            server_id = callback.data.split('_')[2]
            
            deployment_states[callback.from_user.id] = {
                'type': 'github',
                'server_id': server_id,
                'step': 'repo_url'
            }
            
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"deploy_bot_{server_id}"))
            
            await bot.send_message(
                callback.from_user.id,
                "🐙 <b>GitHub Deployment</b>\n\n"
                "Send the GitHub repository URL:\n"
                "Example: <code>https://github.com/user/repo</code>",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"GitHub deploy error: {e}")
            await callback.message.edit_text("❌ Error starting GitHub deployment.")
    
    # --- DOCKER DEPLOYMENT ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("deploy_docker_"))
    async def deploy_docker_handler(callback: types.CallbackQuery):
        """Start Docker deployment"""
        try:
            server_id = callback.data.split('_')[2]
            
            deployment_states[callback.from_user.id] = {
                'type': 'docker',
                'server_id': server_id,
                'step': 'image_or_repo'
            }
            
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"deploy_bot_{server_id}"))
            
            await bot.send_message(
                callback.from_user.id,
                "🐳 <b>Docker Deployment</b>\n\n"
                "Send either:\n"
                "• Docker image name: <code>nginx:latest</code>\n"
                "• GitHub repo URL: <code>https://github.com/user/repo</code>",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Docker deploy error: {e}")
            await callback.message.edit_text("❌ Error starting Docker deployment.")
    
    # --- DEPLOYMENT MESSAGE HANDLER ---
    
    @dp.message_handler(lambda message: message.from_user.id in deployment_states)
    async def handle_deployment_input(message: types.Message):
        """Handle deployment input messages"""
        try:
            uid = message.from_user.id
            if uid not in deployment_states:
                return
            
            state = deployment_states[uid]
            server_id = state['server_id']
            
            if state['type'] == 'github' and state['step'] == 'repo_url':
                # GitHub repository URL received
                repo_url = message.text.strip()
                
                if not repo_url.startswith('https://github.com/'):
                    await message.answer("❌ Please provide a valid GitHub URL starting with https://github.com/")
                    return
                
                await message.answer("📥 <b>Cloning repository...</b>", parse_mode='HTML')
                
                # Clone repository
                ssh = await get_ssh_session(server_id)
                if not ssh:
                    await message.answer("❌ SSH connection failed")
                    deployment_states.pop(uid, None)
                    return
                
                repo_name = repo_url.split('/')[-1].replace('.git', '')
                clone_path = f"/tmp/{repo_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                
                stdin, stdout, stderr = ssh.exec_command(f"git clone {repo_url} {clone_path}")
                stderr_output = stderr.read().decode()
                
                if stderr_output and "error" in stderr_output.lower():
                    await message.answer(f"❌ Clone failed: {stderr_output}")
                    deployment_states.pop(uid, None)
                    return
                
                # Check for requirements files
                stdin, stdout, stderr = ssh.exec_command(f"ls {clone_path}/")
                files = stdout.read().decode().strip().split('\n')
                
                has_requirements = any(f in files for f in ['requirements.txt', 'package.json', 'Pipfile', 'poetry.lock'])
                
                state['repo_url'] = repo_url
                state['clone_path'] = clone_path
                state['files'] = files
                
                if has_requirements:
                    kb = InlineKeyboardMarkup(row_width=2)
                    kb.add(
                        InlineKeyboardButton("✅ Install", callback_data=f"install_deps_{uid}"),
                        InlineKeyboardButton("⏭️ Skip", callback_data=f"skip_deps_{uid}")
                    )
                    kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_deploy_{uid}"))
                    
                    await message.answer(
                        "📦 <b>Dependencies Found</b>\n\n"
                        "Found dependency files. Install requirements?",
                        parse_mode='HTML',
                        reply_markup=kb
                    )
                else:
                    # No dependencies, proceed to executable selection
                    await show_executable_selection(message, uid, state)
            
            elif state['type'] == 'docker' and state['step'] == 'image_or_repo':
                # Docker image or repo URL received
                input_text = message.text.strip()
                
                if input_text.startswith('https://github.com/'):
                    # GitHub repo for Docker build
                    state['repo_url'] = input_text
                    state['step'] = 'dockerfile_check'
                    
                    await message.answer("🐳 <b>Checking for Dockerfile...</b>", parse_mode='HTML')
                    
                    # Clone and check for Dockerfile
                    ssh = await get_ssh_session(server_id)
                    if not ssh:
                        await message.answer("❌ SSH connection failed")
                        deployment_states.pop(uid, None)
                        return
                    
                    repo_name = input_text.split('/')[-1].replace('.git', '')
                    clone_path = f"/tmp/{repo_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    
                    stdin, stdout, stderr = ssh.exec_command(f"git clone {input_text} {clone_path}")
                    stderr_output = stderr.read().decode()
                    
                    if stderr_output and "error" in stderr_output.lower():
                        await message.answer(f"❌ Clone failed: {stderr_output}")
                        deployment_states.pop(uid, None)
                        return
                    
                    # Check for Dockerfile
                    stdin, stdout, stderr = ssh.exec_command(f"ls {clone_path}/Dockerfile 2>/dev/null && echo 'found' || echo 'not found'")
                    dockerfile_check = stdout.read().decode().strip()
                    
                    state['clone_path'] = clone_path
                    
                    if 'found' in dockerfile_check:
                        await show_docker_env_options(message, uid, state)
                    else:
                        await message.answer("❌ No Dockerfile found in repository")
                        deployment_states.pop(uid, None)
                        return
                
                else:
                    # Docker image name
                    state['image_name'] = input_text
                    await show_docker_env_options(message, uid, state)
        
        except Exception as e:
            logger.error(f"Deployment input error: {e}")
            await message.answer("❌ Error processing deployment input")
            deployment_states.pop(message.from_user.id, None)
    
    async def show_executable_selection(message, uid, state):
        """Show executable file selection"""
        try:
            clone_path = state['clone_path']
            ssh = await get_ssh_session(state['server_id'])
            
            # Find potential executable files
            stdin, stdout, stderr = ssh.exec_command(f"find {clone_path} -name '*.py' -o -name '*.js' -o -name 'main.*' -o -name 'bot.*' -o -name 'index.*' | head -20")
            executables = stdout.read().decode().strip().split('\n')
            executables = [f for f in executables if f.strip()]
            
            if not executables:
                await message.answer("❌ No executable files found")
                deployment_states.pop(uid, None)
                return
            
            kb = InlineKeyboardMarkup(row_width=1)
            for exe in executables[:10]:  # Limit to 10 files
                filename = exe.split('/')[-1]
                kb.add(InlineKeyboardButton(filename, callback_data=f"select_exe_{uid}_{filename}"))
            
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_deploy_{uid}"))
            
            state['executables'] = executables
            
            await message.answer(
                "📄 <b>Select Executable File</b>\n\n"
                "Choose the main file to run:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Executable selection error: {e}")
            await message.answer("❌ Error finding executable files")
    
    async def show_docker_env_options(message, uid, state):
        """Show Docker environment options"""
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("📄 Send .env", callback_data=f"docker_env_{uid}"),
            InlineKeyboardButton("⏭️ Continue", callback_data=f"docker_no_env_{uid}")
        )
        kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_deploy_{uid}"))
        
        await message.answer(
            "🔧 <b>Environment Configuration</b>\n\n"
            "Do you want to provide environment variables?",
            parse_mode='HTML',
            reply_markup=kb
        )
    
    # --- ADDITIONAL CALLBACK HANDLERS ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("install_deps_") or c.data.startswith("skip_deps_"))
    async def handle_deps_decision(callback: types.CallbackQuery):
        """Handle dependency installation decision"""
        try:
            uid = int(callback.data.split('_')[2])
            action = callback.data.split('_')[1]
            
            if uid not in deployment_states:
                await callback.message.edit_text("❌ Deployment session expired")
                return
            
            state = deployment_states[uid]
            
            if action == 'install':
                await callback.message.edit_text("📦 <b>Installing dependencies...</b>", parse_mode='HTML')
                
                ssh = await get_ssh_session(state['server_id'])
                clone_path = state['clone_path']
                
                # Install dependencies based on file type
                if 'requirements.txt' in state['files']:
                    stdin, stdout, stderr = ssh.exec_command(f"cd {clone_path} && pip install -r requirements.txt")
                elif 'package.json' in state['files']:
                    stdin, stdout, stderr = ssh.exec_command(f"cd {clone_path} && npm install")
                
                await callback.message.edit_text("✅ <b>Dependencies installed!</b>", parse_mode='HTML')
            
            # Proceed to executable selection
            await show_executable_selection(callback.message, uid, state)
            
        except Exception as e:
            logger.error(f"Deps decision error: {e}")
            await callback.message.edit_text("❌ Error handling dependency installation")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("cancel_deploy_"))
    async def cancel_deployment(callback: types.CallbackQuery):
        """Cancel deployment"""
        try:
            uid = int(callback.data.split('_')[2])
            deployment_states.pop(uid, None)
            await callback.message.edit_text("❌ <b>Deployment cancelled</b>", parse_mode='HTML')
        except Exception as e:
            logger.error(f"Cancel deployment error: {e}")
    
    logger.info("Bot manager initialized successfully")
