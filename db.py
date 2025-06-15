import logging
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from config import MONGO_URI

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# MongoDB client and collections
client = AsyncIOMotorClient(MONGO_URI)
db = client["server_manager"]
servers_collection = db["servers"]

async def add_server(data):
    """Add a new server to the database"""
    try:
        result = await servers_collection.insert_one(data)
        logger.info(f"Added server with ID: {result.inserted_id}")
        return result.inserted_id
    except Exception as e:
        logger.error(f"Error adding server: {e}")
        raise

async def get_servers():
    """Get all servers from the database"""
    try:
        servers = await servers_collection.find().to_list(None)
        logger.info(f"Retrieved {len(servers)} servers")
        return servers
    except Exception as e:
        logger.error(f"Error fetching servers: {e}")
        return []

async def get_server_by_id(server_id):
    """Get a specific server by ID"""
    try:
        server = await servers_collection.find_one({"_id": ObjectId(server_id)})
        if server:
            logger.info(f"Retrieved server: {server.get('name', 'Unknown')}")
        else:
            logger.warning(f"Server not found: {server_id}")
        return server
    except Exception as e:
        logger.error(f"Error fetching server {server_id}: {e}")
        return None

async def update_server_name(server_id, new_name):
    """Update server name"""
    try:
        result = await servers_collection.update_one(
            {"_id": ObjectId(server_id)},
            {"$set": {"name": new_name}}
        )
        if result.modified_count > 0:
            logger.info(f"Updated server name to: {new_name}")
        else:
            logger.warning(f"No server updated for ID: {server_id}")
        return result.modified_count > 0
    except Exception as e:
        logger.error(f"Error updating name for server {server_id}: {e}")
        raise

async def update_server_username(server_id, new_username):
    """Update server username"""
    try:
        result = await servers_collection.update_one(
            {"_id": ObjectId(server_id)},
            {"$set": {"username": new_username}}
        )
        if result.modified_count > 0:
            logger.info(f"Updated server username to: {new_username}")
        else:
            logger.warning(f"No server updated for ID: {server_id}")
        return result.modified_count > 0
    except Exception as e:
        logger.error(f"Error updating username for server {server_id}: {e}")
        raise

async def delete_server_by_id(server_id):
    """Delete a server by ID"""
    try:
        result = await servers_collection.delete_one({"_id": ObjectId(server_id)})
        if result.deleted_count > 0:
            logger.info(f"Deleted server with ID: {server_id}")
        else:
            logger.warning(f"No server deleted for ID: {server_id}")
        return result.deleted_count > 0
    except Exception as e:
        logger.error(f"Error deleting server {server_id}: {e}")
        raise

async def update_server_stats(server_id, stats):
    """Update server statistics"""
    try:
        result = await servers_collection.update_one(
            {"_id": ObjectId(server_id)},
            {"$set": {"stats": stats, "last_updated": datetime.utcnow()}}
        )
        return result.modified_count > 0
    except Exception as e:
        logger.error(f"Error updating stats for server {server_id}: {e}")
        return False

# Health check function
async def check_database_connection():
    """Check if database connection is working"""
    try:
        await client.admin.command('ping')
        logger.info("Database connection successful")
        return True
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        return False
