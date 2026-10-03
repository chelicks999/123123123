"""
Скрипт для рассылки сообщений в Telegram
ВАЖНО: Используйте только для легальных целей и с согласия получателей!
"""

import asyncio
import time
import random
import sys
import traceback
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError, 
    UserPrivacyRestrictedError, 
    UserIsBlockedError,
    UserNotMutualContactError,
    PeerFloodError,
    UsernameNotOccupiedError,
    UsernameInvalidError,
    InputUserDeactivatedError,
    ChatWriteForbiddenError,
    ApiIdInvalidError
)
from telethon.tl.types import Channel, Chat, User, InputDocument
from telethon.tl.functions.messages import SetTypingRequest, GetBotCallbackAnswerRequest
from telethon.tl.types import SendMessageTypingAction
import json
import os
from dotenv import load_dotenv

# Загрузка переменных окружения из .env файла
load_dotenv()

# Windows: Telethon/asyncio иногда стабильнее работает на SelectorEventLoop
if sys.platform.startswith('win'):
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        # Если политика не поддерживается — игнорируем, Telethon сам попробует корректно работать.
        pass
    # Чтобы избежать UnicodeEncodeError в консоли (эмодзи/UTF-8)
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Конфигурация (для обратной совместимости)
API_ID = os.getenv('TELEGRAM_API_ID', '')
API_HASH = os.getenv('TELEGRAM_API_HASH', '')
PHONE_NUMBER = os.getenv('TELEGRAM_PHONE', '')

# Настройки рассылки
MESSAGES_PER_USER = 2  # Количество сообщений для каждого пользователя
DELAY_BETWEEN_MESSAGES = 3  # Задержка между сообщениями для одного пользователя (секунды)
DELAY_BETWEEN_USERS_MIN = 10  # Минимальная задержка между разными пользователями (секунды)
DELAY_BETWEEN_USERS_MAX = 45  # Максимальная задержка между разными пользователями (секунды)
DELAY_AFTER_EVERY_N_USERS = 10  # Количество пользователей, после которых делается пауза
DELAY_AFTER_N_USERS = 90  # Задержка после каждых N пользователей (секунды)
TYPING_DURATION = 1.5  # Длительность эффекта печатания перед отправкой сообщения (секунды)

# Максимальное количество сообщений в день (для безопасности)
MAX_MESSAGES_PER_DAY = 200

# Сетевые параметры Telethon (для стабильного коннекта на Windows)
TELETHON_START_TIMEOUT = 120  # max time for connect/start during authorization/login
TELETHON_TIMEOUT = 20          # per-request socket timeout inside Telethon
TELETHON_REQUEST_RETRIES = 7
TELETHON_CONNECTION_RETRIES = 7
TELETHON_RETRY_DELAY = 1       # seconds between retries
SEND_RETRY_ATTEMPTS = 3        # retries for transient disconnect/network errors
SEND_RETRY_DELAY = 4           # seconds between send retries
MAX_MESSAGE_FILES = 50         # message1.txt, message2.txt, ...


def parse_file_reference(ref_str):
    """Преобразует file_reference из файла в bytes."""
    ref_str = (ref_str or '').strip()
    if not ref_str:
        return b''

    try:
        return bytes.fromhex(ref_str)
    except ValueError:
        pass

    try:
        import base64
        return base64.b64decode(ref_str, validate=True)
    except Exception:
        return ref_str.encode('utf-8')


def parse_message_file(filepath):
    """Читает messageN.txt и возвращает команду отправки или None."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except OSError as e:
        print(f"⚠ Не удалось прочитать {filepath}: {e}")
        return None

    if not content.strip():
        return None

    lines = content.splitlines()
    action = lines[0].strip().upper()

    if action == 'TEXT':
        text = '\n'.join(lines[1:]).strip()
        if not text:
            print(f"⚠ {filepath}: TEXT без текста — пропускаю")
            return None
        return {'type': 'TEXT', 'text': text}

    if action == 'FORWARD':
        if len(lines) < 3:
            print(f"⚠ {filepath}: FORWARD требует источник и ID сообщения — пропускаю")
            return None
        source = lines[1].strip()
        try:
            message_id = int(lines[2].strip())
        except ValueError:
            print(f"⚠ {filepath}: неверный ID сообщения — пропускаю")
            return None
        return {'type': 'FORWARD', 'source': source, 'message_id': message_id}

    if action == 'PREMIUM_EMOJI':
        if len(lines) < 3:
            print(f"⚠ {filepath}: PREMIUM_EMOJI требует источник и ID сообщения — пропускаю")
            return None
        source = lines[1].strip()
        try:
            message_id = int(lines[2].strip())
        except ValueError:
            print(f"⚠ {filepath}: неверный ID сообщения — пропускаю")
            return None
        return {'type': 'PREMIUM_EMOJI', 'source': source, 'message_id': message_id}

    if action == 'STICKER':
        if len(lines) < 4:
            print(f"⚠ {filepath}: STICKER требует id, access_hash и file_reference — пропускаю")
            return None
        try:
            sticker_id = int(lines[1].strip())
            access_hash = int(lines[2].strip())
        except ValueError:
            print(f"⚠ {filepath}: неверные id/access_hash стикера — пропускаю")
            return None
        file_reference = parse_file_reference(lines[3])
        return {
            'type': 'STICKER',
            'id': sticker_id,
            'access_hash': access_hash,
            'file_reference': file_reference,
        }

    # Старый формат без ключевого слова — весь файл считается текстом
    return {'type': 'TEXT', 'text': content.strip()}


def load_message_commands(max_files=MAX_MESSAGE_FILES):
    """Загружает message1.txt, message2.txt, ... Пропускает отсутствующие файлы."""
    commands = []
    for index in range(1, max_files + 1):
        filepath = f'message{index}.txt'
        if not os.path.exists(filepath):
            continue
        command = parse_message_file(filepath)
        if command:
            command['file'] = filepath
            commands.append(command)
    return commands


def format_message_command_preview(command):
    """Краткий предпросмотр команды для консоли."""
    if command['type'] == 'TEXT':
        text = command['text']
        return text[:200] + ('...' if len(text) > 200 else '')
    if command['type'] == 'FORWARD':
        return f"FORWARD: {command['source']} → msg #{command['message_id']}"
    if command['type'] == 'PREMIUM_EMOJI':
        return f"PREMIUM_EMOJI: {command['source']} → msg #{command['message_id']}"
    if command['type'] == 'STICKER':
        return f"STICKER: id={command['id']}, access_hash={command['access_hash']}"
    return str(command)


class TelegramSender:
    def __init__(self, api_id, api_hash, phone_number, account_name=None, proxy=None):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone_number = phone_number
        self.account_name = account_name or phone_number
        self.proxy = proxy  # Прокси для этого аккаунта
        self.client = None

    async def _ensure_connected(self):
        """Гарантирует активное и авторизованное соединение перед отправкой."""
        if not self.client:
            await self.connect()
            return

        if not self.client.is_connected():
            try:
                await self.client.connect()
            except Exception:
                # Если простое подключение не удалось, пробуем полную переавторизацию.
                await self.connect()
                return

        if not await self.client.is_user_authorized():
            await self.connect()

    def _create_client(self, session_name):
        """Создает TelegramClient с общими сетевыми настройками и опциональным прокси."""
        client_kwargs = {
            'timeout': TELETHON_TIMEOUT,
            'request_retries': TELETHON_REQUEST_RETRIES,
            'connection_retries': TELETHON_CONNECTION_RETRIES,
            'retry_delay': TELETHON_RETRY_DELAY,
            'auto_reconnect': False,
            'sequential_updates': True,
            'receive_updates': False,
        }
        if self.proxy:
            client_kwargs['proxy'] = self.proxy
        return TelegramClient(session_name, self.api_id, self.api_hash, **client_kwargs)

    def _is_transient_connection_error(self, error):
        """Определяет временные сетевые ошибки, где уместен ретрай."""
        error_msg = str(error).lower()
        error_type = type(error).__name__.lower()
        connection_keywords = [
            'disconnect', 'disconnected', 'connection reset', 'connection aborted',
            'connection closed', 'connection lost', 'network is unreachable',
            'timed out', 'timeout', 'server closed', 'transport'
        ]
        return (
            isinstance(error, (ConnectionResetError, ConnectionError, asyncio.TimeoutError))
            or any(keyword in error_msg for keyword in connection_keywords)
            or 'timeout' in error_type
            or 'disconnect' in error_type
            or 'connection' in error_type
        )
        
    async def connect(self):
        """Подключение к Telegram с использованием сохраненной сессии или создание новой"""
        # Используем уникальное имя сессии для каждого аккаунта
        session_name = f'session_{self.account_name.replace("+", "").replace("-", "_")}'
        session_file = f'{session_name}.session'
        
        if self.proxy:
            print(f"   🔒 Использую прокси для аккаунта {self.account_name}: {format_proxy_display(self.proxy)}")
        self.client = self._create_client(session_name)
        
        # Проверяем, существует ли файл сессии
        if os.path.exists(session_file):
            print(f"   📁 Найдена существующая сессия: {session_file}")
            try:
                # Сначала просто подключаемся и проверяем авторизацию на существующей сессии.
                # Это стабильнее, чем запускать full .start() на Windows.
                await self.client.connect()
                if await self.client.is_user_authorized():
                    print(f"   ✓ Подключено используя сохраненную сессию (аккаунт: {self.account_name})")
                    return
                else:
                    print(f"   ⚠ Сессия существует, но не авторизована. Требуется повторная авторизация...")
            except ApiIdInvalidError:
                print(f"\n{'='*60}")
                print(f"✗ ОШИБКА: Неверные API_ID/API_HASH для аккаунта {self.account_name}")
                print(f"{'='*60}")
                print(f"Файл сессии существует, но API_ID/API_HASH неверные.")
                print(f"Проверьте правильность API_ID и API_HASH в файле .env")
                print(f"Аккаунт: {self.account_name} ({self.phone_number})")
                print(f"{'='*60}")
                raise
            except Exception as e:
                error_msg = str(e).lower()
                # Проверяем, не является ли ошибка связанной с неверными API_ID/API_HASH
                if 'api_id' in error_msg and 'invalid' in error_msg:
                    print(f"\n{'='*60}")
                    print(f"✗ ОШИБКА: Неверные API_ID/API_HASH для аккаунта {self.account_name}")
                    print(f"{'='*60}")
                    print(f"Ошибка: {str(e)}")
                    print(f"Проверьте правильность API_ID и API_HASH в файле .env")
                    print(f"Аккаунт: {self.account_name} ({self.phone_number})")
                    print(f"{'='*60}")
                    raise
                else:
                    print(f"   ⚠ Ошибка при подключении с существующей сессией:")
                    print(f"      type: {type(e).__name__}")
                    print(f"      repr: {repr(e)}")
                    if isinstance(e, asyncio.TimeoutError):
                        print(f"      hint: похоже, connect/start не успевает за TELETHON_START_TIMEOUT")
                    # ВАЖНО: при некоторых сетевых сбоях (например, WinError 10054)
                    # подключение с существующей сессией может не успевать/обрываться.
                    # В таком случае пробуем продолжить и дойти до start(phone=...),
                    # чтобы Telethon перевыполнил необходимые шаги с сессией.
                    is_connection_reset = (
                        isinstance(e, ConnectionResetError)
                        or 'winerror 10054' in error_msg
                        or 'connectionreseterror' in error_msg
                        or '10054' in error_msg
                    )
                    # Telethon при серии reset'ов может в конце бросить ConnectionError:
                    # "Connection to Telegram failed N time(s)".
                    # Считаем это сетевой проблемой и тоже пробуем пересоздать сессию.
                    is_connection_to_telegram_failed = (
                        'connection to telegram failed' in error_msg
                        or ('time(s)' in error_msg and 'telegram' in error_msg)
                    )

                    if is_connection_reset or is_connection_to_telegram_failed:
                        try:
                            await self.client.disconnect()
                        except Exception:
                            pass
                        print("   🔁 Сетевой обрыв. Пробую пересоздать сессию (B)...")

                        # SQLite-сессии Telethon на Windows часто оставляют мусор в journal/wal/shm,
                        # поэтому чистим несколько типичных файлов.
                        session_candidates = [
                            session_file,
                            f"{session_file}-journal",
                            f"{session_file}-wal",
                            f"{session_file}-shm",
                        ]
                        for sf in session_candidates:
                            try:
                                if os.path.exists(sf):
                                    os.remove(sf)
                            except Exception:
                                # Если удалить не удалось — просто продолжим с пересозданием клиента.
                                pass

                        # Пересоздаем TelegramClient, чтобы Telethon заново создал сессию.
                        self.client = self._create_client(session_name)

                        try:
                            await self.client.start(phone=self.phone_number)
                            print(f"   ✓ Успешно авторизован и подключен (аккаунт: {self.account_name})")
                            print(f"   💾 Новая сессия создана: {session_file}")
                            return
                        except ApiIdInvalidError:
                            print(f"\n{'='*60}")
                            print(f"✗ ОШИБКА: Неверные API_ID/API_HASH для аккаунта {self.account_name}")
                            print(f"{'='*60}")
                            print(f"Не удалось авторизоваться из-за неверных API_ID/API_HASH.")
                            print(f"Проверьте правильность API_ID и API_HASH в файле .env")
                            print(f"Аккаунт: {self.account_name} ({self.phone_number})")
                            print(f"{'='*60}")
                            raise
                        except Exception:
                            # Если пересоздание тоже не помогло — пусть ошибка всплывает наверх.
                            raise
                    else:
                        # Прочие ошибки при connect лучше считать фатальными для этого аккаунта,
                        # чтобы не оставлять "висящие" задачи Telethon.
                        try:
                            await self.client.disconnect()
                        except Exception:
                            pass
                        raise
        else:
            print(f"   📝 Сессия не найдена. Создаю новую сессию для аккаунта {self.account_name}...")

        # Если сессии нет или она невалидна - выполняем авторизацию с телефоном
        try:
            print(f"   🔐 Выполняю авторизацию для аккаунта {self.account_name}...")
            await self.client.start(phone=self.phone_number)
            print(f"   ✓ Успешно авторизован и подключен к Telegram (аккаунт: {self.account_name})")
            print(f"   💾 Сессия сохранена в файл: {session_file}")
        except ApiIdInvalidError:
            print(f"\n{'='*60}")
            print(f"✗ ОШИБКА: Неверные API_ID/API_HASH для аккаунта {self.account_name}")
            print(f"{'='*60}")
            print(f"Не удалось авторизоваться из-за неверных API_ID/API_HASH.")
            print(f"Проверьте правильность API_ID и API_HASH в файле .env")
            print(f"Аккаунт: {self.account_name} ({self.phone_number})")
            print(f"{'='*60}")
            raise
        except Exception as e:
            error_msg = str(e).lower()
            if 'api_id' in error_msg and 'invalid' in error_msg:
                print(f"\n{'='*60}")
                print(f"✗ ОШИБКА: Неверные API_ID/API_HASH для аккаунта {self.account_name}")
                print(f"{'='*60}")
                print(f"Ошибка: {str(e)}")
                print(f"Проверьте правильность API_ID и API_HASH в файле .env")
                print(f"Аккаунт: {self.account_name} ({self.phone_number})")
                print(f"{'='*60}")
                raise
            else:
                raise
        
    async def check_if_user(self, username):
        """Проверка, является ли сущность пользователем (не каналом/чатом)"""
        try:
            entity = await self.client.get_entity(username)
            # Если это канал или чат, а не пользователь - возвращаем False
            if isinstance(entity, (Channel, Chat)):
                return False
            # Если это пользователь - возвращаем True
            if isinstance(entity, User):
                return True
            return False
        except Exception:
            # Если не удалось получить сущность, попробуем отправить сообщение
            # и обработаем ошибку там
            return None
    
    async def handle_temporary_restriction(self):
        """Автоматическая обработка временного ограничения Telegram через бота @SpamBot"""
        print("\n" + "="*60)
        print("⚠ ВРЕМЕННОЕ ОГРАНИЧЕНИЕ TELEGRAM")
        print("="*60)
        print("Обнаружено временное ограничение. Автоматически обрабатываю через @SpamBot...")
        print("="*60)
        
        # Ждем 3 минуты (180 секунд) с обратным отсчетом
        wait_time = 180
        print(f"\n⏳ Ожидание {wait_time} секунд (3 минуты)...")
        for remaining in range(wait_time, 0, -10):
            print(f"   Осталось: {remaining} секунд", end='\r')
            await asyncio.sleep(10)
        print(f"   Осталось: 0 секунд")
        
        try:
            # Открываем чат с ботом @SpamBot
            print("\n📱 Открываю чат с ботом @SpamBot...")
            spam_bot_username = 'SpamBot'
            
            # Получаем сущность бота
            bot_entity = await self.client.get_entity(spam_bot_username)
            print(f"✓ Бот @SpamBot найден")
            
            # Отправляем команду /start для активации бота
            print("📤 Отправляю команду /start боту...")
            await self.client.send_message(bot_entity, '/start')
            await asyncio.sleep(3)
            
            # Получаем последнее сообщение от бота
            print("📥 Читаю ответ бота...")
            messages = await self.client.get_messages(bot_entity, limit=1)
            
            if messages and messages[0].message:
                bot_message = messages[0].message
                print(f"📄 Сообщение от бота: {bot_message[:100]}...")
                
                # Проверяем начало сообщения (может быть 'Здравствуйте' или 'Dear')
                if bot_message.startswith('Здравствуйте') or bot_message.startswith('Dear'):
                    greeting = 'Здравствуйте' if bot_message.startswith('Здравствуйте') else 'Dear'
                    print(f"✓ Сообщение начинается с '{greeting}' - нажимаю кнопку OK")
                    
                    # Ищем кнопку OK в сообщении
                    button_clicked = False
                    if hasattr(messages[0], 'reply_markup') and messages[0].reply_markup:
                        if hasattr(messages[0].reply_markup, 'rows'):
                            for row in messages[0].reply_markup.rows:
                                if hasattr(row, 'buttons'):
                                    for button in row.buttons:
                                        button_text = getattr(button, 'text', '').strip()
                                        # Ищем кнопку OK (может быть на разных языках и регистрах)
                                        button_text_upper = button_text.upper()
                                        if button_text_upper in ['OK', 'ОК', 'ОКЕЙ', 'OKAY', 'ОКЕЙ'] or button_text_upper.startswith('OK'):
                                            try:
                                                # Пробуем нажать через callback
                                                if hasattr(button, 'data') and button.data:
                                                    await self.client(GetBotCallbackAnswerRequest(
                                                        peer=bot_entity,
                                                        msg_id=messages[0].id,
                                                        data=button.data
                                                    ))
                                                    button_clicked = True
                                                    print(f"✓ Кнопка '{button_text}' нажата успешно")
                                                    await asyncio.sleep(2)
                                                    break
                                            except Exception as callback_e:
                                                # Альтернативный способ - отправляем текст кнопки
                                                try:
                                                    await self.client.send_message(bot_entity, button_text)
                                                    button_clicked = True
                                                    print(f"✓ Кнопка '{button_text}' нажата успешно (альтернативный способ)")
                                                    await asyncio.sleep(2)
                                                    break
                                                except Exception as alt_e:
                                                    print(f"⚠ Ошибка при нажатии кнопки: {str(alt_e)}")
                                        
                                        if button_clicked:
                                            break
                                if button_clicked:
                                    break
                    
                    if button_clicked:
                        print("✓ Продолжаю рассылку...\n")
                        return 'continue'
                    else:
                        print("⚠ Не найдена кнопка OK, но продолжаю рассылку...\n")
                        return 'continue'
                
                elif bot_message.startswith('Ваш аккаунт') or bot_message.startswith('Good news'):
                    message_type = 'Ваш аккаунт' if bot_message.startswith('Ваш аккаунт') else 'Good news'
                    print("\n" + "="*60)
                    print("⚠ АККАУНТ ЗАБЛОКИРОВАН")
                    print("="*60)
                    print(f"Бот Telegram сообщил, что аккаунт заблокирован (сообщение начинается с '{message_type}').")
                    print("Рассылка завершается.")
                    print("="*60)
                    return 'account_free'
                else:
                    print(f"⚠ Неизвестный формат сообщения от бота. Продолжаю рассылку...\n")
                    return 'continue'
            else:
                print("⚠ Не удалось получить сообщение от бота. Продолжаю рассылку...\n")
                return 'continue'
                
        except Exception as e:
            print(f"\n⚠ Ошибка при работе с ботом @SpamBot: {str(e)}")
            print("✓ Продолжаю рассылку...\n")
            return 'continue'
    
    def _normalize_message_command(self, message):
        """Приводит строку или dict к формату команды отправки."""
        if isinstance(message, dict):
            return message
        if isinstance(message, str):
            return {'type': 'TEXT', 'text': message}
        raise TypeError(f"Неподдерживаемый тип сообщения: {type(message)!r}")

    async def _show_typing_for_command(self, username, command):
        """Эффект печатания для текстовых сообщений и премиум-эмодзи."""
        if command.get('type') not in ('TEXT', 'PREMIUM_EMOJI'):
            return
        try:
            await self.client(SetTypingRequest(
                peer=username,
                action=SendMessageTypingAction()
            ))
            await asyncio.sleep(TYPING_DURATION)
        except Exception:
            pass

    async def _fetch_source_message(self, source, message_id):
        """Загружает сообщение из канала/чата по ID."""
        fetched = await self.client.get_messages(source, ids=message_id)
        if isinstance(fetched, list):
            source_message = fetched[0] if fetched else None
        else:
            source_message = fetched

        if not source_message:
            raise ValueError(f"Сообщение #{message_id} не найдено в {source}")

        return source_message

    async def _perform_send_action(self, username, command):
        """Выполняет одну команду отправки для пользователя."""
        command = self._normalize_message_command(command)
        action_type = command['type']

        if action_type == 'TEXT':
            sent_message = await self.client.send_message(
                username,
                command['text'],
                silent=False,
                background=False,
                clear_draft=False,
            )
            print(f"✓ Текст отправлен: {username} (ID: {sent_message.id})")
            return sent_message

        if action_type == 'FORWARD':
            sent_messages = await self.client.forward_messages(
                username,
                command['message_id'],
                command['source'],
            )
            sent_message = sent_messages[0] if isinstance(sent_messages, list) else sent_messages
            print(f"✓ Сообщение переслано: {username} (ID: {sent_message.id})")
            return sent_message

        if action_type == 'PREMIUM_EMOJI':
            source_message = await self._fetch_source_message(
                command['source'],
                command['message_id'],
            )
            text = source_message.message or source_message.text or ''
            entities = source_message.entities

            if not text and not entities:
                raise ValueError(
                    f"Сообщение #{command['message_id']} в {command['source']} не содержит текста"
                )

            sent_message = await self.client.send_message(
                username,
                text,
                formatting_entities=entities,
                silent=False,
                background=False,
                clear_draft=False,
            )
            print(f"✓ Текст с премиум-эмодзи отправлен: {username} (ID: {sent_message.id})")
            return sent_message

        if action_type == 'STICKER':
            document = InputDocument(
                id=command['id'],
                access_hash=command['access_hash'],
                file_reference=command['file_reference'],
            )
            sent_message = await self.client.send_file(username, file=document)
            print(f"✓ Стикер отправлен: {username} (ID: {sent_message.id})")
            return sent_message

        raise ValueError(f"Неизвестный тип команды: {action_type}")

    async def send_message_to_user(self, username, message):
        """Отправка сообщения конкретному пользователю
        Возвращает: True - успешно, False - ошибка, 'not_exists' - аккаунт не существует,
                   'cannot_message' - нельзя написать пользователю (приватность, блок и т.д.),
                   'is_channel' - это канал/чат, 'requires_stars' - требуется оплата через Telegram Stars,
                   'temp_restriction' - временное ограничение, 'account_free' - аккаунт свободен от ограничений,
                   'flood_limit' - превышен лимит сообщений для этого пользователя
        """
        command = self._normalize_message_command(message)

        # Сначала проверяем, не является ли это каналом/чатом
        is_user = await self.check_if_user(username)
        if is_user is False:
            print(f"⚠ {username} является каналом или чатом, а не пользователем - пропускаю")
            return 'is_channel'
        
        try:
            last_error = None
            for attempt in range(1, SEND_RETRY_ATTEMPTS + 1):
                try:
                    await self._ensure_connected()
                    await self._show_typing_for_command(username, command)
                    await self._perform_send_action(username, command)
                    return True
                except Exception as send_error:
                    if self._is_transient_connection_error(send_error) and attempt < SEND_RETRY_ATTEMPTS:
                        last_error = send_error
                        print(
                            f"⚠ Временная сетевая ошибка при отправке {username} "
                            f"(попытка {attempt}/{SEND_RETRY_ATTEMPTS}): {str(send_error)}"
                        )
                        try:
                            await self.client.disconnect()
                        except Exception:
                            pass
                        await asyncio.sleep(SEND_RETRY_DELAY)
                        continue
                    raise

            if last_error:
                raise last_error
        except FloodWaitError as e:
            # Если FloodWait больше или равен 3 минутам, возможно это то самое ограничение
            if e.seconds >= 180:
                print(f"⚠ Обнаружено временное ограничение Telegram ({e.seconds} секунд)")
                restriction_result = await self.handle_temporary_restriction()
                # Если аккаунт свободен от ограничений, завершаем работу
                if restriction_result == 'account_free':
                    return 'account_free'
                # Пытаемся отправить снова после ожидания
                try:
                    await self._perform_send_action(username, command)
                    return True
                except Exception as retry_e:
                    print(f"✗ Не удалось отправить после ожидания: {str(retry_e)}")
                    return False
            else:
                print(f"⚠ Ожидание {e.seconds} секунд из-за ограничения Telegram...")
                await asyncio.sleep(e.seconds)
                return await self.send_message_to_user(username, command)
        except (UsernameNotOccupiedError, UsernameInvalidError, InputUserDeactivatedError) as e:
            print(f"⚠ Аккаунт {username} не существует или был удален - пропускаю")
            return 'not_exists'
        except ChatWriteForbiddenError:
            print(f"⚠ {username} является каналом или чатом, куда нельзя отправлять сообщения - пропускаю")
            return 'is_channel'
        except (UserPrivacyRestrictedError, UserIsBlockedError, UserNotMutualContactError):
            print(f"⚠ Нельзя написать {username}: пользователь недоступен для сообщений - пропускаю")
            return 'cannot_message'
        except PeerFloodError:
            print(f"⚠ Превышен лимит сообщений для {username}")
            print(f"⚠ Обрабатываю ограничение через @SpamBot...")
            # Вызываем обработку временного ограничения через SpamBot
            restriction_result = await self.handle_temporary_restriction()
            # Если аккаунт свободен от ограничений, завершаем работу
            if restriction_result == 'account_free':
                return 'account_free'
            # После обработки пробуем отправить сообщение снова
            try:
                await self._perform_send_action(username, command)
                return True
            except Exception as retry_e:
                print(f"⚠ Не удалось отправить после обработки ограничения: {str(retry_e)}")
                return 'flood_limit'
        except Exception as e:
            # Проверяем, не является ли ошибка связанной с временным ограничением
            error_msg = str(e).lower()
            error_type = type(e).__name__.lower()

            # Если это сетевой/дисконнект сбой после ретраев — оставляем пользователя в файле
            # и просто считаем отправку временно неуспешной.
            if self._is_transient_connection_error(e):
                print(f"⚠ Временная сетевая ошибка для {username} после ретраев: {str(e)}")
                return False
            
            # Проверяем на признаки временного ограничения, требующего ручного подтверждения
            restriction_keywords = [
                'temporary restriction', 'temp restriction', 'wait', 
                'flood', 'too many', 'slow mode', 'rate limit',
                'please wait', 'подождите', 'ограничение', 'restriction',
                'verify', 'verification', 'confirm', 'подтверждение'
            ]
            
            # Проверяем, есть ли в сообщении об ошибке указание на необходимость ручного подтверждения
            if any(keyword in error_msg for keyword in restriction_keywords) or 'flood' in error_type:
                # Проверяем, не является ли это обычным FloodWait (обработан выше)
                # Если это другая ошибка с похожими ключевыми словами, обрабатываем как временное ограничение
                print(f"⚠ Обнаружено временное ограничение Telegram")
                restriction_result = await self.handle_temporary_restriction()
                # Если аккаунт свободен от ограничений, завершаем работу
                if restriction_result == 'account_free':
                    return 'account_free'
                # Пытаемся отправить снова после ожидания
                try:
                    await self._perform_send_action(username, command)
                    return True
                except Exception as retry_e:
                    print(f"✗ Не удалось отправить после ожидания: {str(retry_e)}")
                    return False
            
            # Проверяем, не является ли ошибка связанной с Telegram Stars (требуется оплата)
            stars_keywords = [
                'stars', 'star', 'payment required', 'payment', 'pay', 
                'premium', 'monetization', 'monetize', 'paid message',
                'requires payment', 'нужна оплата', 'звезды', 'звезд'
            ]
            if any(keyword in error_msg for keyword in stars_keywords) or 'payment' in error_type:
                print(f"⚠ {username} требует оплату через Telegram Stars - пропускаю")
                return 'requires_stars'
            
            # Проверяем, не является ли ошибка связанной с несуществующим аккаунтом
            if any(keyword in error_msg for keyword in [
                'not found', 'does not exist', 'invalid', 'deactivated',
                'username not occupied', 'no user has'
            ]):
                print(f"⚠ Аккаунт {username} не существует или недоступен - пропускаю")
                return 'not_exists'
            # Проверяем, нельзя ли написать пользователю
            if any(keyword in error_msg for keyword in [
                'privacy', 'restricted', 'user_is_blocked', 'user_not_mutual',
                'not mutual', 'message not allowed', 'forbidden', 'blocked by user'
            ]):
                print(f"⚠ Нельзя написать {username}: пользователь недоступен для сообщений - пропускаю")
                return 'cannot_message'
            # Проверяем, не является ли ошибка связанной с каналом/чатом
            if any(keyword in error_msg for keyword in ['chat', 'channel', 'write forbidden', 'can\'t write']):
                print(f"⚠ {username} является каналом или чатом - пропускаю")
                return 'is_channel'
            print(f"✗ Ошибка при отправке {username}: {str(e)}")
            return False
    
    def check_stop_flag(self):
        """Проверка флага остановки рассылки"""
        stop_flag_file = 'stop_sending.txt'
        if os.path.exists(stop_flag_file):
            return True
        return False
    
    async def send_bulk_messages(self, users_list, messages_list, users_file=None, max_successful_users=None):
        """Массовая рассылка сообщений
        messages_list - список сообщений для отправки (по порядку)
        users_file - путь к файлу с пользователями (для автоматического удаления после рассылки)
        """
        # Проверяем, что клиент создан и подключен
        if not self.client:
            await self.connect()
        elif not self.client.is_connected():
            # Переподключаемся, если соединение потеряно
            await self.client.connect()
            if not await self.client.is_user_authorized():
                await self.connect()
            
        total_users = len(users_list)
        messages_count = len(messages_list)
        total_messages = total_users * messages_count
        successful_messages = 0
        failed_messages = 0
        successful_users = 0
        failed_users = 0
        not_existing_accounts = 0
        cannot_message_users = 0
        channels_skipped = 0
        stars_required = 0
        flood_limited = 0  # Количество пользователей с превышенным лимитом
        successful_usernames = []  # Список успешно обработанных пользователей
        flood_limited_usernames = []  # Список пользователей с превышенным лимитом (для удаления из файла)
        # Количество пользователей, которым было отправлено хотя бы одно сообщение
        users_with_at_least_one_message = 0
        
        print(f"\nНачинаю рассылку...")
        print(f"Пользователей: {total_users}")
        print(f"Сообщений на пользователя: {messages_count}")
        print(f"Всего сообщений: {total_messages}")
        print(f"Задержка между сообщениями для одного пользователя: {DELAY_BETWEEN_MESSAGES} сек")
        print(f"Задержка между пользователями: случайная от {DELAY_BETWEEN_USERS_MIN} до {DELAY_BETWEEN_USERS_MAX} сек")
        print(f"Пауза после каждых {DELAY_AFTER_EVERY_N_USERS} пользователей: {DELAY_AFTER_N_USERS} сек")
        print(f"\n💡 Для остановки рассылки:")
        print(f"   - Нажмите Ctrl+C в этом окне")
        print(f"   - Или создайте файл 'stop_sending.txt' в папке со скриптом\n")

        def should_remove_user_after_processing(
            *,
            user_got_message_local,
            account_not_exists_local,
            cannot_message_local,
            is_channel_local,
            requires_stars_local
        ):
            """Удаляем успешных и заведомо недоступных пользователей."""
            return (
                user_got_message_local
                or account_not_exists_local
                or cannot_message_local
                or is_channel_local
                or requires_stars_local
            )

        def should_skip_user_delay(
            *,
            account_not_exists_local,
            cannot_message_local,
            is_channel_local,
            requires_stars_local
        ):
            """Без паузы перед следующим юзером — заведомо не подходит для рассылки."""
            return (
                account_not_exists_local
                or cannot_message_local
                or is_channel_local
                or requires_stars_local
            )
        
        for i, username in enumerate(users_list, 1):
            # Убираем @ если есть
            username = username.strip().lstrip('@')
            
            print(f"\n[{i}/{total_users}] Обработка пользователя: {username}")
            
            user_success = True
            user_got_message = False  # Было ли отправлено хотя бы одно сообщение этому пользователю
            account_not_exists = False
            cannot_message = False
            is_channel = False
            requires_stars = False
            flood_limited_user = False
            
            # Отправка нескольких сообщений одному пользователю
            for msg_num in range(1, messages_count + 1):
                # Проверяем флаг остановки перед каждым сообщением
                if self.check_stop_flag():
                    print(f"\n{'='*50}")
                    print(f"⚠ РАССЫЛКА ОСТАНОВЛЕНА ПОЛЬЗОВАТЕЛЕМ")
                    print(f"{'='*50}")
                    print(f"Обнаружен файл stop_sending.txt - рассылка прервана")
                    print(f"Обработано пользователей до остановки: {i-1}/{total_users}")
                    print(f"Текущий пользователь: {username} (сообщение {msg_num}/{messages_count})")
                    print(f"Успешно отправлено сообщений: {successful_messages}")
                    print(f"{'='*50}")
                    # Перед остановкой удаляем только если пользователь уже обработан
                    # (успех или заведомо неподходящая причина пропуска).
                    if users_file and should_remove_user_after_processing(
                        user_got_message_local=user_got_message,
                        account_not_exists_local=account_not_exists,
                        cannot_message_local=cannot_message,
                        is_channel_local=is_channel,
                        requires_stars_local=requires_stars
                    ):
                        remove_user_from_file(users_file, username)
                    # Удаляем флаг остановки
                    try:
                        os.remove('stop_sending.txt')
                        print(f"✓ Файл stop_sending.txt удален")
                    except:
                        pass
                    return
                
                print(f"  → Сообщение {msg_num}/{messages_count} для {username}")
                
                # Выбираем сообщение по номеру (msg_num - 1, так как индексация с 0)
                message_index = (msg_num - 1) % len(messages_list)
                message_to_send = messages_list[message_index]
                
                result = await self.send_message_to_user(username, message_to_send)
                
                if result == True:
                    successful_messages += 1
                    user_got_message = True
                elif result == 'account_free':
                    # Аккаунт свободен от ограничений - завершаем рассылку
                    print(f"\n{'='*50}")
                    print(f"Рассылка прервана: аккаунт свободен от ограничений")
                    print(f"Обработано пользователей до прерывания: {i-1}/{total_users}")
                    print(f"Успешно отправлено сообщений: {successful_messages}")
                    print(f"{'='*50}")
                    
                    # Пользователи уже удаляются сразу после обработки, поэтому здесь только статистика
                    if users_file:
                        print(f"\n📊 Статистика удаления пользователей из файла {users_file}:")
                        print(f"   - Успешно обработано и удалено: {len(successful_usernames)}")
                        if flood_limited_usernames:
                            print(f"   - С превышенным лимитом и удалено: {len(flood_limited_usernames)}")
                        print(f"   - Все пользователи удалены сразу после обработки")
                    
                    return
                elif result == 'not_exists':
                    # Аккаунт не существует - пропускаем остальные сообщения для этого пользователя
                    failed_messages += 1
                    user_success = False
                    account_not_exists = True
                    not_existing_accounts += 1
                    print(f"  ⚠ Пропускаю остальные сообщения для {username} (аккаунт не существует)")
                    break
                elif result == 'cannot_message':
                    failed_messages += 1
                    user_success = False
                    cannot_message = True
                    cannot_message_users += 1
                    print(f"  ⚠ Пропускаю остальные сообщения для {username} (нельзя написать)")
                    break
                elif result == 'is_channel':
                    # Это канал/чат - пропускаем остальные сообщения для этого пользователя
                    failed_messages += 1
                    user_success = False
                    is_channel = True
                    channels_skipped += 1
                    print(f"  ⚠ Пропускаю остальные сообщения для {username} (это канал/чат)")
                    break
                elif result == 'requires_stars':
                    # Требуется оплата через Telegram Stars - пропускаем остальные сообщения для этого пользователя
                    failed_messages += 1
                    user_success = False
                    requires_stars = True
                    stars_required += 1
                    print(f"  ⚠ Пропускаю остальные сообщения для {username} (требуется оплата через Telegram Stars)")
                    break
                elif result == 'flood_limit':
                    # Превышен лимит сообщений для этого пользователя - пропускаем остальные сообщения
                    failed_messages += 1
                    user_success = False
                    flood_limited_user = True
                    flood_limited += 1
                    flood_limited_usernames.append(username)
                    print(f"  ⚠ Пропускаю остальные сообщения для {username} (превышен лимит сообщений)")
                    break
                else:
                    failed_messages += 1
                    user_success = False
                
                # Задержка между сообщениями для одного пользователя (кроме последнего сообщения)
                if msg_num < messages_count and not account_not_exists and not cannot_message and not is_channel and not requires_stars and not flood_limited_user:
                    # Проверяем флаг остановки перед задержкой
                    if self.check_stop_flag():
                        print(f"\n{'='*50}")
                        print(f"⚠ РАССЫЛКА ОСТАНОВЛЕНА ПОЛЬЗОВАТЕЛЕМ")
                        print(f"{'='*50}")
                        print(f"Обнаружен файл stop_sending.txt - рассылка прервана")
                        print(f"Обработано пользователей до остановки: {i-1}/{total_users}")
                        print(f"Текущий пользователь: {username} (сообщение {msg_num}/{messages_count})")
                        print(f"Успешно отправлено сообщений: {successful_messages}")
                        print(f"{'='*50}")
                        # Перед остановкой удаляем только если пользователь уже обработан
                        # (успех или заведомо неподходящая причина пропуска).
                        if users_file and should_remove_user_after_processing(
                            user_got_message_local=user_got_message,
                            account_not_exists_local=account_not_exists,
                            cannot_message_local=cannot_message,
                            is_channel_local=is_channel,
                            requires_stars_local=requires_stars
                        ):
                            remove_user_from_file(users_file, username)
                        # Удаляем флаг остановки
                        try:
                            os.remove('stop_sending.txt')
                            print(f"✓ Файл stop_sending.txt удален")
                        except:
                            pass
                        return
                    
                    await asyncio.sleep(DELAY_BETWEEN_MESSAGES)
            
            # Удаляем пользователя из файла при успешной отправке
            # или если ему заведомо нельзя/невозможно написать.
            if users_file:
                should_remove_user = should_remove_user_after_processing(
                    user_got_message_local=user_got_message,
                    account_not_exists_local=account_not_exists,
                    cannot_message_local=cannot_message,
                    is_channel_local=is_channel,
                    requires_stars_local=requires_stars
                )

                if should_remove_user:
                    if remove_user_from_file(users_file, username):
                        if user_success:
                            print(f"  ✓ Пользователь {username} удален из файла (успешно обработан)")
                        elif account_not_exists:
                            print(f"  ✓ Пользователь {username} удален из файла (не найден)")
                        elif cannot_message:
                            print(f"  ✓ Пользователь {username} удален из файла (нельзя написать)")
                        else:
                            print(f"  ✓ Пользователь {username} удален из файла (неподходящий для рассылки)")
                    else:
                        print(f"  ⚠ Не удалось удалить пользователя {username} из файла")
                else:
                    print(f"  ↩ Пользователь {username} оставлен в файле (временная/неопределенная ошибка)")
            
            if user_success:
                successful_users += 1
                # Сохраняем username успешно обработанного пользователя
                successful_usernames.append(username)
            else:
                failed_users += 1

            # Считаем пользователей, которым отправилось хотя бы одно сообщение
            if user_got_message:
                users_with_at_least_one_message += 1
                # Если задан лимит по таким пользователям и мы его достигли — останавливаем рассылку
                if max_successful_users is not None and users_with_at_least_one_message >= max_successful_users:
                    print(f"\n{'='*50}")
                    print(f"Достигнут лимит пользователей, которым отправлено сообщение: {users_with_at_least_one_message}/{max_successful_users}")
                    print(f"Рассылка для этого аккаунта остановлена по лимиту.")
                    print(f"{'='*50}")
                    break
            
            # Задержка между пользователями (кроме последнего)
            if i < total_users:
                skip_user_delay = should_skip_user_delay(
                    account_not_exists_local=account_not_exists,
                    cannot_message_local=cannot_message,
                    is_channel_local=is_channel,
                    requires_stars_local=requires_stars,
                )

                if skip_user_delay:
                    print(f"  ⏭ Без задержки — пользователь недоступен, перехожу к следующему")
                    continue

                # Проверяем флаг остановки перед задержкой
                if self.check_stop_flag():
                    print(f"\n{'='*50}")
                    print(f"⚠ РАССЫЛКА ОСТАНОВЛЕНА ПОЛЬЗОВАТЕЛЕМ")
                    print(f"{'='*50}")
                    print(f"Обнаружен файл stop_sending.txt - рассылка прервана")
                    print(f"Обработано пользователей до остановки: {i}/{total_users}")
                    print(f"Успешно отправлено сообщений: {successful_messages}")
                    print(f"{'='*50}")
                    # Удаляем флаг остановки
                    try:
                        os.remove('stop_sending.txt')
                        print(f"✓ Файл stop_sending.txt удален")
                    except:
                        pass
                    return
                
                # Случайная задержка между пользователями от 10 до 45 секунд
                delay = random.randint(DELAY_BETWEEN_USERS_MIN, DELAY_BETWEEN_USERS_MAX)
                print(f"  ⏳ Задержка {delay} секунд перед следующим пользователем...")
                await asyncio.sleep(delay)
                
                # Пауза после каждых N пользователей
                if i % DELAY_AFTER_EVERY_N_USERS == 0:
                    # Проверяем флаг остановки перед паузой
                    if self.check_stop_flag():
                        print(f"\n{'='*50}")
                        print(f"⚠ РАССЫЛКА ОСТАНОВЛЕНА ПОЛЬЗОВАТЕЛЕМ")
                        print(f"{'='*50}")
                        print(f"Обнаружен файл stop_sending.txt - рассылка прервана")
                        print(f"Обработано пользователей до остановки: {i}/{total_users}")
                        print(f"Успешно отправлено сообщений: {successful_messages}")
                        print(f"{'='*50}")
                        # Удаляем флаг остановки
                        try:
                            os.remove('stop_sending.txt')
                            print(f"✓ Файл stop_sending.txt удален")
                        except:
                            pass
                        return
                    
                    print(f"\n⏸ Пауза {DELAY_AFTER_N_USERS} секунд после {i} пользователей...")
                    await asyncio.sleep(DELAY_AFTER_N_USERS)
        
        print(f"\n{'='*50}")
        print(f"Рассылка завершена!")
        print(f"Успешно обработано пользователей (все сообщения прошли): {successful_users}/{total_users}")
        print(f"Пользователей, которым отправлено хотя бы одно сообщение: {users_with_at_least_one_message}"
              + (f"/{max_successful_users}" if max_successful_users is not None else ""))
        print(f"Ошибок при обработке пользователей: {failed_users}")
        if not_existing_accounts > 0:
            print(f"Несуществующих аккаунтов: {not_existing_accounts}")
        if cannot_message_users > 0:
            print(f"Недоступных для сообщений: {cannot_message_users}")
        if channels_skipped > 0:
            print(f"Пропущено каналов/чатов: {channels_skipped}")
        if stars_required > 0:
            print(f"Требуют оплату через Telegram Stars: {stars_required}")
        if flood_limited > 0:
            print(f"Пользователей с превышенным лимитом: {flood_limited}")
        print(f"Успешно отправлено сообщений: {successful_messages}/{total_messages}")
        print(f"Ошибок при отправке сообщений: {failed_messages}")
        print(f"{'='*50}")
        
        # Пользователи уже удаляются сразу после обработки, поэтому здесь только статистика
        if users_file:
            print(f"\n📊 Статистика удаления пользователей из файла {users_file}:")
            print(f"   - Успешно обработано и удалено: {len(successful_usernames)}")
            if flood_limited_usernames:
                print(f"   - С превышенным лимитом и удалено: {len(flood_limited_usernames)}")
            print(f"   - Все пользователи удалены сразу после обработки")
        
    async def disconnect(self):
        """Отключение от Telegram"""
        if self.client:
            await self.client.disconnect()
            print(f"✓ Отключено от Telegram (аккаунт: {self.account_name})")


def format_proxy_display(proxy):
    """Форматирует прокси для вывода в консоль (без пароля)."""
    if not proxy:
        return "нет"
    proxy_type, host, port = proxy[0], proxy[1], proxy[2]
    # Telethon: (type, host, port, rdns, user, pass) — 6 элементов
    has_auth = len(proxy) >= 6 and bool(proxy[4] and proxy[5])
    auth_suffix = " (с авторизацией)" if has_auth else ""
    return f"{proxy_type}://{host}:{port}{auth_suffix}"


def parse_proxy(proxy_string):
    """Парсинг строки прокси в формат для Telethon.
    Поддерживаемые форматы:
    - socks5://user:pass@host:port
    - http://user:pass@host:port
    - socks5://host:port
    - http://host:port
    - host:port:user:pass
    - user:pass@host:port
    """
    if not proxy_string or not proxy_string.strip():
        return None

    original_proxy_string = proxy_string.strip()

    try:
        proxy_string = original_proxy_string
        proxy_type = 'socks5'

        if proxy_string.startswith('socks5://'):
            proxy_string = proxy_string[len('socks5://'):]
            proxy_type = 'socks5'
        elif proxy_string.startswith('socks4://'):
            proxy_string = proxy_string[len('socks4://'):]
            proxy_type = 'socks4'
        elif proxy_string.startswith('http://'):
            proxy_string = proxy_string[len('http://'):]
            proxy_type = 'http'
        elif proxy_string.startswith('https://'):
            proxy_string = proxy_string[len('https://'):]
            proxy_type = 'http'

        username = None
        password = None
        host_port = proxy_string

        if '@' in proxy_string:
            auth_part, host_port = proxy_string.rsplit('@', 1)
            if ':' in auth_part:
                username, password = auth_part.split(':', 1)
            else:
                username, password = auth_part, None
        elif proxy_string.count(':') >= 3 and '://' not in original_proxy_string:
            # Формат host:port:user:pass (часто у прокси-провайдеров)
            host, port, username, password = proxy_string.split(':', 3)
            port = int(port)
            if username and password:
                # Telethon: (type, host, port, rdns, username, password)
                return (proxy_type, host, port, True, username, password)
            return (proxy_type, host, port)

        if ':' in host_port:
            host, port = host_port.rsplit(':', 1)
            port = int(port)
        else:
            host = host_port
            port = 1080 if proxy_type in ('socks5', 'socks4') else 8080

        if username and password:
            return (proxy_type, host, port, True, username, password)
        return (proxy_type, host, port)
    except Exception as e:
        print(f"⚠ Ошибка при парсинге прокси '{original_proxy_string}': {str(e)}")
        return None


def check_proxy_dependencies(accounts):
    """Проверяет, установлена ли библиотека для работы прокси."""
    if not any(acc.get('proxy') for acc in accounts):
        return True

    try:
        import python_socks  # noqa: F401
        return True
    except ImportError:
        try:
            import socks  # noqa: F401
            return True
        except ImportError:
            print("\n⚠ ОШИБКА: Для прокси нужен пакет python-socks")
            print("   Установите зависимости командой:")
            print("   py -m pip install -r requirements.txt")
            print("   или:")
            print('   py -m pip install "python-socks[asyncio]"')
            return False


def load_accounts():
    """Загрузка аккаунтов из переменных окружения"""
    accounts = []
    
    # Сначала проверяем новый формат (несколько аккаунтов)
    # Формат: TELEGRAM_ACCOUNT_1_API_ID, TELEGRAM_ACCOUNT_1_API_HASH, TELEGRAM_ACCOUNT_1_PHONE
    # Проверяем до 100 аккаунтов, чтобы найти все, даже если есть пропуски
    new_format_found = False
    max_accounts = 100  # Максимальное количество аккаунтов для проверки
    empty_count = 0  # Счетчик пустых аккаунтов подряд
    max_empty = 10  # Увеличено до 10 пустых подряд перед остановкой (для большей надежности)
    
    print(f"\n🔍 Поиск аккаунтов в .env файле...")
    print(f"   Проверяю до {max_accounts} аккаунтов...")
    print(f"   Остановка после {max_empty} пустых аккаунтов подряд\n")
    
    for account_num in range(1, max_accounts + 1):
        api_id = os.getenv(f'TELEGRAM_ACCOUNT_{account_num}_API_ID', '').strip()
        api_hash = os.getenv(f'TELEGRAM_ACCOUNT_{account_num}_API_HASH', '').strip()
        phone = os.getenv(f'TELEGRAM_ACCOUNT_{account_num}_PHONE', '').strip()
        name = os.getenv(f'TELEGRAM_ACCOUNT_{account_num}_NAME', f'Account_{account_num}').strip()
        
        # Файл со списком пользователей для этого аккаунта
        users_file_env = os.getenv(f'TELEGRAM_ACCOUNT_{account_num}_USERS_FILE', '').strip()
        if users_file_env:
            users_file = users_file_env
        else:
            users_file = f'users{account_num}.txt'
        
        # Детальная проверка всех полей
        missing_fields = []
        if not api_id:
            missing_fields.append('API_ID')
        if not api_hash:
            missing_fields.append('API_HASH')
        if not phone:
            missing_fields.append('PHONE')
        
        # Проверяем, есть ли все необходимые данные для аккаунта
        if api_id and api_hash and phone:
            new_format_found = True
            empty_count = 0  # Сбрасываем счетчик пустых
            # Загружаем прокси для этого аккаунта (опционально)
            proxy_string = os.getenv(f'TELEGRAM_ACCOUNT_{account_num}_PROXY', '').strip()
            proxy = parse_proxy(proxy_string) if proxy_string else None
            if proxy_string and not proxy:
                proxy_info = " - прокси: ⚠ неверный формат"
            elif proxy:
                proxy_info = f" - прокси: {format_proxy_display(proxy)}"
            else:
                proxy_info = " - прокси: нет"
            print(f"   ✓ Аккаунт {account_num}: {name} ({phone}) - файл: {users_file}{proxy_info}")
            accounts.append({
                'api_id': api_id,
                'api_hash': api_hash,
                'phone': phone,
                'name': name,
                'users_file': users_file,
                'proxy': proxy
            })
        else:
            # Проверяем, есть ли хотя бы одно поле заполнено (частично заполненный аккаунт)
            has_partial_data = api_id or api_hash or phone
            if has_partial_data:
                # Если есть частичные данные, выводим предупреждение
                print(f"   ⚠ Аккаунт {account_num}: неполные данные - отсутствует: {', '.join(missing_fields)}")
                empty_count = 0  # Не считаем как пустой, если есть хотя бы часть данных
            else:
                # Если аккаунт полностью пустой, увеличиваем счетчик пустых
                empty_count += 1
                # Если нашли несколько аккаунтов и потом много пустых подряд, останавливаемся
                if new_format_found and empty_count >= max_empty:
                    print(f"\n   ⏹ Остановка поиска: найдено {len(accounts)} аккаунтов, затем {max_empty} пустых подряд")
                    break
    
    # Если новый формат не найден, проверяем старый формат (один аккаунт)
    if not new_format_found and API_ID and API_HASH and PHONE_NUMBER:
        users_file = os.getenv('TELEGRAM_USERS_FILE', 'users.txt')
        # Загружаем прокси для старого формата (опционально)
        proxy_string = os.getenv('TELEGRAM_PROXY', '').strip()
        proxy = parse_proxy(proxy_string) if proxy_string else None
        accounts.append({
            'api_id': API_ID,
            'api_hash': API_HASH,
            'phone': PHONE_NUMBER,
            'name': PHONE_NUMBER,
            'users_file': users_file,
            'proxy': proxy
        })
    
    return accounts


def load_users_from_file(filename):
    """Загрузка списка пользователей из файла"""
    if not os.path.exists(filename):
        return None
    
    with open(filename, 'r', encoding='utf-8') as f:
        users = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
    
    return users if users else None


def remove_user_from_file(filename, username):
    """Удаление одного пользователя из файла сразу после обработки"""
    if not os.path.exists(filename):
        return False
    
    # Нормализуем username для удаления (убираем @ и пробелы)
    username_normalized = username.strip().lstrip('@').lower()
    
    # Читаем все строки из файла
    with open(filename, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Фильтруем строки: оставляем только те, которые не нужно удалять
    remaining_lines = []
    removed = False
    
    for line in lines:
        line_stripped = line.strip()
        # Сохраняем комментарии и пустые строки
        if not line_stripped or line_stripped.startswith('#'):
            remaining_lines.append(line)
        else:
            # Нормализуем username для сравнения
            line_username_normalized = line_stripped.lstrip('@').lower()
            if line_username_normalized != username_normalized:
                remaining_lines.append(line)
            else:
                removed = True
    
    # Записываем обновленный список обратно в файл
    if removed:
        with open(filename, 'w', encoding='utf-8') as f:
            f.writelines(remaining_lines)
    
    return removed


def remove_users_from_file(filename, users_to_remove):
    """Удаление нескольких пользователей из файла после успешной рассылки"""
    if not os.path.exists(filename):
        return False
    
    # Нормализуем список пользователей для удаления (убираем @ и пробелы)
    users_to_remove_normalized = {user.strip().lstrip('@').lower() for user in users_to_remove}
    
    # Читаем все строки из файла
    with open(filename, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Фильтруем строки: оставляем только те, которые не нужно удалять
    remaining_lines = []
    removed_count = 0
    
    for line in lines:
        line_stripped = line.strip()
        # Сохраняем комментарии и пустые строки
        if not line_stripped or line_stripped.startswith('#'):
            remaining_lines.append(line)
        else:
            # Нормализуем username для сравнения
            username_normalized = line_stripped.lstrip('@').lower()
            if username_normalized not in users_to_remove_normalized:
                remaining_lines.append(line)
            else:
                removed_count += 1
    
    # Записываем обновленный список обратно в файл
    with open(filename, 'w', encoding='utf-8') as f:
        f.writelines(remaining_lines)
    
    return removed_count > 0


async def main():
    """Основная функция"""
    print("="*50)
    print("Telegram Message Sender")
    print("="*50)
    
    # Загрузка аккаунтов
    accounts = load_accounts()

    if accounts and not check_proxy_dependencies(accounts):
        return
    
    if not accounts:
        print("\n⚠ ОШИБКА: Необходимо указать хотя бы один аккаунт")
        print("\nСпособы настройки:")
        print("1. Один аккаунт (старый формат):")
        print("   TELEGRAM_API_ID=ваш_api_id")
        print("   TELEGRAM_API_HASH=ваш_api_hash")
        print("   TELEGRAM_PHONE=+1234567890")
        print("\n2. Несколько аккаунтов (новый формат):")
        print("   TELEGRAM_ACCOUNT_1_API_ID=api_id_1")
        print("   TELEGRAM_ACCOUNT_1_API_HASH=api_hash_1")
        print("   TELEGRAM_ACCOUNT_1_PHONE=+1234567890")
        print("   TELEGRAM_ACCOUNT_1_NAME=Account1 (опционально)")
        print("   TELEGRAM_ACCOUNT_1_PROXY=socks5://user:pass@host:port (опционально)")
        print("   TELEGRAM_ACCOUNT_2_API_ID=api_id_2")
        print("   TELEGRAM_ACCOUNT_2_API_HASH=api_hash_2")
        print("   TELEGRAM_ACCOUNT_2_PHONE=+0987654321")
        print("   TELEGRAM_ACCOUNT_2_NAME=Account2 (опционально)")
        print("\nКак получить API_ID и API_HASH:")
        print("1. Перейдите на https://my.telegram.org/apps")
        print("2. Войдите в свой аккаунт")
        print("3. Создайте приложение и получите API_ID и API_HASH")
        return
    
    print(f"\n{'='*60}")
    print(f"📋 ЗАГРУЗКА АККАУНТОВ ИЗ .ENV")
    print(f"{'='*60}")
    print(f"✓ Загружено аккаунтов из .env: {len(accounts)}")
    if len(accounts) == 0:
        print(f"⚠ ВНИМАНИЕ: Не найдено ни одного аккаунта в .env файле!")
        print(f"   Проверьте, что переменные окружения указаны правильно.")
    for i, acc in enumerate(accounts, 1):
        print(f"\n   Аккаунт {i}: {acc['name']} ({acc['phone']})")
        print(f"      Файл пользователей: {acc['users_file']}")
        print(f"      Прокси: {format_proxy_display(acc.get('proxy'))}")
        # Проверяем, существует ли файл
        if os.path.exists(acc['users_file']):
            print(f"      ✓ Файл существует")
        else:
            print(f"      ⚠ Файл НЕ существует!")
    
    # Загрузка списков пользователей для каждого аккаунта
    accounts_with_users = []
    print(f"\n📋 Загрузка пользователей для каждого аккаунта...")
    for acc in accounts:
        print(f"   Проверяю аккаунт {acc['name']} - файл: {acc['users_file']}")
        users = load_users_from_file(acc['users_file'])
        if users is None:
            # Если файл не существует, создаем пример
            print(f"\n⚠ Файл {acc['users_file']} не найден для аккаунта {acc['name']}!")
            print(f"Создаю пример файла {acc['users_file']}...")
            with open(acc['users_file'], 'w', encoding='utf-8') as f:
                f.write("# Список пользователей (по одному на строку)\n")
                f.write("# Можно использовать @username или просто username\n")
                f.write("# Строки, начинающиеся с #, игнорируются\n")
                f.write("@username1\n")
                f.write("username2\n")
            print(f"✓ Файл создан. Отредактируйте его и добавьте список пользователей.")
            return
        
        if not users:
            print(f"   ⚠ Файл {acc['users_file']} для аккаунта {acc['name']} пуст или содержит только комментарии - пропускаю")
            continue
        
        print(f"   ✓ Аккаунт {acc['name']}: загружено {len(users)} пользователей")
        accounts_with_users.append({
            'account': acc,
            'users': users
        })
    
    if not accounts_with_users:
        print("\n⚠ Нет аккаунтов с пользователями для рассылки!")
        return
    
    # Загрузка сообщений из message1.txt, message2.txt, ...
    messages = load_message_commands()

    if not messages:
        message1_file = 'message1.txt'
        if not os.path.exists(message1_file):
            print(f"\n⚠ Файлы messageN.txt не найдены!")
            print(f"Создаю пример файла {message1_file}...")
            with open(message1_file, 'w', encoding='utf-8') as f:
                f.write("TEXT\n")
                f.write("Первое сообщение")
            print(f"✓ Файл создан. Отредактируйте его и добавьте команды отправки.")
        else:
            print(f"\n⚠ Не удалось загрузить команды из messageN.txt!")
        return
    
    # Показываем статистику по пользователям для каждого аккаунта
    total_users = sum(len(acc['users']) for acc in accounts_with_users)
    print(f"\n✓ Всего пользователей для рассылки: {total_users}")
    for acc_data in accounts_with_users:
        print(f"   {acc_data['account']['name']}: {len(acc_data['users'])} пользователей")
    
    print(f"\nКоличество сообщений: {len(messages)}")
    print(f"\nПредварительный просмотр сообщений:")
    print("-" * 50)
    for i, command in enumerate(messages, 1):
        print(f"Сообщение {i} ({command.get('file', f'message{i}.txt')}) [{command['type']}]:")
        print(format_message_command_preview(command))
        print()
    print("-" * 50)
    
    # Инициализация и параллельное подключение всех аккаунтов
    print(f"\n{'='*60}")
    print(f"Подключение к аккаунтам (параллельно)...")
    print(f"{'='*60}")
    print(f"Количество аккаунтов для подключения: {len(accounts_with_users)}")
    
    async def connect_account(acc_data):
        """Подключение одного аккаунта"""
        acc = acc_data['account']
        print(f"\n🔌 Подключение к аккаунту: {acc['name']} ({acc['phone']})")
        proxy = acc.get('proxy', None)
        sender = TelegramSender(acc['api_id'], acc['api_hash'], acc['phone'], acc['name'], proxy=proxy)
        
        try:
            await sender.connect()
            print(f"   ✓ Аккаунт {acc['name']} успешно подключен")
            return {
                'success': True,
                'sender': sender,
                'users': acc_data['users'],
                'users_file': acc_data['account']['users_file'],
                'account_name': acc['name']
            }
        except ApiIdInvalidError:
            print(f"   ✗ Пропускаю аккаунт {acc['name']} из-за неверных API_ID/API_HASH")
            return {
                'success': False,
                'account_name': acc['name'],
                'error': 'invalid_api'
            }
        except Exception as e:
            error_msg = str(e).lower()
            if 'api_id' in error_msg and 'invalid' in error_msg:
                print(f"   ✗ Пропускаю аккаунт {acc['name']} из-за неверных API_ID/API_HASH")
                return {
                    'success': False,
                    'account_name': acc['name'],
                    'error': 'invalid_api'
                }
            else:
                print(f"   ✗ Ошибка при подключении аккаунта {acc['name']}: {str(e)}")
                return {
                    'success': False,
                    'account_name': acc['name'],
                    'error': str(e)
                }
    
    # Подключаем все аккаунты параллельно
    print(f"\n🚀 Запускаю параллельное подключение всех аккаунтов...")
    connection_results = await asyncio.gather(
        *(connect_account(acc_data) for acc_data in accounts_with_users),
        return_exceptions=True
    )
    
    # Обрабатываем результаты подключения
    senders = []
    for result in connection_results:
        if isinstance(result, Exception):
            print(f"\n✗ Критическая ошибка при подключении: {str(result)}")
            continue
        
        if result.get('success', False):
            senders.append({
                'sender': result['sender'],
                'users': result['users'],
                'users_file': result['users_file']
            })
        else:
            account_name = result.get('account_name', 'Unknown')
            error = result.get('error', 'unknown')
            print(f"   ⚠ Аккаунт {account_name} пропущен (ошибка: {error})")
    
    if not senders:
        print(f"\n{'='*60}")
        print(f"✗ ОШИБКА: Не удалось подключиться ни к одному аккаунту!")
        print(f"{'='*60}")
        print(f"Проверьте правильность API_ID и API_HASH в файле .env")
        return
    
    print(f"\n{'='*60}")
    print(f"✓ Успешно подключено аккаунтов: {len(senders)}")
    if len(senders) < len(accounts_with_users):
        print(f"⚠ Пропущено аккаунтов: {len(accounts_with_users) - len(senders)}")
    print(f"{'='*60}")
    print(f"\n📊 Статус подключенных аккаунтов:")
    for i, sender_data in enumerate(senders):
        # Находим имя аккаунта из исходного списка
        account_name = None
        for acc_data in accounts_with_users:
            if acc_data['account']['api_id'] == sender_data['sender'].api_id:
                account_name = acc_data['account']['name']
                break
        if not account_name:
            account_name = f"Account_{i+1}"
        users_count = len(sender_data['users'])
        session_name = f'session_{account_name.replace("+", "").replace("-", "_")}.session'
        session_exists = "✓ Сессия существует" if os.path.exists(session_name) else "⚠ Новая сессия"
        print(f"   Аккаунт {i+1}: {account_name}")
        print(f"      - Пользователей: {users_count}")
        print(f"      - Сессия: {session_exists}")
    
    # Подтверждение после подключения
    print(f"\n{'='*60}")
    confirm = input("Все аккаунты подключены. Начать рассылку? (yes/no): ").strip().lower()
    if confirm not in ['yes', 'y', 'да', 'д']:
        print("Отменено пользователем. Отключаю аккаунты...")
        # Отключаем все аккаунты при отмене
        for sender_data in senders:
            await sender_data['sender'].disconnect()
        return
    
    async def process_account(sender_data, max_successful_users=None, skip=False):
        """Обработка рассылки для одного аккаунта"""
        sender = sender_data['sender']
        account_users = sender_data['users']
        users_file = sender_data['users_file']
        # Получаем имя аккаунта из sender
        account_name = sender.account_name

        if skip:
            print(f"\n{'='*60}")
            print(f"⏭ Аккаунт пропущен: {account_name}")
            print(f"   Рассылка на этом аккаунте не выполняется (лимит = 0)")
            print(f"{'='*60}")
            return 0
        
        if account_users:
            print(f"\n{'='*60}")
            print(f"Рассылка с аккаунта: {account_name}")
            print(f"Пользователей в этом аккаунте: {len(account_users)}")
            print(f"{'='*60}")
            await sender.send_bulk_messages(account_users, messages, users_file, max_successful_users=max_successful_users)
            # Возвращаем количество пользователей в этом аккаунте — общая статистика выше не меняется
            return len(account_users)
        return 0
    
    try:
        # Перед запуском спрашиваем лимит пользователей для КАЖДОГО аккаунта отдельно
        # Считаются только те, кому реально отправилось хотя бы одно сообщение
        limits_per_account = {}
        print(f"\n{'='*60}")
        print("Лимит пользователей для каждого аккаунта")
        print("(считаются только те, кому отправлено хотя бы одно сообщение)")
        print(f"{'='*60}")
        for i, sender_data in enumerate(senders):
            account_name = sender_data['sender'].account_name
            users_count = len(sender_data['users'])
            max_limit = None
            skip_account = False
            try:
                raw_limit = input(
                    f"\nАккаунт {i+1} ({account_name}): сколько пользователей обработать?\n"
                    f"  В файле: {users_count}. Число = лимит, 0 = пропустить аккаунт, пусто = без ограничения: "
                ).strip()
                if raw_limit:
                    limit_value = int(raw_limit)
                    if limit_value > 0:
                        max_limit = limit_value
                        print(f"   → Лимит: {max_limit} пользователей")
                    elif limit_value == 0:
                        skip_account = True
                        print(f"   → Аккаунт будет пропущен (рассылка не начнётся)")
                    else:
                        print(f"   → Без ограничения")
                else:
                    print(f"   → Без ограничения")
            except ValueError:
                print(f"   → Некорректный ввод, без ограничения")
            limits_per_account[i] = {'limit': max_limit, 'skip': skip_account}

        # Запускаем рассылку для всех аккаунтов параллельно
        active_accounts = sum(1 for cfg in limits_per_account.values() if not cfg.get('skip'))
        print(f"\n{'='*60}")
        print(f"Запуск параллельной рассылки для {active_accounts} из {len(senders)} аккаунтов")
        print(f"{'='*60}")
        print(f"   Аккаунтов к рассылке: {active_accounts}/{len(senders)}")

        # Запускаем рассылку для всех аккаунтов одновременно
        for i, sender_data in enumerate(senders):
            account_name = sender_data['sender'].account_name
            users_count = len(sender_data['users'])
            cfg = limits_per_account.get(i, {})
            if cfg.get('skip'):
                limit_str = ", пропуск"
            elif cfg.get('limit'):
                limit_str = f", лимит: {cfg['limit']}"
            else:
                limit_str = ", без ограничения"
            print(f"\n   → Подготовка аккаунта {i+1}: {account_name} ({users_count} пользователей{limit_str})")

        send_tasks = [
            process_account(
                sender_data,
                max_successful_users=limits_per_account.get(i, {}).get('limit'),
                skip=limits_per_account.get(i, {}).get('skip', False),
            )
            for i, sender_data in enumerate(senders)
        ]
        send_results = await asyncio.gather(*send_tasks, return_exceptions=True)

        total_processed = 0
        for i, result in enumerate(send_results):
            account_name = senders[i]['sender'].account_name
            if isinstance(result, Exception):
                print(f"\n✗ Ошибка при обработке аккаунта {account_name}: {str(result)}")
                import traceback
                print("   Детали ошибки:")
                traceback.print_exception(type(result), result, result.__traceback__)
            else:
                total_processed += result
        
        # Общая статистика
        print(f"\n{'='*60}")
        print(f"ОБЩАЯ СТАТИСТИКА ПО ВСЕМ АККАУНТАМ")
        print(f"{'='*60}")
        print(f"Всего аккаунтов использовано: {len(senders)}")
        print(f"Всего пользователей обработано: {total_processed}")
        print(f"{'='*60}")
        
    except KeyboardInterrupt:
        print(f"\n\n{'='*60}")
        print("⚠ РАССЫЛКА ПРЕРВАНА ПОЛЬЗОВАТЕЛЕМ (Ctrl+C)")
        print(f"{'='*60}")
        print("Рассылка остановлена по запросу пользователя")
        print("Все обработанные пользователи уже удалены из файлов")
        print(f"{'='*60}\n")
    except Exception as e:
        print(f"\n✗ Критическая ошибка: {str(e)}")
    finally:
        # Отключаем все аккаунты
        for sender_data in senders:
            await sender_data['sender'].disconnect()


if __name__ == "__main__":
    asyncio.run(main())

