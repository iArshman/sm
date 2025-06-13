from aiogram.types import Message

async def handle_file_manager(message: Message, server_data):
    await message.answer("📂 File Manager: Coming Soon")
