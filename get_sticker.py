import asyncio
from telethon import TelegramClient

api_id = 123456  # Твой api_id
api_hash = 'your_api_hash'  # Твой api_hash

async def main():
    async with TelegramClient('session_name', api_id, api_hash) as client:
        # Берем последнее сообщение из Избранного
        async for message in client.iter_messages('me', limit=1):
            if message.sticker:
                print("\n--- СКОПИРУЙ ЭТИ ДАННЫЕ В СВОЙ ФАЙЛ MESSAGE ---")
                print("STICKER")
                print(f"{message.sticker.id}")
                print(f"{message.sticker.access_hash}")
                # Переводим file_reference в удобный HEX-формат (строку из букв и цифр)
                print(f"{message.sticker.file_reference.hex()}")
                print("----------------------------------------------\n")
            else:
                print("Последнее сообщение в Избранном — это не стикер!")

asyncio.run(main())