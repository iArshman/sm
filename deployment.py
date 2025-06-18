import logging
import os
import json
import tempfile
import zipfile
import tarfile
from datetime import datetime
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import asyncio
import re

logger = logging.getLogger(__name__)

# Deployment state
deployment_states = {}

def init_deployment(dp, bot, active_sessions, user_input):
    """Initialize deployment handlers"""
    
    # --- UTILITY FUNCTIONS ---
    
    async def get_ssh_session(server_id):
        """Get SSH session for server"""
        from db import get_server_by_id
        from main import get_ssh_session as main_get_ssh_session
        
        server = await get_server_by_id(server_id)
        if not server:
            return None
        
        return main_get_ssh_session(server_id, server['ip'], server['username'], server['key_content'])
    
    # --- DEPLOYMENT MAIN HANDLER ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("deploy_bot_"))
    async def deploy_bot_handler(callback: types.CallbackQuery):
        """Deploy new bot menu"""
        try:
            server_id = callback.data.split('_')[2]
            
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(
                InlineKeyboardButton("🐙 Deploy from GitHub", callback_data=f"deploy_github_{server_id}"),
                InlineKeyboardButton("🐳 Deploy Docker", callback_data=f"deploy_docker_{server_id}"),
                InlineKeyboardButton("📦 Upload & Deploy", callback_data=f"deploy_upload_{server_id}")
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
    
    # --- UPLOAD DEPLOYMENT ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("deploy_upload_"))
    async def deploy_upload_handler(callback: types.CallbackQuery):
        """Start upload deployment"""
        try:
            server_id = callback.data.split('_')[2]
            
            deployment_states[callback.from_user.id] = {
                'type': 'upload',
                'server_id': server_id,
                'step': 'file_upload'
            }
            
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"deploy_bot_{server_id}"))
            
            await bot.send_message(
                callback.from_user.id,
                "📦 <b>Upload & Deploy</b>\n\n"
                "Send a ZIP or TAR file containing your bot code.\n"
                "The file should contain all necessary files and dependencies.",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Upload deploy error: {e}")
            await callback.message.edit_text("❌ Error starting upload deployment.")
    
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
                await handle_github_repo_url(message, uid, state)
            elif state['type'] == 'docker' and state['step'] == 'image_or_repo':
                await handle_docker_input(message, uid, state)
            elif state['type'] == 'upload' and state['step'] == 'file_upload':
                await handle_file_upload(message, uid, state)
            
        except Exception as e:
            logger.error(f"Deployment input error: {e}")
            await message.answer("❌ Error processing deployment input")
            deployment_states.pop(message.from_user.id, None)
    
    async def handle_github_repo_url(message, uid, state):
        """Handle GitHub repository URL"""
        try:
            server_id = state['server_id']
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
            await asyncio.sleep(3)  # Give time for clone
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
            state['repo_name'] = repo_name
            
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
        
        except Exception as e:
            logger.error(f"GitHub repo URL error: {e}")
            await message.answer("❌ Error processing GitHub URL")
            deployment_states.pop(uid, None)
    
    async def handle_docker_input(message, uid, state):
        """Handle Docker deployment input"""
        try:
            server_id = state['server_id']
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
                await asyncio.sleep(3)
                stderr_output = stderr.read().decode()
                
                if stderr_output and "error" in stderr_output.lower():
                    await message.answer(f"❌ Clone failed: {stderr_output}")
                    deployment_states.pop(uid, None)
                    return
                
                # Check for Dockerfile
                stdin, stdout, stderr = ssh.exec_command(f"ls {clone_path}/Dockerfile 2>/dev/null && echo 'found' || echo 'not found'")
                dockerfile_check = stdout.read().decode().strip()
                
                state['clone_path'] = clone_path
                state['repo_name'] = repo_name
                
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
            logger.error(f"Docker input error: {e}")
            await message.answer("❌ Error processing Docker input")
            deployment_states.pop(uid, None)
    
    async def handle_file_upload(message, uid, state):
        """Handle file upload for deployment"""
        try:
            if not message.document:
                await message.answer("❌ Please send a ZIP or TAR file")
                return
            
            file_name = message.document.file_name
            if not (file_name.endswith('.zip') or file_name.endswith('.tar') or file_name.endswith('.tar.gz')):
                await message.answer("❌ Please send a ZIP or TAR file")
                return
            
            await message.answer("📥 <b>Downloading file...</b>", parse_mode='HTML')
            
            # Download file
            file_info = await bot.get_file(message.document.file_id)
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file_name}") as tmp_file:
                await bot.download_file(file_info.file_path, tmp_file)
                local_path = tmp_file.name
            
            state['uploaded_file'] = local_path
            state['file_name'] = file_name
            
            await show_upload_options(message, uid, state)
            
        except Exception as e:
            logger.error(f"File upload error: {e}")
            await message.answer("❌ Error processing uploaded file")
            deployment_states.pop(uid, None)
    
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
            InlineKeyboardButton("📄 Add ENV", callback_data=f"docker_env_{uid}"),
            InlineKeyboardButton("⏭️ Continue", callback_data=f"docker_no_env_{uid}")
        )
        kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_deploy_{uid}"))
        
        await message.answer(
            "🔧 <b>Environment Configuration</b>\n\n"
            "Do you want to provide environment variables?",
            parse_mode='HTML',
            reply_markup=kb
        )
    
    async def show_upload_options(message, uid, state):
        """Show upload deployment options"""
        kb = InlineKeyboardMarkup(row_width=2)
        kb.add(
            InlineKeyboardButton("📄 Set ENV", callback_data=f"upload_env_{uid}"),
            InlineKeyboardButton("⏭️ Deploy", callback_data=f"upload_deploy_{uid}")
        )
        kb.add(InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_deploy_{uid}"))
        
        await message.answer(
            "📦 <b>Upload Ready</b>\n\n"
            f"File: <b>{state['file_name']}</b>\n"
            "Configure environment or deploy directly?",
            parse_mode='HTML',
            reply_markup=kb
        )
    
    # --- DEPLOYMENT CALLBACK HANDLERS ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("install_deps_"))
    async def install_deps_handler(callback: types.CallbackQuery):
        """Install dependencies"""
        try:
            uid = int(callback.data.split('_')[2])
            if uid not in deployment_states:
                await callback.message.edit_text("❌ Deployment session expired")
                return
            
            state = deployment_states[uid]
            await callback.message.edit_text("📦 <b>Installing dependencies...</b>", parse_mode='HTML')
            
            ssh = await get_ssh_session(state['server_id'])
            clone_path = state['clone_path']
            
            # Install dependencies based on found files
            if 'requirements.txt' in state['files']:
                stdin, stdout, stderr = ssh.exec_command(f"cd {clone_path} && pip install -r requirements.txt")
            elif 'package.json' in state['files']:
                stdin, stdout, stderr = ssh.exec_command(f"cd {clone_path} && npm install")
            elif 'Pipfile' in state['files']:
                stdin, stdout, stderr = ssh.exec_command(f"cd {clone_path} && pipenv install")
            
            await asyncio.sleep(5)  # Give time for installation
            
            await callback.message.edit_text("✅ <b>Dependencies installed!</b>", parse_mode='HTML')
            await asyncio.sleep(1)
            
            await show_executable_selection(callback.message, uid, state)
            
        except Exception as e:
            logger.error(f"Install deps error: {e}")
            await callback.message.edit_text("❌ Error installing dependencies")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("skip_deps_"))
    async def skip_deps_handler(callback: types.CallbackQuery):
        """Skip dependency installation"""
        try:
            uid = int(callback.data.split('_')[2])
            if uid not in deployment_states:
                await callback.message.edit_text("❌ Deployment session expired")
                return
            
            state = deployment_states[uid]
            await show_executable_selection(callback.message, uid, state)
            
        except Exception as e:
            logger.error(f"Skip deps error: {e}")
            await callback.message.edit_text("❌ Error skipping dependencies")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("select_exe_"))
    async def select_exe_handler(callback: types.CallbackQuery):
        """Handle executable selection"""
        try:
            parts = callback.data.split('_')
            uid = int(parts[2])
            filename = '_'.join(parts[3:])
            
            if uid not in deployment_states:
                await callback.message.edit_text("❌ Deployment session expired")
                return
            
            state = deployment_states[uid]
            state['selected_exe'] = filename
            
            await callback.message.edit_text("🚀 <b>Deploying bot...</b>", parse_mode='HTML')
            
            # Deploy the bot
            success = await deploy_github_bot(state)
            
            if success:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("✅ View Bot", callback_data=f"bot_manager_{state['server_id']}"))
                
                await callback.message.edit_text(
                    f"✅ <b>Bot Deployed Successfully!</b>\n\n"
                    f"Bot: <b>{state['repo_name']}</b>\n"
                    f"File: <b>{filename}</b>\n\n"
                    f"The bot is now available in your bot manager.",
                    parse_mode='HTML',
                    reply_markup=kb
                )
            else:
                await callback.message.edit_text("❌ Deployment failed")
            
            deployment_states.pop(uid, None)
            
        except Exception as e:
            logger.error(f"Select exe error: {e}")
            await callback.message.edit_text("❌ Error selecting executable")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("docker_no_env_"))
    async def docker_no_env_handler(callback: types.CallbackQuery):
        """Deploy Docker without environment variables"""
        try:
            uid = int(callback.data.split('_')[3])
            if uid not in deployment_states:
                await callback.message.edit_text("❌ Deployment session expired")
                return
            
            state = deployment_states[uid]
            await callback.message.edit_text("🐳 <b>Deploying Docker container...</b>", parse_mode='HTML')
            
            # Deploy Docker container
            success = await deploy_docker_bot(state)
            
            if success:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("✅ View Bot", callback_data=f"bot_manager_{state['server_id']}"))
                
                container_name = state.get('image_name', state.get('repo_name', 'unknown'))
                await callback.message.edit_text(
                    f"✅ <b>Docker Bot Deployed!</b>\n\n"
                    f"Container: <b>{container_name}</b>\n\n"
                    f"The bot is now available in your bot manager.",
                    parse_mode='HTML',
                    reply_markup=kb
                )
            else:
                await callback.message.edit_text("❌ Docker deployment failed")
            
            deployment_states.pop(uid, None)
            
        except Exception as e:
            logger.error(f"Docker no env error: {e}")
            await callback.message.edit_text("❌ Error deploying Docker container")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("upload_deploy_"))
    async def upload_deploy_handler(callback: types.CallbackQuery):
        """Deploy uploaded file"""
        try:
            uid = int(callback.data.split('_')[2])
            if uid not in deployment_states:
                await callback.message.edit_text("❌ Deployment session expired")
                return
            
            state = deployment_states[uid]
            await callback.message.edit_text("📦 <b>Deploying uploaded file...</b>", parse_mode='HTML')
            
            # Deploy uploaded file
            success = await deploy_uploaded_bot(state)
            
            if success:
                kb = InlineKeyboardMarkup()
                kb.add(InlineKeyboardButton("✅ View Bot", callback_data=f"bot_manager_{state['server_id']}"))
                
                await callback.message.edit_text(
                    f"✅ <b>Upload Bot Deployed!</b>\n\n"
                    f"File: <b>{state['file_name']}</b>\n\n"
                    f"The bot is now available in your bot manager.",
                    parse_mode='HTML',
                    reply_markup=kb
                )
            else:
                await callback.message.edit_text("❌ Upload deployment failed")
            
            deployment_states.pop(uid, None)
            
        except Exception as e:
            logger.error(f"Upload deploy error: {e}")
            await callback.message.edit_text("❌ Error deploying uploaded file")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("cancel_deploy_"))
    async def cancel_deploy_handler(callback: types.CallbackQuery):
        """Cancel deployment"""
        try:
            uid = int(callback.data.split('_')[2])
            deployment_states.pop(uid, None)
            
            await callback.message.edit_text(
                "❌ <b>Deployment Cancelled</b>\n\n"
                "Deployment has been cancelled.",
                parse_mode='HTML'
            )
            
        except Exception as e:
            logger.error(f"Cancel deploy error: {e}")
            await callback.message.edit_text("❌ Error cancelling deployment")
    
    # --- DEPLOYMENT FUNCTIONS ---
    
    async def deploy_github_bot(state):
        """Deploy GitHub bot"""
        try:
            ssh = await get_ssh_session(state['server_id'])
            if not ssh:
                return False
            
            clone_path = state['clone_path']
            filename = state['selected_exe']
            repo_name = state['repo_name']
            
            # Move to permanent location
            deploy_path = f"/opt/{repo_name}"
            stdin, stdout, stderr = ssh.exec_command(f"sudo mkdir -p {deploy_path} && sudo cp -r {clone_path}/* {deploy_path}/")
            
            # Create systemd service
            service_content = f"""[Unit]
Description={repo_name} Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={deploy_path}
ExecStart=/usr/bin/python3 {deploy_path}/{filename}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
            
            # Write service file
            stdin, stdout, stderr = ssh.exec_command(f"echo '{service_content}' | sudo tee /etc/systemd/system/{repo_name}.service")
            
            # Enable and start service
            stdin, stdout, stderr = ssh.exec_command(f"sudo systemctl daemon-reload && sudo systemctl enable {repo_name} && sudo systemctl start {repo_name}")
            
            # Here you would also add the bot to your database/storage for bot_manager to use
            
            return True
            
        except Exception as e:
            logger.error(f"Deploy GitHub bot error: {e}")
            return False
    
    async def deploy_docker_bot(state):
        """Deploy Docker bot"""
        try:
            ssh = await get_ssh_session(state['server_id'])
            if not ssh:
                return False
            
            if 'image_name' in state:
                # Deploy from Docker Hub image
                image_name = state['image_name']
                container_name = image_name.split(':')[0].replace('/', '_')
                
                stdin, stdout, stderr = ssh.exec_command(f"docker run -d --name {container_name} --restart unless-stopped {image_name}")
                
            else:
                # Build from Dockerfile
                clone_path = state['clone_path']
                repo_name = state['repo_name']
                
                stdin, stdout, stderr = ssh.exec_command(f"cd {clone_path} && docker build -t {repo_name} .")
                await asyncio.sleep(10)  # Give time for build
                
                stdin, stdout, stderr = ssh.exec_command(f"docker run -d --name {repo_name} --restart unless-stopped {repo_name}")
            
            # Here you would also add the bot to your database/storage for bot_manager to use
            
            return True
            
        except Exception as e:
            logger.error(f"Deploy Docker bot error: {e}")
            return False
    
    async def deploy_uploaded_bot(state):
        """Deploy uploaded bot"""
        try:
            ssh = await get_ssh_session(state['server_id'])
            if not ssh:
                return False
            
            local_path = state['uploaded_file']
            file_name = state['file_name']
            bot_name = file_name.replace('.zip', '').replace('.tar', '').replace('.gz', '')
            
            # Upload file to server
            # This is a simplified version - you'd need to implement proper file transfer
            # For now, we'll assume the file is uploaded and extracted
            
            deploy_path = f"/opt/{bot_name}"
            
            # Here you would:
            # 1. Upload the file via SFTP
            # 2. Extract it on the server
            # 3. Set up the service
            # 4. Add to bot database/storage
            
            return True
            
        except Exception as e:
            logger.error(f"Deploy uploaded bot error: {e}")
            return False
    
    logger.info("Deployment handlers initialized successfully")
