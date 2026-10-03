"""
Парсер активных пользователей Telegram (Telethon).

Использует существующую сессию из основного скрипта рассылки —
повторная авторизация не требуется, если файл .session уже есть.
"""

import asyncio
import sys
import os

from telethon import TelegramClient
from telethon.errors import (
    ChannelPrivateError,
    ChatAdminRequiredError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.types import Channel, Chat, User

# --- Настройки подключения (должны совпадать с основным скриптом) ---
api_id = 34371662
api_hash = '3dcb822b759133f1393b1bb7048336c5'
session_name = 'session_Account1'  # Имя без расширения .session (TELEGRAM_ACCOUNT_1_NAME=Account1)

# --- Цель парсинга ---
URL = 'https://t.me/MARKETPLACEKZ1'

# --- Параметры сбора ---
OUTPUT_FILE = 'users.txt'
ALL_OUTPUT_FILE = 'usersall.txt'
CHAT_MESSAGE_LIMIT = 10000
CHANNEL_POST_LIMIT = 100


if sys.platform.startswith('win'):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass


def is_broadcast_channel(entity):
    """True для публичного/приватного канала (не супергруппы)."""
    return isinstance(entity, Channel) and entity.broadcast and not entity.megagroup


def is_chat_entity(entity):
    """True для обычной группы или супергруппы."""
    return isinstance(entity, Chat) or (
        isinstance(entity, Channel) and not entity.broadcast
    )


async def try_add_username(usernames, sender):
    """Добавляет username, если это живой пользователь с @username."""
    if not isinstance(sender, User):
        return
    if sender.bot:
        return
    if not sender.username:
        return
    usernames.add(sender.username.lower())


async def collect_from_message(client, message, usernames):
    """Извлекает username автора сообщения."""
    sender = message.sender
    if sender is None:
        try:
            sender = await message.get_sender()
        except Exception:
            return
    await try_add_username(usernames, sender)


async def parse_chat_messages(client, entity, usernames):
    """Чат/супергруппа: последние CHAT_MESSAGE_LIMIT сообщений."""
    print(f"📥 Режим: чат/группа — сканирую последние {CHAT_MESSAGE_LIMIT} сообщений...")
    processed = 0

    async for message in client.iter_messages(entity, limit=CHAT_MESSAGE_LIMIT):
        processed += 1
        await collect_from_message(client, message, usernames)
        if processed % 200 == 0:
            print(f"   ... обработано сообщений: {processed}, уникальных юзеров: {len(usernames)}")

    print(f"✓ Сканирование чата завершено: сообщений {processed}, уникальных юзеров {len(usernames)}")


async def parse_channel_comments(client, entity, usernames):
    """Канал: последние CHANNEL_POST_LIMIT постов и комментарии к каждому."""
    print(f"📥 Режим: канал — сканирую последние {CHANNEL_POST_LIMIT} постов и их комментарии...")
    posts = []
    async for post in client.iter_messages(entity, limit=CHANNEL_POST_LIMIT):
        if post and post.id:
            posts.append(post)

    print(f"   Найдено постов для проверки: {len(posts)}")

    for index, post in enumerate(posts, 1):
        print(f"   💬 Сканирую комментарии к посту {index}/{len(posts)} (ID: {post.id})...")
        comment_count = 0

        try:
            async for comment in client.iter_messages(entity, reply_to=post.id):
                comment_count += 1
                await collect_from_message(client, comment, usernames)
        except ChatAdminRequiredError:
            print(f"   ⚠ Нет доступа к комментариям поста {post.id} — пропускаю")
        except Exception as e:
            print(f"   ⚠ Ошибка при чтении комментариев поста {post.id}: {e}")

        if comment_count == 0:
            print(f"   — Комментариев нет или они недоступны")
        else:
            print(f"   — Комментариев обработано: {comment_count}, уникальных юзеров: {len(usernames)}")

    print(f"✓ Сканирование канала завершено: уникальных юзеров {len(usernames)}")


def save_usernames(usernames):
    """Перезаписывает users.txt свежим списком без @."""
    sorted_names = sorted(usernames)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as file:
        for username in sorted_names:
            file.write(f"{username}\n")
    return sorted_names


def append_to_usersall(source_url, usernames):
    """Добавляет блок в usersall.txt: ссылка сверху, затем юзернеймы."""
    sorted_names = sorted(usernames)
    file_exists = os.path.exists(ALL_OUTPUT_FILE) and os.path.getsize(ALL_OUTPUT_FILE) > 0

    with open(ALL_OUTPUT_FILE, 'a', encoding='utf-8') as file:
        if file_exists:
            file.write("\n")
        file.write(f"{source_url.strip()}\n")
        for username in sorted_names:
            file.write(f"{username}\n")

    return sorted_names


async def main():
    print("=" * 60)
    print("Парсер активных пользователей Telegram")
    print("=" * 60)
    print(f"Сессия: {session_name}.session")
    print(f"Цель:   {URL}")
    print(f"Вывод:  {OUTPUT_FILE} (перезапись) + {ALL_OUTPUT_FILE} (добавление)")
    print("=" * 60)

    session_file = f"{session_name}.session"
    if not os.path.exists(session_file):
        print(f"\n✗ Файл сессии не найден: {session_file}")
        print("  Сначала авторизуйтесь через telegram_sender.py на этом аккаунте.")
        return

    client = TelegramClient(session_name, api_id, api_hash)
    usernames = set()

    try:
        print("\n🔌 Подключение к существующей сессии...")
        await client.connect()

        if not await client.is_user_authorized():
            print("✗ Сессия найдена, но аккаунт не авторизован.")
            print("  Запустите telegram_sender.py и войдите в аккаунт заново.")
            return

        me = await client.get_me()
        print(f"✓ Подключение к существующей сессии успешно (@{me.username or me.id})")

        print(f"\n🔍 Определяю тип ссылки: {URL}")
        try:
            entity = await client.get_entity(URL)
        except (UsernameInvalidError, UsernameNotOccupiedError, ValueError) as e:
            print(f"✗ Невалидная ссылка или username: {e}")
            return
        except (InviteHashInvalidError, InviteHashExpiredError) as e:
            print(f"✗ Ссылка-приглашение недействительна или истекла: {e}")
            return
        except ChannelPrivateError:
            print("✗ Приватный канал/чат: аккаунт не состоит в нём или нет доступа.")
            return

        title = getattr(entity, 'title', None) or getattr(entity, 'username', URL)
        print(f"✓ Объект найден: {title}")

        if is_broadcast_channel(entity):
            await parse_channel_comments(client, entity, usernames)
        elif is_chat_entity(entity):
            await parse_chat_messages(client, entity, usernames)
        else:
            print("✗ Не удалось определить тип чата/канала для этой ссылки.")
            return

        if not usernames:
            print("\n⚠ Активных пользователей с @username не найдено.")
            print(f"  Файлы {OUTPUT_FILE} и {ALL_OUTPUT_FILE} не будут изменены.")
            return

        saved = save_usernames(usernames)
        append_to_usersall(URL, saved)
        print(f"\n✓ Найдено {len(saved)} уникальных активных пользователей")
        print(f"✓ {OUTPUT_FILE} — перезаписан ({len(saved)} юзеров)")
        print(f"✓ {ALL_OUTPUT_FILE} — добавлен блок: ссылка + {len(saved)} юзеров")

    except ChatAdminRequiredError:
        print("✗ Недостаточно прав для чтения сообщений в этом чате/канале.")
    except ChannelPrivateError:
        print("✗ Нет доступа: канал/чат приватный или аккаунт не состоит в нём.")
    except Exception as e:
        print(f"✗ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await client.disconnect()
        print("\n🔌 Отключено от Telegram.")


if __name__ == '__main__':
    asyncio.run(main())
