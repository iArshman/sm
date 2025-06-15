import logging
import os
import json
import asyncio
import subprocess
import tempfile
import shutil
import zipfile
import git
from datetime import datetime
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import paramiko
import psutil

logger = logging.getLogger(__name__)

# Global state for bot management
bot_manager_state = {}
running_bots = {}  # {server_id: {bot_name: process_info}}

def init_bot_manager(dp, bot, active_sessions, user_input):
    """Initialize bot manager handlers"""
    
    # --- BOT MANAGER MAIN ---
    @dp.callback_query_handler(lambda c: c.data.startswith("bot_manager_"))
    async def bot_manager_main(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("🤖 Bots", callback_data=f"bm_bots_{server_id}"),
                InlineKeyboardButton("🔧 Services", callback_data=f"bm_services_{server_id}")
            )
            kb.add(InlineKeyboardButton("🚀 Future Features", callback_data=f"bm_future_{server_id}"))
            kb.add(InlineKeyboardButton("⬅️ Back to Server", callback_data=f"server_{server_id}"))
            
            await callback.message.edit_text(
                "🤖 <b>Bot Management Center</b>\n\n"
                "Choose a management option:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Bot manager main error: {e}")
            await callback.message.edit_text("❌ Error accessing bot manager.")

    # --- BOTS SECTION ---
    @dp.callback_query_handler(lambda c: c.data.startswith("bm_bots_"))
    async def bots_section(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            
            # Get list of bots
            bots = await get_server_bots(server_id, active_sessions)
            
            kb = InlineKeyboardMarkup(row_width=1)
            
            # Add deploy button at top
            kb.add(InlineKeyboardButton("🚀 Deploy New Bot", callback_data=f"bm_deploy_{server_id}"))
            
            if bots:
                kb.add(InlineKeyboardButton("📊 Bot Overview", callback_data=f"bm_overview_{server_id}"))
                
                for bot_info in bots:
                    status_icon = "🟢" if bot_info['status'] == 'running' else "🔴"
                    kb.add(InlineKeyboardButton(
                        f"{status_icon} {bot_info['name']} ({bot_info['type']})",
                        callback_data=f"bm_bot_{server_id}_{bot_info['name']}"
                    ))
            else:
                kb.add(InlineKeyboardButton("📋 Scan for Existing Bots", callback_data=f"bm_scan_{server_id}"))
            
            kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"bot_manager_{server_id}"))
            
            bot_count = len(bots) if bots else 0
            running_count = len([b for b in bots if b['status'] == 'running']) if bots else 0
            
            text = (
                f"🤖 <b>Bot Management</b>\n\n"
                f"📊 <b>Statistics:</b>\n"
                f"Total Bots: {bot_count}\n"
                f"Running: {running_count}\n"
                f"Stopped: {bot_count - running_count}\n\n"
                f"Select a bot to manage or deploy a new one:"
            )
            
            await callback.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Bots section error: {e}")
            await callback.message.edit_text("❌ Error loading bots section.")

    # --- DEPLOY NEW BOT ---
    @dp.callback_query_handler(lambda c: c.data.startswith("bm_deploy_"))
    async def deploy_new_bot(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("📤 Upload Files", callback_data=f"bm_upload_{server_id}"),
                InlineKeyboardButton("🐙 From GitHub", callback_data=f"bm_github_{server_id}")
            )
            kb.add(InlineKeyboardButton("📁 Select Existing", callback_data=f"bm_select_{server_id}"))
            kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"bm_bots_{server_id}"))
            
            await callback.message.edit_text(
                "🚀 <b>Deploy New Bot</b>\n\n"
                "Choose deployment method:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Deploy new bot error: {e}")

    # --- UPLOAD BOT FILES ---
    @dp.callback_query_handler(lambda c: c.data.startswith("bm_upload_"))
    async def upload_bot_files(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            user_id = callback.from_user.id
            
            user_input[user_id] = {
                'action': 'bot_upload',
                'server_id': server_id,
                'step': 'name'
            }
            
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"bm_deploy_{server_id}"))
            
            await bot.send_message(
                user_id,
                "📤 <b>Upload Bot Files</b>\n\n"
                "Enter bot name:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Upload bot files error: {e}")

    # --- GITHUB DEPLOYMENT ---
    @dp.callback_query_handler(lambda c: c.data.startswith("bm_github_"))
    async def github_deployment(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            user_id = callback.from_user.id
            
            user_input[user_id] = {
                'action': 'bot_github',
                'server_id': server_id,
                'step': 'name'
            }
            
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"bm_deploy_{server_id}"))
            
            await bot.send_message(
                user_id,
                "🐙 <b>Deploy from GitHub</b>\n\n"
                "Enter bot name:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"GitHub deployment error: {e}")

    # --- SELECT EXISTING BOT ---
    @dp.callback_query_handler(lambda c: c.data.startswith("bm_select_"))
    async def select_existing_bot(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            user_id = callback.from_user.id
            
            user_input[user_id] = {
                'action': 'bot_select',
                'server_id': server_id
            }
            
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"bm_deploy_{server_id}"))
            
            await bot.send_message(
                user_id,
                "📁 <b>Select Existing Bot</b>\n\n"
                "Enter the full path to bot directory:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Select existing bot error: {e}")

    # --- SCAN FOR BOTS ---
    @dp.callback_query_handler(lambda c: c.data.startswith("bm_scan_"))
    async def scan_for_bots(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            
            await callback.message.edit_text("🔍 <b>Scanning for bots...</b>", parse_mode='HTML')
            
            found_bots = await scan_server_for_bots(server_id, active_sessions)
            
            if found_bots:
                kb = InlineKeyboardMarkup(row_width=1)
                
                for bot_path in found_bots:
                    bot_name = os.path.basename(bot_path)
                    kb.add(InlineKeyboardButton(
                        f"📁 {bot_name}",
                        callback_data=f"bm_add_found_{server_id}_{bot_name}"
                    ))
                
                kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"bm_bots_{server_id}"))
                
                await callback.message.edit_text(
                    f"🔍 <b>Found {len(found_bots)} potential bots:</b>\n\n"
                    "Select bots to add to management:",
                    parse_mode='HTML',
                    reply_markup=kb
                )
            else:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"bm_bots_{server_id}"))
                
                await callback.message.edit_text(
                    "🔍 <b>No bots found</b>\n\n"
                    "No Python or Node.js bot projects were detected.",
                    parse_mode='HTML',
                    reply_markup=kb
                )
            
        except Exception as e:
            logger.error(f"Scan for bots error: {e}")
            await callback.message.edit_text("❌ Error scanning for bots.")

    # --- BOT CONTROL PANEL ---
    @dp.callback_query_handler(lambda c: c.data.startswith("bm_bot_"))
    async def bot_control_panel(callback: types.CallbackQuery):
        try:
            parts = callback.data.split('_', 3)
            server_id = parts[2]
            bot_name = parts[3]
            
            bot_info = await get_bot_info(server_id, bot_name, active_sessions)
            
            if not bot_info:
                await callback.message.edit_text("❌ Bot not found.")
                return
            
            kb = InlineKeyboardMarkup(row_width=2)
            
            # Control buttons based on status
            if bot_info['status'] == 'running':
                kb.add(
                    InlineKeyboardButton("⏹️ Stop", callback_data=f"bm_stop_{server_id}_{bot_name}"),
                    InlineKeyboardButton("🔄 Restart", callback_data=f"bm_restart_{server_id}_{bot_name}")
                )
            else:
                kb.add(InlineKeyboardButton("▶️ Start", callback_data=f"bm_start_{server_id}_{bot_name}"))
            
            # Management buttons
            kb.add(
                InlineKeyboardButton("📋 Logs", callback_data=f"bm_logs_{server_id}_{bot_name}"),
                InlineKeyboardButton("📊 Stats", callback_data=f"bm_stats_{server_id}_{bot_name}")
            )
            kb.add(
                InlineKeyboardButton("⚙️ Config", callback_data=f"bm_config_{server_id}_{bot_name}"),
                InlineKeyboardButton("🗑️ Delete", callback_data=f"bm_delete_{server_id}_{bot_name}")
            )
            kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"bm_bots_{server_id}"))
            
            status_icon = "🟢" if bot_info['status'] == 'running' else "🔴"
            
            text = (
                f"🤖 <b>{bot_name}</b>\n\n"
                f"{status_icon} Status: <b>{bot_info['status'].title()}</b>\n"
                f"📁 Path: <code>{bot_info['path']}</code>\n"
                f"🐍 Type: {bot_info['type']}\n"
                f"⏰ Uptime: {bot_info.get('uptime', 'N/A')}\n"
                f"🧠 Memory: {bot_info.get('memory', 'N/A')}\n"
                f"🔥 CPU: {bot_info.get('cpu', 'N/A')}"
            )
            
            await callback.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Bot control panel error: {e}")
            await callback.message.edit_text("❌ Error loading bot control panel.")

    # --- SERVICES SECTION ---
    @dp.callback_query_handler(lambda c: c.data.startswith("bm_services_"))
    async def services_section(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("🐳 Docker", callback_data=f"bm_docker_{server_id}"),
                InlineKeyboardButton("⚙️ System Services", callback_data=f"bm_systemd_{server_id}")
            )
            kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"bot_manager_{server_id}"))
            
            await callback.message.edit_text(
                "🔧 <b>Service Management</b>\n\n"
                "Choose service type to manage:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Services section error: {e}")

    # --- DOCKER MANAGEMENT ---
    @dp.callback_query_handler(lambda c: c.data.startswith("bm_docker_"))
    async def docker_management(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            
            # Get Docker containers
            containers = await get_docker_containers(server_id, active_sessions)
            
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton("➕ Add Docker Container", callback_data=f"bm_add_docker_{server_id}"))
            
            if containers:
                for container in containers:
                    status_icon = "🟢" if container['status'] == 'running' else "🔴"
                    kb.add(InlineKeyboardButton(
                        f"{status_icon} {container['name']} ({container['image']})",
                        callback_data=f"bm_docker_ctrl_{server_id}_{container['id']}"
                    ))
            
            kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"bm_services_{server_id}"))
            
            container_count = len(containers) if containers else 0
            running_count = len([c for c in containers if c['status'] == 'running']) if containers else 0
            
            text = (
                f"🐳 <b>Docker Management</b>\n\n"
                f"📊 <b>Statistics:</b>\n"
                f"Total Containers: {container_count}\n"
                f"Running: {running_count}\n"
                f"Stopped: {container_count - running_count}\n\n"
                f"Select a container to manage:"
            )
            
            await callback.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Docker management error: {e}")
            await callback.message.edit_text("❌ Error loading Docker management.")

    # --- ADD DOCKER CONTAINER ---
    @dp.callback_query_handler(lambda c: c.data.startswith("bm_add_docker_"))
    async def add_docker_container(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[3]
            
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("🔍 Auto Detect", callback_data=f"bm_docker_detect_{server_id}"),
                InlineKeyboardButton("🐙 From GitHub", callback_data=f"bm_docker_github_{server_id}")
            )
            kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"bm_docker_{server_id}"))
            
            await callback.message.edit_text(
                "🐳 <b>Add Docker Container</b>\n\n"
                "Choose deployment method:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Add Docker container error: {e}")

    # --- SYSTEM SERVICES ---
    @dp.callback_query_handler(lambda c: c.data.startswith("bm_systemd_"))
    async def system_services(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            
            # Get system services
            services = await get_system_services(server_id, active_sessions)
            
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(InlineKeyboardButton("➕ Add Service", callback_data=f"bm_add_service_{server_id}"))
            
            if services:
                for service in services:
                    status_icon = "🟢" if service['status'] == 'active' else "🔴"
                    kb.add(InlineKeyboardButton(
                        f"{status_icon} {service['name']}",
                        callback_data=f"bm_service_ctrl_{server_id}_{service['name']}"
                    ))
            
            kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"bm_services_{server_id}"))
            
            service_count = len(services) if services else 0
            active_count = len([s for s in services if s['status'] == 'active']) if services else 0
            
            text = (
                f"⚙️ <b>System Services</b>\n\n"
                f"📊 <b>Statistics:</b>\n"
                f"Total Services: {service_count}\n"
                f"Active: {active_count}\n"
                f"Inactive: {service_count - active_count}\n\n"
                f"Select a service to manage:"
            )
            
            await callback.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"System services error: {e}")
            await callback.message.edit_text("❌ Error loading system services.")

    # --- FUTURE FEATURES ---
    @dp.callback_query_handler(lambda c: c.data.startswith("bm_future_"))
    async def future_features(callback: types.CallbackQuery):
        try:
            server_id = callback.data.split('_')[2]
            
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("⬅️ Back", callback_data=f"bot_manager_{server_id}"))
            
            text = (
                "🚀 <b>Future Features</b>\n\n"
                "🔮 <b>Coming Soon:</b>\n\n"
                "🤖 <b>Advanced Bot Features:</b>\n"
                "• Bot Templates & Quick Deploy\n"
                "• Environment Variable Manager\n"
                "• Bot Performance Analytics\n"
                "• Auto-scaling & Load Balancing\n"
                "• Bot Health Monitoring\n\n"
                "🔧 <b>Service Enhancements:</b>\n"
                "• Kubernetes Integration\n"
                "• Service Dependency Mapping\n"
                "• Automated Backup Systems\n"
                "• Security Scanning\n"
                "• Performance Optimization\n\n"
                "📊 <b>Monitoring & Alerts:</b>\n"
                "• Real-time Dashboards\n"
                "• Custom Alert Rules\n"
                "• Integration with External Tools\n"
                "• Historical Data Analysis\n"
                "• Predictive Maintenance\n\n"
                "💡 Have suggestions? Let us know!"
            )
            
            await callback.message.edit_text(text, parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Future features error: {e}")

    # --- HANDLE TEXT INPUTS ---
    @dp.message_handler(lambda message: message.from_user.id in user_input and user_input[message.from_user.id].get('action', '').startswith('bot_'))
    async def handle_bot_inputs(message: types.Message):
        try:
            user_id = message.from_user.id
            data = user_input[user_id]
            action = data['action']
            server_id = data['server_id']
            
            if action == 'bot_upload':
                await handle_bot_upload_input(message, data, server_id)
            elif action == 'bot_github':
                await handle_bot_github_input(message, data, server_id)
            elif action == 'bot_select':
                await handle_bot_select_input(message, data, server_id)
                
        except Exception as e:
            logger.error(f"Handle bot inputs error: {e}")
            await message.answer("❌ Error processing input.")

    # --- HANDLE FILE UPLOADS FOR BOTS ---
    @dp.message_handler(content_types=types.ContentType.DOCUMENT)
    async def handle_bot_file_upload(message: types.Message):
        try:
            user_id = message.from_user.id
            if user_id not in user_input or user_input[user_id].get('action') != 'bot_upload' or user_input[user_id].get('step') != 'file':
                return
            
            data = user_input[user_id]
            server_id = data['server_id']
            bot_name = data['bot_name']
            
            await message.answer("📤 Uploading and deploying bot...")
            
            # Download file
            file = await bot.download_file_by_id(message.document.file_id)
            file_content = file.read()
            
            # Deploy bot
            success = await deploy_bot_from_upload(server_id, bot_name, file_content, active_sessions)
            
            if success:
                await message.answer("✅ Bot deployed successfully!")
            else:
                await message.answer("❌ Failed to deploy bot.")
            
            user_input.pop(user_id, None)
            
            # Return to bots section
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("🤖 Back to Bots", callback_data=f"bm_bots_{server_id}"))
            await message.answer("Choose an option:", reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Bot file upload error: {e}")
            await message.answer("❌ Error uploading bot files.")

# --- HELPER FUNCTIONS ---

async def get_server_bots(server_id, active_sessions):
    """Get list of managed bots on server"""
    try:
        if server_id not in active_sessions:
            return []
        
        ssh = active_sessions[server_id]
        
        # Check for bot management directory
        command = "ls -la ~/bots/ 2>/dev/null || echo 'NO_BOTS_DIR'"
        stdin, stdout, stderr = ssh.exec_command(command)
        output = stdout.read().decode().strip()
        
        if output == 'NO_BOTS_DIR':
            return []
        
        bots = []
        # Parse bot directories and check status
        lines = output.split('\n')[1:]  # Skip total line
        
        for line in lines:
            if not line.strip() or line.startswith('total'):
                continue
            
            parts = line.split()
            if len(parts) >= 9 and parts[0].startswith('d'):
                bot_name = ' '.join(parts[8:])
                if bot_name not in ['.', '..']:
                    bot_info = await get_bot_status(server_id, bot_name, ssh)
                    bots.append(bot_info)
        
        return bots
        
    except Exception as e:
        logger.error(f"Get server bots error: {e}")
        return []

async def get_bot_status(server_id, bot_name, ssh):
    """Get status of a specific bot"""
    try:
        # Check if bot process is running
        command = f"pgrep -f 'python.*{bot_name}|node.*{bot_name}' || echo 'NOT_RUNNING'"
        stdin, stdout, stderr = ssh.exec_command(command)
        output = stdout.read().decode().strip()
        
        status = 'running' if output != 'NOT_RUNNING' else 'stopped'
        
        # Detect bot type
        command = f"ls ~/bots/{bot_name}/ | grep -E '\\.(py|js)$' | head -1"
        stdin, stdout, stderr = ssh.exec_command(command)
        file_output = stdout.read().decode().strip()
        
        bot_type = 'Python' if file_output.endswith('.py') else 'Node.js' if file_output.endswith('.js') else 'Unknown'
        
        return {
            'name': bot_name,
            'status': status,
            'type': bot_type,
            'path': f'~/bots/{bot_name}',
            'pid': output if status == 'running' else None
        }
        
    except Exception as e:
        logger.error(f"Get bot status error: {e}")
        return {
            'name': bot_name,
            'status': 'unknown',
            'type': 'Unknown',
            'path': f'~/bots/{bot_name}',
            'pid': None
        }

async def scan_server_for_bots(server_id, active_sessions):
    """Scan server for potential bot projects"""
    try:
        if server_id not in active_sessions:
            return []
        
        ssh = active_sessions[server_id]
        
        # Search for Python and Node.js projects
        search_paths = [
            "find ~ -name '*.py' -path '*/bot*' -o -name 'main.py' -o -name 'bot.py' 2>/dev/null",
            "find ~ -name 'package.json' -path '*/bot*' -o -name 'index.js' -path '*/bot*' 2>/dev/null"
        ]
        
        found_bots = []
        
        for search_cmd in search_paths:
            stdin, stdout, stderr = ssh.exec_command(search_cmd)
            output = stdout.read().decode().strip()
            
            if output:
                for file_path in output.split('\n'):
                    if file_path.strip():
                        bot_dir = os.path.dirname(file_path)
                        if bot_dir not in found_bots:
                            found_bots.append(bot_dir)
        
        return found_bots[:10]  # Limit to 10 results
        
    except Exception as e:
        logger.error(f"Scan server for bots error: {e}")
        return []

async def get_bot_info(server_id, bot_name, active_sessions):
    """Get detailed information about a bot"""
    try:
        if server_id not in active_sessions:
            return None
        
        ssh = active_sessions[server_id]
        bot_info = await get_bot_status(server_id, bot_name, ssh)
        
        if bot_info['status'] == 'running' and bot_info['pid']:
            # Get additional process information
            command = f"ps -p {bot_info['pid']} -o pid,ppid,pcpu,pmem,etime,cmd --no-headers 2>/dev/null || echo 'NO_PROCESS'"
            stdin, stdout, stderr = ssh.exec_command(command)
            output = stdout.read().decode().strip()
            
            if output != 'NO_PROCESS':
                parts = output.split(None, 5)
                if len(parts) >= 5:
                    bot_info['cpu'] = f"{parts[2]}%"
                    bot_info['memory'] = f"{parts[3]}%"
                    bot_info['uptime'] = parts[4]
        
        return bot_info
        
    except Exception as e:
        logger.error(f"Get bot info error: {e}")
        return None

async def get_docker_containers(server_id, active_sessions):
    """Get Docker containers on server"""
    try:
        if server_id not in active_sessions:
            return []
        
        ssh = active_sessions[server_id]
        
        # Check if Docker is installed
        command = "docker --version 2>/dev/null || echo 'NO_DOCKER'"
        stdin, stdout, stderr = ssh.exec_command(command)
        output = stdout.read().decode().strip()
        
        if output == 'NO_DOCKER':
            return []
        
        # Get container list
        command = "docker ps -a --format 'table {{.ID}}\\t{{.Names}}\\t{{.Image}}\\t{{.Status}}' | tail -n +2"
        stdin, stdout, stderr = ssh.exec_command(command)
        output = stdout.read().decode().strip()
        
        containers = []
        if output:
            for line in output.split('\n'):
                if line.strip():
                    parts = line.split('\t')
                    if len(parts) >= 4:
                        containers.append({
                            'id': parts[0],
                            'name': parts[1],
                            'image': parts[2],
                            'status': 'running' if 'Up' in parts[3] else 'stopped'
                        })
        
        return containers
        
    except Exception as e:
        logger.error(f"Get Docker containers error: {e}")
        return []

async def get_system_services(server_id, active_sessions):
    """Get system services"""
    try:
        if server_id not in active_sessions:
            return []
        
        ssh = active_sessions[server_id]
        
        # Get systemd services
        command = "systemctl list-units --type=service --state=active,inactive --no-pager --no-legend | head -20"
        stdin, stdout, stderr = ssh.exec_command(command)
        output = stdout.read().decode().strip()
        
        services = []
        if output:
            for line in output.split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 4:
                        service_name = parts[0].replace('.service', '')
                        status = 'active' if parts[2] == 'active' else 'inactive'
                        services.append({
                            'name': service_name,
                            'status': status,
                            'description': ' '.join(parts[4:]) if len(parts) > 4 else ''
                        })
        
        return services
        
    except Exception as e:
        logger.error(f"Get system services error: {e}")
        return []

async def deploy_bot_from_upload(server_id, bot_name, file_content, active_sessions):
    """Deploy bot from uploaded file"""
    try:
        if server_id not in active_sessions:
            return False
        
        ssh = active_sessions[server_id]
        sftp = ssh.open_sftp()
        
        # Create bots directory if it doesn't exist
        command = "mkdir -p ~/bots"
        stdin, stdout, stderr = ssh.exec_command(command)
        stdout.read()
        
        # Create bot directory
        bot_dir = f"~/bots/{bot_name}"
        command = f"mkdir -p {bot_dir}"
        stdin, stdout, stderr = ssh.exec_command(command)
        stdout.read()
        
        # Upload and extract file
        with tempfile.NamedTemporaryFile() as temp_file:
            temp_file.write(file_content)
            temp_file.flush()
            
            remote_path = f"{bot_dir}/bot_files.zip"
            sftp.put(temp_file.name, remote_path)
        
        # Extract if it's a zip file
        command = f"cd {bot_dir} && unzip -o bot_files.zip && rm bot_files.zip"
        stdin, stdout, stderr = ssh.exec_command(command)
        stdout.read()
        
        # Try to install dependencies
        command = f"cd {bot_dir} && (pip install -r requirements.txt 2>/dev/null || npm install 2>/dev/null || echo 'No dependencies')"
        stdin, stdout, stderr = ssh.exec_command(command)
        stdout.read()
        
        sftp.close()
        return True
        
    except Exception as e:
        logger.error(f"Deploy bot from upload error: {e}")
        return False

async def handle_bot_upload_input(message, data, server_id):
    """Handle bot upload text inputs"""
    step = data['step']
    
    if step == 'name':
        data['bot_name'] = message.text.strip()
        data['step'] = 'file'
        await message.answer(
            f"📤 Bot name set to: <b>{data['bot_name']}</b>\n\n"
            "Now send the bot files (zip archive):",
            parse_mode='HTML'
        )
    
async def handle_bot_github_input(message, data, server_id):
    """Handle GitHub deployment inputs"""
    step = data['step']
    
    if step == 'name':
        data['bot_name'] = message.text.strip()
        data['step'] = 'repo'
        await message.answer(
            f"🐙 Bot name set to: <b>{data['bot_name']}</b>\n\n"
            "Enter GitHub repository URL:",
            parse_mode='HTML'
        )
    elif step == 'repo':
        data['repo_url'] = message.text.strip()
        # TODO: Implement GitHub cloning
        await message.answer("🚧 GitHub deployment coming soon!")

async def handle_bot_select_input(message, data, server_id):
    """Handle existing bot selection"""
    bot_path = message.text.strip()
    # TODO: Implement existing bot addition
    await message.answer(f"📁 Selected path: {bot_path}\n🚧 Feature coming soon!")
