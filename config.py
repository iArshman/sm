import os
import sys
from pathlib import Path

# Hardcoded credentials
BOT_TOKEN = "8167520002:AAEHfTsi8SBJQU2V8mOVI15MG2X8fvKQZoU"
MONGO_URI = "mongodb+srv://irexanon:xUf7PCf9cvMHy8g6@rexdb.d9rwo.mongodb.net/?retryWrites=true&w=majority&appName=RexDB"

# Bot token check
if not BOT_TOKEN:
    print("❌ BOT_TOKEN is not set")
    raise ValueError("BOT_TOKEN is required")

print(f"✅ Bot token loaded: {BOT_TOKEN[:10]}...")

# MongoDB configuration
if MONGO_URI.startswith('mongodb+srv://'):
    print("✅ Using MongoDB Atlas connection")
else:
    print("✅ Using local MongoDB connection")

# SSH configuration
SSH_TIMEOUT = 15
MAX_CONNECTIONS = 10

# File manager configuration
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
ALLOWED_EXTENSIONS=.txt,.py,.js,.json,.yml,.yaml,.conf,.jpg

# Logging configuration
LOG_LEVEL = 'INFO'

print(f"✅ Configuration loaded successfully")
print(f"  - SSH Timeout: {SSH_TIMEOUT}s")
print(f"  - Max Connections: {MAX_CONNECTIONS}")
print(f"  - Max File Size: {MAX_FILE_SIZE // (1024*1024)}MB") 
print(f"  - Log Level: {LOG_LEVEL}") 
