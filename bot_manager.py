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

            # You probably intended to continue logic here
            # For now, let's simulate with a placeholder
            return {"id": bot_id, "status": "running"}

        except Exception as e:
            logger.error(f"Error getting bot details for {bot_id}: {e}")
            return None
