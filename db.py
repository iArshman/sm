from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from config import MONGO_URI

client = AsyncIOMotorClient(MONGO_URI)
db = client["server_manager"]
servers_collection = db["servers"]

async def add_server(data):
    await servers_collection.insert_one(data)

async def get_servers():
    return await servers_collection.find().to_list(None)

async def get_server_by_id(server_id):
    return await servers_collection.find_one({"_id": ObjectId(server_id)})

async def update_server_name(server_id, new_name):
    await servers_collection.update_one(
        {"_id": ObjectId(server_id)},
        {"$set": {"name": new_name}}
    )

async def update_server_username(server_id, new_username):
    await servers_collection.update_one(
        {"_id": ObjectId(server_id)},
        {"$set": {"username": new_username}}
    )

async def delete_server_by_id(server_id):
    await servers_collection.delete_one({"_id": ObjectId(server_id)})
