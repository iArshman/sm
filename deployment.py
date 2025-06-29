import logging
import os
import json
import tempfile
import zipfile
import tarfile
import asyncio
import re
import hashlib
from datetime import datetime
from aiogram import types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# Deployment state
deployment_states = {}
callback_cache = {}  # Cache for long callback data

def get_callback_hash(data):
    """Generate short hash for long callback data"""
    return hashlib.md5(data.encode()).hexdigest()[:8]

def cache_callback_data(data):
    """Cache callback data and return hash if too long"""
    if len(data) <= 60:  # Safe length for callback data
        return data
    
    callback_hash = get_callback_hash(data)
    callback_cache[callback_hash] = data
    return callback_hash

def get_cached_callback_data(identifier):
    """Get callback data from cache or return identifier if not cached"""
    return callback_cache.get(identifier, identifier)

def init_deployment(dp, bot, active_sessions, user_input):
    """Initialize deployment handlers"""
    
    # --- UTILITY FUNCTIONS ---
    
    async def get_ssh_session(server_id):
        """Get SSH session for server"""
        if server_id in active_sessions:
            return active_sessions[server_id]
        return None
    
    # --- DEPLOYMENT MAIN HANDLER ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("deploy_") or get_cached_callback_data(c.data).startswith("deploy_"))
    async def deploy_handler(callback: types.CallbackQuery):
        """Main deployment menu"""
        try:
            # Get actual callback data
            callback_data = get_cached_callback_data(callback.data)
            server_id = callback_data.split('_')[1]
            
            github_callback = cache_callback_data(f"deploy_github_{server_id}")
            docker_callback = cache_callback_data(f"deploy_docker_{server_id}")
            upload_callback = cache_callback_data(f"deploy_upload_{server_id}")
            back_callback = cache_callback_data(f"server_{server_id}")
            
            kb = InlineKeyboardMarkup(row_width=1)
            kb.add(
                InlineKeyboardButton("🐙 Deploy from GitHub", callback_data=github_callback),
                InlineKeyboardButton("🐳 Deploy with Docker", callback_data=docker_callback),
                InlineKeyboardButton("📦 Upload & Deploy", callback_data=upload_callback)
            )
            kb.add(InlineKeyboardButton("⬅️ Back to Server", callback_data=back_callback))
            
            await callback.message.edit_text(
                "🚀 <b>Deployment Center</b>\n\n"
                "Choose your deployment method:",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Deploy handler error: {e}")
            await callback.message.edit_text("❌ Error loading deployment menu.")
    
    # --- GITHUB DEPLOYMENT ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("deploy_github_") or get_cached_callback_data(c.data).startswith("deploy_github_"))
    async def deploy_github_handler(callback: types.CallbackQuery):
        """Start GitHub deployment"""
        try:
            # Get actual callback data
            callback_data = get_cached_callback_data(callback.data)
            server_id = callback_data.split('_')[2]
            
            deployment_states[callback.from_user.id] = {
                'type': 'github',
                'server_id': server_id,
                'step': 'repo_url'
            }
            
            cancel_callback = cache_callback_data(f"deploy_{server_id}")
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=cancel_callback))
            
            await bot.send_message(
                callback.from_user.id,
                "🐙 <b>GitHub Deployment</b>\n\n"
                "Send the GitHub repository URL:\n"
                "Example: <code>https://github.com/username/repository</code>\n\n"
                "Supported formats:\n"
                "• Public repositories\n"
                "• Private repositories (with token)\n"
                "• Specific branches: <code>https://github.com/user/repo/tree/branch</code>",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"GitHub deploy error: {e}")
            await callback.message.edit_text("❌ Error starting GitHub deployment.")
    
    # --- DOCKER DEPLOYMENT ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("deploy_docker_") or get_cached_callback_data(c.data).startswith("deploy_docker_"))
    async def deploy_docker_handler(callback: types.CallbackQuery):
        """Start Docker deployment"""
        try:
            # Get actual callback data
            callback_data = get_cached_callback_data(callback.data)
            server_id = callback_data.split('_')[2]
            
            deployment_states[callback.from_user.id] = {
                'type': 'docker',
                'server_id': server_id,
                'step': 'image_or_repo'
            }
            
            cancel_callback = cache_callback_data(f"deploy_{server_id}")
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=cancel_callback))
            
            await bot.send_message(
                callback.from_user.id,
                "🐳 <b>Docker Deployment</b>\n\n"
                "Send either:\n\n"
                "📦 <b>Docker Hub Image:</b>\n"
                "<code>nginx:latest</code>\n"
                "<code>python:3.9</code>\n"
                "<code>node:16-alpine</code>\n\n"
                "🐙 <b>GitHub Repository:</b>\n"
                "<code>https://github.com/user/repo</code>\n"
                "(Must contain Dockerfile)",
                parse_mode='HTML',
                reply_markup=kb
            )
            
        except Exception as e:
            logger.error(f"Docker deploy error: {e}")
            await callback.message.edit_text("❌ Error starting Docker deployment.")
    
    # --- UPLOAD DEPLOYMENT ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("deploy_upload_") or get_cached_callback_data(c.data).startswith("deploy_upload_"))
    async def deploy_upload_handler(callback: types.CallbackQuery):
        """Start upload deployment"""
        try:
            # Get actual callback data
            callback_data = get_cached_callback_data(callback.data)
            server_id = callback_data.split('_')[2]
            
            deployment_states[callback.from_user.id] = {
                'type': 'upload',
                'server_id': server_id,
                'step': 'file_upload'
            }
            
            cancel_callback = cache_callback_data(f"deploy_{server_id}")
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=cancel_callback))
            
            await bot.send_message(
                callback.from_user.id,
                "📦 <b>Upload & Deploy</b>\n\n"
                "Send a compressed file containing your application:\n\n"
                "📁 <b>Supported formats:</b>\n"
                "• ZIP files (.zip)\n"
                "• TAR files (.tar, .tar.gz, .tgz)\n"
                "• RAR files (.rar)\n\n"
                "📋 <b>Requirements:</b>\n"
                "• Include all source code\n"
                "• Include dependencies file (requirements.txt, package.json, etc.)\n"
                "• Max size: 50MB",
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
            
            # Validate GitHub URL
            if not repo_url.startswith('https://github.com/'):
                await message.answer("❌ Please provide a valid GitHub URL starting with https://github.com/")
                return
            
            # Parse URL for branch
            branch = 'main'
            if '/tree/' in repo_url:
                parts = repo_url.split('/tree/')
                repo_url = parts[0]
                branch = parts[1]
            
            await message.answer("📥 <b>Cloning repository...</b>", parse_mode='HTML')
            
            # Get SSH session
            ssh = await get_ssh_session(server_id)
            if not ssh:
                await message.answer("❌ SSH connection failed")
                deployment_states.pop(uid, None)
                return
            
            # Extract repo info
            repo_parts = repo_url.replace('https://github.com/', '').split('/')
            if len(repo_parts) < 2:
                await message.answer("❌ Invalid GitHub URL format")
                deployment_states.pop(uid, None)
                return
            
            repo_owner = repo_parts[0]
            repo_name = repo_parts[1].replace('.git', '')
            
            # Create deployment directory
            deploy_name = f"{repo_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            deploy_path = f"/opt/deployments/{deploy_name}"
            
            # Clone repository
            clone_cmd = f"mkdir -p /opt/deployments && cd /opt/deployments && git clone -b {branch} {repo_url} {deploy_name}"
            stdin, stdout, stderr = ssh.exec_command(clone_cmd)
            
            # Wait for clone to complete
            exit_status = stdout.channel.recv_exit_status()
            stderr_output = stderr.read().decode().strip()
            
            if exit_status != 0:
                await message.answer(f"❌ Clone failed: {stderr_output}")
                deployment_states.pop(uid, None)
                return
            
            # Check for common files
            stdin, stdout, stderr = ssh.exec_command(f"ls -la {deploy_path}/")
            files_output = stdout.read().decode().strip()
            
            # Detect project type and dependencies
            project_info = await detect_project_type(ssh, deploy_path)
            
            state.update({
                'repo_url': repo_url,
                'repo_name': repo_name,
                'deploy_path': deploy_path,
                'deploy_name': deploy_name,
                'branch': branch,
                'project_info': project_info
            })
            
            # Show project detection results
            await show_project_detection(message, uid, state)
            
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
                await message.answer("🐳 <b>Preparing Docker build from repository...</b>", parse_mode='HTML')
                
                # First clone the repo
                await handle_github_repo_url(message, uid, state)
                if uid not in deployment_states:
                    return
                
                # Check for Dockerfile
                ssh = await get_ssh_session(server_id)
                deploy_path = deployment_states[uid]['deploy_path']
                
                stdin, stdout, stderr = ssh.exec_command(f"test -f {deploy_path}/Dockerfile && echo 'found' || echo 'not found'")
                dockerfile_check = stdout.read().decode().strip()
                
                if 'not found' in dockerfile_check:
                    await message.answer("❌ No Dockerfile found in repository")
                    deployment_states.pop(uid, None)
                    return
                
                deployment_states[uid]['docker_type'] = 'build'
                await show_docker_options(message, uid, deployment_states[uid])
                
            else:
                # Docker Hub image
                deployment_states[uid].update({
                    'docker_type': 'pull',
                    'image_name': input_text
                })
                await show_docker_options(message, uid, deployment_states[uid])
        
        except Exception as e:
            logger.error(f"Docker input error: {e}")
            await message.answer("❌ Error processing Docker input")
            deployment_states.pop(uid, None)
    
    async def handle_file_upload(message, uid, state):
        """Handle file upload for deployment"""
        try:
            if not message.document:
                await message.answer("❌ Please send a compressed file (ZIP, TAR, etc.)")
                return
            
            file_name = message.document.file_name
            file_size = message.document.file_size
            
            # Check file size (50MB limit)
            if file_size > 50 * 1024 * 1024:
                await message.answer("❌ File too large. Maximum size is 50MB.")
                return
            
            # Check file extension
            supported_extensions = ['.zip', '.tar', '.tar.gz', '.tgz', '.tar.bz2', '.rar']
            if not any(file_name.lower().endswith(ext) for ext in supported_extensions):
                await message.answer("❌ Unsupported file format. Please send ZIP, TAR, or RAR files.")
                return
            
            await message.answer("📥 <b>Downloading and extracting file...</b>", parse_mode='HTML')
            
            # Download file
            file_info = await bot.get_file(message.document.file_id)
            
            # Get SSH session
            ssh = await get_ssh_session(state['server_id'])
            if not ssh:
                await message.answer("❌ SSH connection failed")
                deployment_states.pop(uid, None)
                return
            
            # Create deployment directory
            deploy_name = f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            deploy_path = f"/opt/deployments/{deploy_name}"
            
            # Create directory on server
            stdin, stdout, stderr = ssh.exec_command(f"mkdir -p {deploy_path}")
            
            # Download file to local temp
            with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file_name}") as tmp_file:
                await bot.download_file(file_info.file_path, tmp_file)
                local_path = tmp_file.name
            
            # Upload file to server
            sftp = ssh.open_sftp()
            remote_file_path = f"{deploy_path}/{file_name}"
            sftp.put(local_path, remote_file_path)
            sftp.close()
            
            # Extract file on server
            extract_success = await extract_uploaded_file(ssh, deploy_path, file_name)
            
            if not extract_success:
                await message.answer("❌ Failed to extract uploaded file")
                deployment_states.pop(uid, None)
                return
            
            # Clean up local temp file
            os.unlink(local_path)
            
            # Detect project type
            project_info = await detect_project_type(ssh, deploy_path)
            
            state.update({
                'deploy_path': deploy_path,
                'deploy_name': deploy_name,
                'file_name': file_name,
                'project_info': project_info
            })
            
            await show_project_detection(message, uid, state)
            
        except Exception as e:
            logger.error(f"File upload error: {e}")
            await message.answer("❌ Error processing uploaded file")
            deployment_states.pop(uid, None)
    
    async def detect_project_type(ssh, deploy_path):
        """Detect project type and configuration"""
        project_info = {
            'type': 'unknown',
            'language': 'unknown',
            'dependencies': [],
            'main_files': [],
            'has_dockerfile': False,
            'suggested_port': 3000
        }
        
        try:
            # Check for common files
            stdin, stdout, stderr = ssh.exec_command(f"find {deploy_path} -maxdepth 2 -type f -name '*.py' -o -name '*.js' -o -name '*.json' -o -name '*.txt' -o -name 'Dockerfile' -o -name '*.go' -o -name '*.java' | head -20")
            files = stdout.read().decode().strip().split('\n')
            files = [f.strip() for f in files if f.strip()]
            
            # Detect language and type
            for file_path in files:
                filename = os.path.basename(file_path)
                
                if filename == 'package.json':
                    project_info['type'] = 'nodejs'
                    project_info['language'] = 'javascript'
                    project_info['dependencies'].append('package.json')
                    project_info['suggested_port'] = 3000
                elif filename == 'requirements.txt':
                    project_info['type'] = 'python'
                    project_info['language'] = 'python'
                    project_info['dependencies'].append('requirements.txt')
                    project_info['suggested_port'] = 5000
                elif filename == 'Pipfile':
                    project_info['type'] = 'python'
                    project_info['language'] = 'python'
                    project_info['dependencies'].append('Pipfile')
                elif filename == 'go.mod':
                    project_info['type'] = 'golang'
                    project_info['language'] = 'go'
                    project_info['dependencies'].append('go.mod')
                    project_info['suggested_port'] = 8080
                elif filename == 'pom.xml':
                    project_info['type'] = 'java'
                    project_info['language'] = 'java'
                    project_info['dependencies'].append('pom.xml')
                    project_info['suggested_port'] = 8080
                elif filename == 'Dockerfile':
                    project_info['has_dockerfile'] = True
                elif filename in ['main.py', 'app.py', 'server.py']:
                    project_info['main_files'].append(filename)
                elif filename in ['index.js', 'server.js', 'app.js', 'main.js']:
                    project_info['main_files'].append(filename)
                elif filename in ['main.go']:
                    project_info['main_files'].append(filename)
            
            # If no specific type detected but has main files
            if project_info['type'] == 'unknown' and project_info['main_files']:
                main_file = project_info['main_files'][0]
                if main_file.endswith('.py'):
                    project_info['type'] = 'python'
                    project_info['language'] = 'python'
                elif main_file.endswith('.js'):
                    project_info['type'] = 'nodejs'
                    project_info['language'] = 'javascript'
                elif main_file.endswith('.go'):
                    project_info['type'] = 'golang'
                    project_info['language'] = 'go'
            
        except Exception as e:
            logger.error(f"Project detection error: {e}")
        
        return project_info
    
    async def extract_uploaded_file(ssh, deploy_path, file_name):
        """Extract uploaded file on server"""
        try:
            file_path = f"{deploy_path}/{file_name}"
            
            if file_name.lower().endswith('.zip'):
                cmd = f"cd {deploy_path} && unzip -q {file_name} && rm {file_name}"
            elif file_name.lower().endswith(('.tar.gz', '.tgz')):
                cmd = f"cd {deploy_path} && tar -xzf {file_name} && rm {file_name}"
            elif file_name.lower().endswith('.tar'):
                cmd = f"cd {deploy_path} && tar -xf {file_name} && rm {file_name}"
            elif file_name.lower().endswith('.tar.bz2'):
                cmd = f"cd {deploy_path} && tar -xjf {file_name} && rm {file_name}"
            elif file_name.lower().endswith('.rar'):
                cmd = f"cd {deploy_path} && unrar x {file_name} && rm {file_name}"
            else:
                return False
            
            stdin, stdout, stderr = ssh.exec_command(cmd)
            exit_status = stdout.channel.recv_exit_status()
            
            return exit_status == 0
            
        except Exception as e:
            logger.error(f"Extract file error: {e}")
            return False
    
    async def show_project_detection(message, uid, state):
        """Show project detection results and deployment options"""
        try:
            project_info = state['project_info']
            
            # Create detection summary
            detection_text = f"🔍 <b>Project Detection Results</b>\n\n"
            detection_text += f"📁 Project: <b>{state.get('deploy_name', 'Unknown')}</b>\n"
            detection_text += f"💻 Type: <b>{project_info['type'].title()}</b>\n"
            detection_text += f"🔤 Language: <b>{project_info['language'].title()}</b>\n"
            
            if project_info['dependencies']:
                detection_text += f"📦 Dependencies: <b>{', '.join(project_info['dependencies'])}</b>\n"
            
            if project_info['main_files']:
                detection_text += f"🎯 Main files: <b>{', '.join(project_info['main_files'])}</b>\n"
            
            if project_info['has_dockerfile']:
                detection_text += f"🐳 Dockerfile: <b>Found</b>\n"
            
            detection_text += f"🌐 Suggested port: <b>{project_info['suggested_port']}</b>\n\n"
            
            # Create deployment options
            kb = InlineKeyboardMarkup(row_width=2)
            
            install_callback = cache_callback_data(f"install_deps_{uid}")
            docker_callback = cache_callback_data(f"docker_deploy_{uid}")
            service_callback = cache_callback_data(f"configure_service_{uid}")
            manual_callback = cache_callback_data(f"manual_setup_{uid}")
            cancel_callback = cache_callback_data(f"cancel_deploy_{uid}")
            
            if project_info['dependencies']:
                kb.add(InlineKeyboardButton("📦 Install Dependencies", callback_data=install_callback))
            
            if project_info['has_dockerfile']:
                kb.add(InlineKeyboardButton("🐳 Docker Deploy", callback_data=docker_callback))
            
            if project_info['type'] != 'unknown':
                kb.add(InlineKeyboardButton("🔧 Configure Service", callback_data=service_callback))
            
            kb.add(
                InlineKeyboardButton("⚙️ Manual Setup", callback_data=manual_callback),
                InlineKeyboardButton("❌ Cancel", callback_data=cancel_callback)
            )
            
            await message.answer(detection_text, parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Show project detection error: {e}")
            await message.answer("❌ Error showing project detection results")
    
    async def show_docker_options(message, uid, state):
        """Show Docker deployment options"""
        try:
            configure_callback = cache_callback_data(f"docker_configure_{uid}")
            deploy_callback = cache_callback_data(f"docker_deploy_now_{uid}")
            cancel_callback = cache_callback_data(f"cancel_deploy_{uid}")
            
            kb = InlineKeyboardMarkup(row_width=2)
            kb.add(
                InlineKeyboardButton("🔧 Configure", callback_data=configure_callback),
                InlineKeyboardButton("🚀 Deploy Now", callback_data=deploy_callback)
            )
            kb.add(InlineKeyboardButton("❌ Cancel", callback_data=cancel_callback))
            
            if state['docker_type'] == 'build':
                text = (
                    "🐳 <b>Docker Build Ready</b>\n\n"
                    f"Repository: <b>{state['repo_name']}</b>\n"
                    f"Dockerfile: <b>Found</b>\n\n"
                    "Configure deployment settings or deploy immediately:"
                )
            else:
                text = (
                    "🐳 <b>Docker Pull Ready</b>\n\n"
                    f"Image: <b>{state['image_name']}</b>\n\n"
                    "Configure deployment settings or deploy immediately:"
                )
            
            await message.answer(text, parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Show Docker options error: {e}")
            await message.answer("❌ Error showing Docker options")
    
    # --- DEPLOYMENT CALLBACK HANDLERS ---
    
    @dp.callback_query_handler(lambda c: c.data.startswith("install_deps_") or get_cached_callback_data(c.data).startswith("install_deps_"))
    async def install_deps_handler(callback: types.CallbackQuery):
        """Install project dependencies"""
        try:
            # Get actual callback data
            callback_data = get_cached_callback_data(callback.data)
            uid = int(callback_data.split('_')[2])
            
            if uid not in deployment_states:
                await callback.message.edit_text("❌ Deployment session expired")
                return
            
            state = deployment_states[uid]
            await callback.message.edit_text("📦 <b>Installing dependencies...</b>", parse_mode='HTML')
            
            ssh = await get_ssh_session(state['server_id'])
            deploy_path = state['deploy_path']
            project_info = state['project_info']
            
            success = True
            install_log = []
            
            # Install based on project type
            if 'requirements.txt' in project_info['dependencies']:
                cmd = f"cd {deploy_path} && python3 -m pip install -r requirements.txt"
                stdin, stdout, stderr = ssh.exec_command(cmd)
                exit_status = stdout.channel.recv_exit_status()
                output = stdout.read().decode() + stderr.read().decode()
                install_log.append(f"Python dependencies: {'✅' if exit_status == 0 else '❌'}")
                if exit_status != 0:
                    success = False
            
            if 'package.json' in project_info['dependencies']:
                cmd = f"cd {deploy_path} && npm install"
                stdin, stdout, stderr = ssh.exec_command(cmd)
                exit_status = stdout.channel.recv_exit_status()
                output = stdout.read().decode() + stderr.read().decode()
                install_log.append(f"Node.js dependencies: {'✅' if exit_status == 0 else '❌'}")
                if exit_status != 0:
                    success = False
            
            if 'Pipfile' in project_info['dependencies']:
                cmd = f"cd {deploy_path} && pipenv install"
                stdin, stdout, stderr = ssh.exec_command(cmd)
                exit_status = stdout.channel.recv_exit_status()
                install_log.append(f"Pipenv dependencies: {'✅' if exit_status == 0 else '❌'}")
                if exit_status != 0:
                    success = False
            
            if 'go.mod' in project_info['dependencies']:
                cmd = f"cd {deploy_path} && go mod download"
                stdin, stdout, stderr = ssh.exec_command(cmd)
                exit_status = stdout.channel.recv_exit_status()
                install_log.append(f"Go modules: {'✅' if exit_status == 0 else '❌'}")
                if exit_status != 0:
                    success = False
            
            # Show results
            result_text = f"📦 <b>Dependency Installation</b>\n\n"
            result_text += "\n".join(install_log)
            result_text += f"\n\n{'✅ All dependencies installed successfully!' if success else '❌ Some dependencies failed to install'}"
            
            service_callback = cache_callback_data(f"configure_service_{uid}")
            manual_callback = cache_callback_data(f"manual_setup_{uid}")
            cancel_callback = cache_callback_data(f"cancel_deploy_{uid}")
            
            kb = InlineKeyboardMarkup(row_width=2)
            if success:
                kb.add(InlineKeyboardButton("🔧 Configure Service", callback_data=service_callback))
            kb.add(
                InlineKeyboardButton("⚙️ Manual Setup", callback_data=manual_callback),
                InlineKeyboardButton("❌ Cancel", callback_data=cancel_callback)
            )
            
            await callback.message.edit_text(result_text, parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Install deps error: {e}")
            await callback.message.edit_text("❌ Error installing dependencies")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("configure_service_") or get_cached_callback_data(c.data).startswith("configure_service_"))
    async def configure_service_handler(callback: types.CallbackQuery):
        """Configure systemd service"""
        try:
            # Get actual callback data
            callback_data = get_cached_callback_data(callback.data)
            uid = int(callback_data.split('_')[2])
            
            if uid not in deployment_states:
                await callback.message.edit_text("❌ Deployment session expired")
                return
            
            state = deployment_states[uid]
            await callback.message.edit_text("🔧 <b>Creating systemd service...</b>", parse_mode='HTML')
            
            ssh = await get_ssh_session(state['server_id'])
            deploy_path = state['deploy_path']
            deploy_name = state['deploy_name']
            project_info = state['project_info']
            
            # Determine start command
            start_command = ""
            if project_info['type'] == 'python':
                main_file = project_info['main_files'][0] if project_info['main_files'] else 'app.py'
                start_command = f"python3 {main_file}"
            elif project_info['type'] == 'nodejs':
                main_file = project_info['main_files'][0] if project_info['main_files'] else 'index.js'
                start_command = f"node {main_file}"
            elif project_info['type'] == 'golang':
                start_command = f"go run ."
            
            # Create systemd service file
            service_content = f"""[Unit]
Description={deploy_name} Application
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={deploy_path}
ExecStart={start_command}
Restart=always
RestartSec=10
Environment=PORT={project_info['suggested_port']}

[Install]
WantedBy=multi-user.target
"""
            
            # Write service file
            service_path = f"/etc/systemd/system/{deploy_name}.service"
            stdin, stdout, stderr = ssh.exec_command(f"echo '{service_content}' | sudo tee {service_path}")
            
            # Reload systemd and enable service
            stdin, stdout, stderr = ssh.exec_command(f"sudo systemctl daemon-reload")
            stdin, stdout, stderr = ssh.exec_command(f"sudo systemctl enable {deploy_name}")
            
            # Start service
            stdin, stdout, stderr = ssh.exec_command(f"sudo systemctl start {deploy_name}")
            start_exit = stdout.channel.recv_exit_status()
            
            # Check service status
            stdin, stdout, stderr = ssh.exec_command(f"sudo systemctl is-active {deploy_name}")
            status = stdout.read().decode().strip()
            
            success = start_exit == 0 and status == 'active'
            
            result_text = f"🔧 <b>Service Configuration</b>\n\n"
            result_text += f"📝 Service name: <b>{deploy_name}</b>\n"
            result_text += f"📁 Working directory: <code>{deploy_path}</code>\n"
            result_text += f"🚀 Start command: <code>{start_command}</code>\n"
            result_text += f"🌐 Port: <b>{project_info['suggested_port']}</b>\n"
            result_text += f"📊 Status: <b>{'✅ Running' if success else '❌ Failed'}</b>\n\n"
            
            if success:
                result_text += "🎉 <b>Deployment completed successfully!</b>\n"
                result_text += f"Your application is now running on port {project_info['suggested_port']}"
                
                # Add to bot manager
                from bot_manager import add_managed_bot
                bot_info = {
                    'id': f"systemd_{deploy_name}",
                    'name': deploy_name,
                    'type': 'systemd',
                    'status': 'running'
                }
                add_managed_bot(state['server_id'], bot_info)
                
            else:
                result_text += "❌ <b>Service failed to start</b>\n"
                result_text += "Check the logs for more information."
            
            logs_callback = cache_callback_data(f"view_logs_{uid}")
            restart_callback = cache_callback_data(f"restart_service_{uid}")
            complete_callback = cache_callback_data(f"deployment_complete_{uid}")
            
            kb = InlineKeyboardMarkup(row_width=2)
            if success:
                kb.add(
                    InlineKeyboardButton("📊 View Logs", callback_data=logs_callback),
                    InlineKeyboardButton("🔄 Restart", callback_data=restart_callback)
                )
            kb.add(InlineKeyboardButton("✅ Done", callback_data=complete_callback))
            
            await callback.message.edit_text(result_text, parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Configure service error: {e}")
            await callback.message.edit_text("❌ Error configuring service")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("docker_deploy_now_") or get_cached_callback_data(c.data).startswith("docker_deploy_now_"))
    async def docker_deploy_now_handler(callback: types.CallbackQuery):
        """Deploy Docker container immediately"""
        try:
            # Get actual callback data
            callback_data = get_cached_callback_data(callback.data)
            uid = int(callback_data.split('_')[3])
            
            if uid not in deployment_states:
                await callback.message.edit_text("❌ Deployment session expired")
                return
            
            state = deployment_states[uid]
            await callback.message.edit_text("🐳 <b>Deploying Docker container...</b>", parse_mode='HTML')
            
            ssh = await get_ssh_session(state['server_id'])
            
            if state['docker_type'] == 'build':
                # Build from Dockerfile
                deploy_path = state['deploy_path']
                image_name = state['deploy_name'].lower()
                container_name = state['deploy_name'].lower()
                
                # Build image
                build_cmd = f"cd {deploy_path} && docker build -t {image_name} ."
                stdin, stdout, stderr = ssh.exec_command(build_cmd)
                build_exit = stdout.channel.recv_exit_status()
                
                if build_exit != 0:
                    error_output = stderr.read().decode()
                    await callback.message.edit_text(f"❌ <b>Docker build failed</b>\n\n<code>{error_output}</code>", parse_mode='HTML')
                    return
                
                # Run container
                run_cmd = f"docker run -d --name {container_name} --restart unless-stopped -p 3000:3000 {image_name}"
                
            else:
                # Pull and run image
                image_name = state['image_name']
                container_name = image_name.replace(':', '_').replace('/', '_')
                
                # Pull image
                pull_cmd = f"docker pull {image_name}"
                stdin, stdout, stderr = ssh.exec_command(pull_cmd)
                pull_exit = stdout.channel.recv_exit_status()
                
                if pull_exit != 0:
                    error_output = stderr.read().decode()
                    await callback.message.edit_text(f"❌ <b>Docker pull failed</b>\n\n<code>{error_output}</code>", parse_mode='HTML')
                    return
                
                # Run container
                run_cmd = f"docker run -d --name {container_name} --restart unless-stopped -p 3000:3000 {image_name}"
            
            # Execute run command
            stdin, stdout, stderr = ssh.exec_command(run_cmd)
            run_exit = stdout.channel.recv_exit_status()
            
            if run_exit != 0:
                error_output = stderr.read().decode()
                await callback.message.edit_text(f"❌ <b>Container start failed</b>\n\n<code>{error_output}</code>", parse_mode='HTML')
                return
            
            # Check container status
            stdin, stdout, stderr = ssh.exec_command(f"docker ps --filter name={container_name} --format 'table {{{{.Status}}}}'")
            status_output = stdout.read().decode().strip()
            
            success = 'Up' in status_output
            
            result_text = f"🐳 <b>Docker Deployment</b>\n\n"
            result_text += f"📦 Container: <b>{container_name}</b>\n"
            result_text += f"🖼️ Image: <b>{image_name if state['docker_type'] == 'pull' else state['deploy_name']}</b>\n"
            result_text += f"🌐 Port: <b>3000</b>\n"
            result_text += f"📊 Status: <b>{'✅ Running' if success else '❌ Failed'}</b>\n\n"
            
            if success:
                result_text += "🎉 <b>Docker deployment completed successfully!</b>\n"
                result_text += "Your container is now running on port 3000"
                
                # Add to bot manager
                from bot_manager import add_managed_bot
                bot_info = {
                    'id': f"docker_{container_name}",
                    'name': container_name,
                    'type': 'docker',
                    'status': 'running'
                }
                add_managed_bot(state['server_id'], bot_info)
                
            else:
                result_text += "❌ <b>Container failed to start</b>"
            
            complete_callback = cache_callback_data(f"deployment_complete_{uid}")
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("✅ Done", callback_data=complete_callback))
            
            await callback.message.edit_text(result_text, parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Docker deploy now error: {e}")
            await callback.message.edit_text("❌ Error deploying Docker container")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("manual_setup_") or get_cached_callback_data(c.data).startswith("manual_setup_"))
    async def manual_setup_handler(callback: types.CallbackQuery):
        """Show manual setup instructions"""
        try:
            # Get actual callback data
            callback_data = get_cached_callback_data(callback.data)
            uid = int(callback_data.split('_')[2])
            
            if uid not in deployment_states:
                await callback.message.edit_text("❌ Deployment session expired")
                return
            
            state = deployment_states[uid]
            project_info = state['project_info']
            deploy_path = state['deploy_path']
            
            instructions = f"⚙️ <b>Manual Setup Instructions</b>\n\n"
            instructions += f"📁 <b>Project Location:</b>\n<code>{deploy_path}</code>\n\n"
            
            if project_info['type'] == 'python':
                instructions += "🐍 <b>Python Project Setup:</b>\n"
                instructions += f"1. <code>cd {deploy_path}</code>\n"
                if 'requirements.txt' in project_info['dependencies']:
                    instructions += "2. <code>pip install -r requirements.txt</code>\n"
                main_file = project_info['main_files'][0] if project_info['main_files'] else 'app.py'
                instructions += f"3. <code>python3 {main_file}</code>\n"
                
            elif project_info['type'] == 'nodejs':
                instructions += "📦 <b>Node.js Project Setup:</b>\n"
                instructions += f"1. <code>cd {deploy_path}</code>\n"
                if 'package.json' in project_info['dependencies']:
                    instructions += "2. <code>npm install</code>\n"
                main_file = project_info['main_files'][0] if project_info['main_files'] else 'index.js'
                instructions += f"3. <code>node {main_file}</code>\n"
                
            elif project_info['type'] == 'golang':
                instructions += "🔷 <b>Go Project Setup:</b>\n"
                instructions += f"1. <code>cd {deploy_path}</code>\n"
                instructions += "2. <code>go mod download</code>\n"
                instructions += "3. <code>go run .</code>\n"
                
            else:
                instructions += "📋 <b>General Setup:</b>\n"
                instructions += f"1. Navigate to: <code>{deploy_path}</code>\n"
                instructions += "2. Review the project files\n"
                instructions += "3. Install dependencies manually\n"
                instructions += "4. Configure and run the application\n"
            
            instructions += f"\n🌐 <b>Suggested Port:</b> {project_info['suggested_port']}\n"
            instructions += "\n💡 <b>Tips:</b>\n"
            instructions += "• Use <code>screen</code> or <code>tmux</code> to run in background\n"
            instructions += "• Create a systemd service for auto-start\n"
            instructions += "• Configure firewall rules for the port\n"
            
            complete_callback = cache_callback_data(f"deployment_complete_{uid}")
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("✅ Done", callback_data=complete_callback))
            
            await callback.message.edit_text(instructions, parse_mode='HTML', reply_markup=kb)
            
        except Exception as e:
            logger.error(f"Manual setup error: {e}")
            await callback.message.edit_text("❌ Error showing manual setup")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("view_logs_") or get_cached_callback_data(c.data).startswith("view_logs_"))
    async def view_logs_handler(callback: types.CallbackQuery):
        """View service logs"""
        try:
            # Get actual callback data
            callback_data = get_cached_callback_data(callback.data)
            uid = int(callback_data.split('_')[2])
            
            if uid not in deployment_states:
                await callback.message.edit_text("❌ Deployment session expired")
                return
            
            state = deployment_states[uid]
            ssh = await get_ssh_session(state['server_id'])
            deploy_name = state['deploy_name']
            
            # Get service logs
            stdin, stdout, stderr = ssh.exec_command(f"sudo journalctl -u {deploy_name} --no-pager -n 20")
            logs = stdout.read().decode().strip()
            
            if len(logs) > 3000:
                logs = logs[-3000:] + "\n\n... (truncated)"
            
            if not logs:
                logs = "No logs available"
            
            back_callback = cache_callback_data(f"configure_service_{uid}")
            
            await callback.message.edit_text(
                f"📊 <b>Service Logs</b>\n\n<code>{logs}</code>",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("⬅️ Back", callback_data=back_callback)
                )
            )
            
        except Exception as e:
            logger.error(f"View logs error: {e}")
            await callback.message.edit_text("❌ Error viewing logs")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("deployment_complete_") or get_cached_callback_data(c.data).startswith("deployment_complete_"))
    async def deployment_complete_handler(callback: types.CallbackQuery):
        """Complete deployment"""
        try:
            # Get actual callback data
            callback_data = get_cached_callback_data(callback.data)
            uid = int(callback_data.split('_')[2])
            
            if uid in deployment_states:
                server_id = deployment_states[uid]['server_id']
                deployment_states.pop(uid, None)
            else:
                server_id = "unknown"
            
            server_callback = cache_callback_data(f"server_{server_id}")
            
            await callback.message.edit_text(
                "✅ <b>Deployment Complete!</b>\n\n"
                "Your application has been deployed successfully.\n"
                "You can now manage it through the Bot Manager or manually via SSH.",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🖥️ Back to Server", callback_data=server_callback)
                )
            )
            
        except Exception as e:
            logger.error(f"Deployment complete error: {e}")
            await callback.message.edit_text("✅ Deployment completed")
    
    @dp.callback_query_handler(lambda c: c.data.startswith("cancel_deploy_") or get_cached_callback_data(c.data).startswith("cancel_deploy_"))
    async def cancel_deploy_handler(callback: types.CallbackQuery):
        """Cancel deployment"""
        try:
            # Get actual callback data
            callback_data = get_cached_callback_data(callback.data)
            uid = int(callback_data.split('_')[2])
            
            if uid in deployment_states:
                server_id = deployment_states[uid]['server_id']
                deployment_states.pop(uid, None)
            else:
                server_id = "unknown"
            
            server_callback = cache_callback_data(f"server_{server_id}")
            
            await callback.message.edit_text(
                "❌ <b>Deployment Cancelled</b>\n\n"
                "The deployment process has been cancelled.",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup().add(
                    InlineKeyboardButton("🖥️ Back to Server", callback_data=server_callback)
                )
            )
            
        except Exception as e:
            logger.error(f"Cancel deploy error: {e}")
            await callback.message.edit_text("❌ Deployment cancelled")
    
    logger.info("✅ Deployment handlers initialized")
