from pymongo import MongoClient
from config import MONGO_URI
import paramiko
from io import StringIO
import threading

class Database:
    def __init__(self):
        self.client = MongoClient(MONGO_URI)
        self.db = self.client.serverbot
        self.connection_cache = {}
        self.lock = threading.Lock()
        
    def get_ssh(self, server):
        """Get cached SSH connection or create new one"""
        server_id = str(server['_id'])
        
        with self.lock:
            if server_id in self.connection_cache:
                ssh, last_used = self.connection_cache[server_id]
                if time.time() - last_used < 30:  # Reuse if recent
                    return ssh
            
            # Create new connection
            try:
                key_file = StringIO()
                key_file.write(server['ssh_key'])
                key_file.seek(0)
                
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(
                    hostname=server['ip'],
                    username=server['username'],
                    pkey=paramiko.RSAKey.from_private_key(key_file),
                    timeout=SSH_TIMEOUT,
                    banner_timeout=10
                )
                
                self.connection_cache[server_id] = (ssh, time.time())
                return ssh
            except Exception as e:
                raise Exception(f"SSH failed: {str(e)}")

db = Database()
