from pymongo import MongoClient
from config import MONGO_URI

client = MongoClient(MONGO_URI)
db = client['server_manager']
servers = db['servers']

def add_server(user_id, name, username, ip, key_file_path):
    servers.insert_one({
        'user_id': user_id,
        'name': name,
        'username': username,
        'ip': ip,
        'key_file_path': key_file_path
    })

def get_servers(user_id):
    return list(servers.find({'user_id': user_id}))

def get_server_by_name(user_id, name):
    return servers.find_one({'user_id': user_id, 'name': name})

def update_server_name(user_id, old_name, new_name):
    servers.update_one({'user_id': user_id, 'name': old_name}, {'$set': {'name': new_name}})

def update_server_username(user_id, name, new_username):
    servers.update_one({'user_id': user_id, 'name': name}, {'$set': {'username': new_username}})

def delete_server(user_id, name):
    servers.delete_one({'user_id': user_id, 'name': name})
