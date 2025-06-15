import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Bot configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")

# MongoDB configuration
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017')

# SSH configuration
SSH_TIMEOUT = int(os.getenv('SSH_TIMEOUT', '15'))
MAX_CONNECTIONS = int(os.getenv('MAX_CONNECTIONS', '10'))

# File manager configuration
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE', '50')) * 1024 * 1024  # 50MB default
ALLOWED_EXTENSIONS = os.getenv('ALLOWED_EXTENSIONS', '').split(',') if os.getenv('ALLOWED_EXTENSIONS') else []

# Logging configuration
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
