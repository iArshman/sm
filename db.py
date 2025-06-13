from pymongo import MongoClient
from bson import ObjectId
from config import MONGO_URI

client = MongoClient(MONGO_URI)
db = client["server_manager"]
servers_collection = db["servers"]

def add_server(data):
    servers_collection.insert_one(data)

def get_servers():
    return list(servers_collection.find())

def get_server_by_id(server_id):
    return servers_collection.find_one({"_id": ObjectId(server_id)})

def update_server_name(server_id, new_name):
    servers_collection.update_one(
        {"_id": ObjectId(server_id)},
        {"$set": {"name": new_name}}
    )

def update_server_username(server_id, new_username):
    servers_collection.update_one(
        {"_id": ObjectId(server_id)},
        {"$set": {"username": new_username}}
    )

def delete_server_by_id(server_id):
    servers_collection.delete_one({"_id": ObjectId(server_id)})
