import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Get the project root directory (parent of bot directory)
project_root = Path(__file__).parent.parent
env_path = project_root / '.env'

# Load environment variables from .env file
if env_path.exists():
    load_dotenv(env_path)
    print(f"✅ Loaded environment variables from: {env_path}")
else:
    # Try loading from current directory
    load_dotenv()
    print("⚠️ Loading environment variables from current directory")

# Bot configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    print("❌ BOT_TOKEN not found in environment variables")
    print("Available environment variables:")
    for key in os.environ.keys():
        if 'BOT' in key.upper() or 'TOKEN' in key.upper():
            print(f"  {key}: {os.environ[key][:10]}...")
    raise ValueError("BOT_TOKEN environment variable is required")

print(f"✅ Bot token loaded: {BOT_TOKEN[:10]}...")

# MongoDB configuration
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017')
if MONGO_URI.startswith('mongodb+srv://'):
    print("✅ Using MongoDB Atlas connection")
else:
    print("✅ Using local MongoDB connection")

# SSH configuration
SSH_TIMEOUT = int(os.getenv('SSH_TIMEOUT', '15'))
MAX_CONNECTIONS = int(os.getenv('MAX_CONNECTIONS', '10'))

# File manager configuration
MAX_FILE_SIZE = int(os.getenv('MAX_FILE_SIZE', '50')) * 1024 * 1024  # Convert MB to bytes
ALLOWED_EXTENSIONS = os.getenv('ALLOWED_EXTENSIONS', '').split(',') if os.getenv('ALLOWED_EXTENSIONS') else []

# Logging configuration
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')

print(f"✅ Configuration loaded successfully")
print(f"  - SSH Timeout: {SSH_TIMEOUT}s")
print(f"  - Max Connections: {MAX_CONNECTIONS}")
print(f"  - Max File Size: {MAX_FILE_SIZE // (1024*1024)}MB")
print(f"  - Log Level: {LOG_LEVEL}")
