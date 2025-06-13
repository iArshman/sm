from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from config import MONGO_URI

client = AsyncIOMotorClient(MONGO_URI)
db = client["server_manager"]
servers_collection = db["servers"]

async def add_server(data):
    await servers_collection.insert_one(data)

async def get_servers():
    try:
        return await servers_collection.find().to_list(None)
    except Exception as e:
        logging.error(f"Error fetching servers: {e}")
        return []

async def get_server_by_id(server_id):
    try:
        return await servers_collection.find_one({"_id": ObjectId(server_id)})
    except Exception as e:
        logging.error(f"Error fetching server {server_id}: {e}")
        return None

async def update_server_name(server_id, new_name):
    try:
        await servers_collection.update_one(
            {"_id": ObjectId(server_id)},
            {"$set": {"name": new_name}}
        )
    except Exception as e:
        logging.error(f"Error updating name for server {server_id}: {e}")

async def update_server_username(server_id, new_username):
    try:
        await servers_collection.update_one(
            {"_id": ObjectId(server_id)},
            {"$set": {"username": new_username}}
        )
    except Exception as e:
        logging.error(f"Error updating username for server {server_id}: {e}")

async def delete_server_by_id(server_id):
    try:
        await servers_collection.delete_one({"_id": ObjectId(server_id)})
    except Exception as e:
        logging.error(f"Error deleting server {server_id}: {e}")
