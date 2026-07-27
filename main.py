import os
import json
import asyncio
import re
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup,
    InlineKeyboardButton, ChatMemberUpdated
)
from aiogram.filters import CommandStart
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest

# Получаем токен
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("Токен не найден! Укажите BOT_TOKEN в переменных окружения на хостинге.")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ==========================================
# 0. БАЗА ДАННЫХ (СОХРАНЕНИЕ В ФАЙЛ JSON)
# ==========================================
DATA_FILE = "bot_data.json"


def load_db():
    """Загружает данные из файла при запуске бота."""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


# Глобальная переменная с базой данных
db = load_db()


def save_db():
    """Сохраняет текущие данные в файл."""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=4)


def get_chat_data(chat_id: int) -> dict:
    """Получает данные чата, создавая дефолтные, если их нет."""
    chat_id_str = str(chat_id)  # JSON хранит ключи только как строки
    if chat_id_str not in db:
        db[chat_id_str] = {"title": "Неизвестный чат", "sticker_check": False}
        save_db()
    return db[chat_id_str]


def get_chat_setting(chat_id: int, setting: str) -> bool:
    """Узнает статус настройки (по умолчанию отключена)."""
    return get_chat_data(chat_id).get(setting, False)


def toggle_chat_setting(chat_id: int, setting: str) -> bool:
    """Переключает настройку и сохраняет в файл."""
    chat_data = get_chat_data(chat_id)
    chat_data[setting] = not chat_data.get(setting, False)
    save_db()
    return chat_data[setting]


def update_known_chats(chat_id: int, title: str):
    """Обновляет название чата в базе и сохраняет."""
    if chat_id < 0:
        chat_data = get_chat_data(chat_id)
        if chat_data.get("title") != title:
            chat_data["title"] = title
            save_db()


# ==========================================
# 1. ОТСЛЕЖИВАНИЕ ДОБАВЛЕНИЯ В ЧАТЫ
# ==========================================
@dp.my_chat_member()
async def track_bot_chats(event: ChatMemberUpdated):
    if event.new_chat_member.status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR):
        update_known_chats(event.chat.id, event.chat.title)
    elif event.new_chat_member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        # Если бота удалили из группы, стираем ее из базы
        db.pop(str(event.chat.id), None)
        save_db()


# ==========================================
# 2. ПАНЕЛЬ УПРАВЛЕНИЯ В ЛС
# ==========================================
async def generate_chats_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = []

    # Проходимся по сохраненной базе данных
    for chat_id_str, chat_info in list(db.items()):
        chat_id = int(chat_id_str)
        title = chat_info.get("title", "Неизвестный чат")
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            if member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
                buttons.append([InlineKeyboardButton(text=f"📁 {title}", callback_data=f"manage_chat_{chat_id}")])
        except TelegramBadRequest:
            continue

    if not buttons:
        return None

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(CommandStart(), F.chat.type == "private")
async def pm_start(message: Message):
    keyboard = await generate_chats_keyboard(message.from_user.id)

    if keyboard:
        await message.answer("Выберите чат для управления настройками:", reply_markup=keyboard)
    else:
        await message.answer(
            "Я пока не знаю ни одной группы, где вы были бы администратором.\n\n"
            "Добавьте меня в группу, выдайте права администратора, "
            "а затем снова введите /start здесь.\n\n"
            "Бот проверяет сообщения на наличие запрещенных слов удаляя эти сообщение. "
            "За простое упоминание этих слов в чате, телеграм фильтры могут его заблокировать"
            "а бот помогает удалять эти опасные сообщения"
        )


@dp.callback_query(F.data == "back_to_chats")
async def back_to_chats_handler(callback: CallbackQuery):
    keyboard = await generate_chats_keyboard(callback.from_user.id)
    if keyboard:
        await callback.message.edit_text("Выберите чат для управления настройками:", reply_markup=keyboard)
    else:
        await callback.message.edit_text("Доступных чатов не найдено.")
    await callback.answer()


@dp.callback_query(F.data.startswith("manage_chat_"))
async def open_chat_settings(callback: CallbackQuery):
    chat_id = int(callback.data.replace("manage_chat_", ""))
    await send_settings_menu(callback.message, chat_id, callback.from_user.id, edit_message=True)
    await callback.answer()


async def send_settings_menu(message_obj: Message, chat_id: int, user_id: int, edit_message: bool = False):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR):
            if edit_message:
                await message_obj.edit_text("❌ У вас больше нет прав администратора в этом чате.")
            return
    except TelegramBadRequest:
        return

    is_enabled = get_chat_setting(chat_id, "sticker_check")
    status_text = "ВКЛЮЧЕНА ✅" if is_enabled else "ОТКЛЮЧЕНА ❌"

    chat_data = get_chat_data(chat_id)
    chat_title = chat_data.get("title", "Неизвестный чат")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Стикеры: {status_text}", callback_data=f"toggle_sticker_{chat_id}")],
        [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="back_to_chats")]
    ])

    text = f"🎛 **Настройки чата:** {chat_title}\n\nвы можете включить проверку стикеров, если пак сожержит рекламу, то стикер будет удален:"

    if edit_message:
        await message_obj.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await message_obj.answer(text, reply_markup=keyboard, parse_mode="Markdown")


@dp.callback_query(F.data.startswith("toggle_sticker_"))
async def toggle_callback(callback: CallbackQuery):
    chat_id = int(callback.data.replace("toggle_sticker_", ""))
    new_status = toggle_chat_setting(chat_id, "sticker_check")
    await send_settings_menu(callback.message, chat_id, callback.from_user.id, edit_message=True)
    await callback.answer(f"Проверка стикеров {'включена' if new_status else 'отключена'}.")


# ==========================================
# 3. МОДЕРАЦИЯ И ЛОГИКА ПРОВЕРКИ
# ==========================================
CHAR_MAP = {
    'a': 'а', '@': 'а', '4': 'а', 'b': 'б', '6': 'б',
    'c': 'с', 'k': 'к', 'e': 'е', '3': 'з', 'h': 'н', 'n': 'н',
    'o': 'о', '0': 'о', 'p': 'р', 't': 'т', 'x': 'х',
    'y': 'у', 'i': 'и', '1': 'и', '!': 'и', 'm': 'м'
}
FORBIDDEN_ROOTS = ['наркоти', 'мефедрон', 'кокаин', 'героин', 'гашиш', 'экстази', 'наркота', 'нарката']


def normalize_text(text: str) -> str:
    if not text: return ""
    normalized = "".join(CHAR_MAP.get(char, char) for char in text.lower())
    normalized = re.sub(r'[^а-яё]', '', normalized)
    return re.sub(r'(.)\1+', r'\1', normalized)


def contains_forbidden(text: str) -> bool:
    norm_text = normalize_text(text)
    for root in FORBIDDEN_ROOTS:
        if root in norm_text or re.sub(r'(.)\1+', r'\1', root) in norm_text:
            return True
    return False


WHITELIST = ["@kfgart", "@FurriStik", "t.me/kfgart", "@fyrporn", "@RA_FAIER"]
LINK_PATTERN = re.compile(r'(?:@|t\.me/)([a-zA-Z0-9_]{4,})', re.IGNORECASE)
checked_packs_cache = {}


@dp.message(F.sticker, F.chat.type.in_({"group", "supergroup"}))
async def check_sticker(message: Message):
    update_known_chats(message.chat.id, message.chat.title)

    if not get_chat_setting(message.chat.id, "sticker_check"):
        return
    if not message.sticker.set_name:
        return

    set_name = message.sticker.set_name
    has_ad = checked_packs_cache.get(set_name)

    if has_ad is None:
        try:
            sticker_set = await bot.get_sticker_set(set_name)
            full_pack_name = f"{sticker_set.title} {sticker_set.name}".lower()
            is_whitelisted = any(wl.lower() in full_pack_name for wl in WHITELIST)

            has_ad = False if is_whitelisted else bool(LINK_PATTERN.search(full_pack_name))

            checked_packs_cache[set_name] = has_ad
            if len(checked_packs_cache) > 5000:
                checked_packs_cache.clear()
        except Exception:
            return

    if has_ad:
        try:
            await message.delete()
            warning = await message.answer(f"⚠️ {message.from_user.first_name}, этот стикер содержит рекламу!")
            await asyncio.sleep(5)
            await warning.delete()
        except TelegramBadRequest:
            pass


@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def moderate_message(message: Message):
    update_known_chats(message.chat.id, message.chat.title)

    text_to_check = message.text or message.caption or ""
    if contains_forbidden(text_to_check):
        try:
            await message.delete()
            warning = await message.answer(
                f"⚠️ {message.from_user.first_name}, сообщение содержит запрещенные слова!")
            await asyncio.sleep(5)
            await warning.delete()
        except Exception:
            pass


async def main():
    logging.basicConfig(level=logging.INFO)
    print("Бот запущен. Ожидание обновлений...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
