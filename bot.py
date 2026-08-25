import asyncio
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Optional, Tuple

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ChatPermissions, Message
from aiogram.exceptions import TelegramBadRequest

# ========== КОНФИГ ==========
API_TOKEN = "8900585219:AAGLJYmGLEUGy7oh7ErqpDawuISLVBynXJM"
ADMIN_IDS = [7890361594]  # Ваши Telegram ID
LOG_CHAT_ID = None  # Укажите ID чата для логов, или оставьте None

# === Словари нарушений ===
BAD_WORDS = ["хуй", "пизда", "блядь", "ебал", "залупа", "мудак", "херня", "гандон"]
INSULTS = ["тупой", "глупый", "дебил", "идиот", "кретин", "лох", "даун", "тупица"]
THREATS = ["убью", "зарежу", "прибью", "сломаю", "смерть", "уничтожу"]
VIOLENCE = ["убить", "вешать", "резать", "расправа", "насилие", "сжечь", "взорвать"]
POLITICS = ["путин", "зеленский", "навальный", "война", "фашист", "нацист", "сво", "киев", "москва", "кремль"]
SPAM_WORDS = ["реклама", "скидка", "заработок", "купить", "канал", "подпишись", "перейди", "сайт", "ссылка"]
NSFW_WORDS = ["порно", "секс", "голый", "эротика", "18+", "инцест", "жопа"]

URL_PATTERN = re.compile(r'https?://\S+|www\.\S+')

# ========== БАЗА ДАННЫХ ==========
conn = sqlite3.connect("moderation.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        warn_count INTEGER DEFAULT 0,
        first_warn_time TEXT,
        mute_until TEXT,
        mute_count INTEGER DEFAULT 0
    )
""")
conn.commit()

# ========== ФУНКЦИИ БД ==========
def get_user(user_id: int) -> Optional[Tuple[int, Optional[str], Optional[str], int]]:
    cursor.execute("SELECT warn_count, first_warn_time, mute_until, mute_count FROM users WHERE user_id = ?", (user_id,))
    return cursor.fetchone()

def reset_warns(user_id: int):
    cursor.execute("UPDATE users SET warn_count = 0, first_warn_time = NULL WHERE user_id = ?", (user_id,))
    conn.commit()

def add_warn(user_id: int) -> int:
    now = datetime.now().isoformat()
    user = get_user(user_id)
    if user is None:
        cursor.execute("INSERT INTO users (user_id, warn_count, first_warn_time, mute_count) VALUES (?, 1, ?, 0)", (user_id, now))
        conn.commit()
        return 1
    count, first_time, _, mute_count = user
    if first_time and datetime.fromisoformat(first_time) < datetime.now() - timedelta(hours=24):
        count = 0
        first_time = now
    count += 1
    if count == 1:
        first_time = now
    cursor.execute("UPDATE users SET warn_count = ?, first_warn_time = ? WHERE user_id = ?", (count, first_time, user_id))
    conn.commit()
    return count

def set_mute(user_id: int, seconds: int) -> str:
    until = (datetime.now() + timedelta(seconds=seconds)).isoformat()
    user = get_user(user_id)
    mute_count = user[3] if user else 0
    if seconds >= 86400:
        mute_count += 1
    cursor.execute("UPDATE users SET mute_until = ?, mute_count = ? WHERE user_id = ?", (until, mute_count, user_id))
    conn.commit()
    return until

def is_muted(user_id: int) -> bool:
    user = get_user(user_id)
    if user and user[2]:
        mute_time = datetime.fromisoformat(user[2])
        if mute_time > datetime.now():
            return True
    return False

def get_mute_count(user_id: int) -> int:
    user = get_user(user_id)
    return user[3] if user else 0

# ========== БОТ ==========
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# ========== КОМАНДЫ ==========
@dp.message(Command("варны"))
async def cmd_warns(msg: Message):
    if msg.reply_to_message:
        target = msg.reply_to_message.from_user.id
    else:
        target = msg.from_user.id
    user = get_user(target)
    count = user[0] if user else 0
    await msg.answer(f"👤 Варнов: {count}/3")

@dp.message(Command("снять_варн"))
async def cmd_unwarn(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    if not msg.reply_to_message:
        await msg.answer("Ответьте на сообщение нарушителя.")
        return
    target = msg.reply_to_message.from_user.id
    reset_warns(target)
    await msg.answer(f"✅ Варны сброшены для {msg.reply_to_message.from_user.full_name}")

@dp.message(Command("мут"))
async def cmd_mute(msg: Message):
    if msg.from_user.id not in ADMIN_IDS:
        return
    args = msg.text.split()
    if len(args) < 2 or not msg.reply_to_message:
        await msg.answer("Использование: /мут 30 (минут) в ответ на сообщение.")
        return
    try:
        minutes = int(args[1])
    except ValueError:
        await msg.answer("Укажите число минут.")
        return
    target = msg.reply_to_message.from_user.id
    seconds = minutes * 60
    await bot.restrict_chat_member(
        msg.chat.id,
        target,
        ChatPermissions(can_send_messages=False),
        until_date=datetime.now() + timedelta(seconds=seconds)
    )
    set_mute(target, seconds)
    await msg.answer(f"⛔ {msg.reply_to_message.from_user.full_name} замучен на {minutes} мин.")

# ========== НАКАЗАНИЕ ==========
async def punish(
    msg: Message,
    action: str,
    duration_minutes: Optional[int] = None,
    reason: str = "",
    ban: bool = False
):
    user_id = msg.from_user.id
    if user_id in ADMIN_IDS:
        return
    if is_muted(user_id):
        await msg.delete()
        return

    if action == "mute":
        seconds = duration_minutes * 60
        mute_count = get_mute_count(user_id)
        if mute_count > 0:
            seconds *= 2
            reason += " (повторное нарушение, срок удвоен)"

        await bot.restrict_chat_member(
            msg.chat.id,
            user_id,
            ChatPermissions(can_send_messages=False),
            until_date=datetime.now() + timedelta(seconds=seconds)
        )
        set_mute(user_id, seconds)
        await msg.delete()

        minutes = seconds // 60
        hours = minutes // 60
        if hours >= 24:
            time_str = f"{hours // 24} дн. {hours % 24} ч."
        elif hours >= 1:
            time_str = f"{hours} ч. {minutes % 60} мин."
        else:
            time_str = f"{minutes} мин."

        await msg.answer(
            f"⛔ {msg.from_user.full_name}, вы заблокированы на {time_str}.\n"
            f"📌 Причина: {reason}.\n"
            f"🔄 Для обжалования напишите администратору в личные сообщения.",
            auto_delete=10
        )

        if LOG_CHAT_ID:
            await bot.send_message(
                LOG_CHAT_ID,
                f"🔴 {msg.from_user.full_name} (ID:{user_id}) — мут {time_str}. Причина: {reason}"
            )
        return

    elif action == "warn":
        count = add_warn(user_id)
        await msg.delete()
        if count >= 3:
            await bot.restrict_chat_member(
                msg.chat.id,
                user_id,
                ChatPermissions(can_send_messages=False),
                until_date=datetime.now() + timedelta(hours=24)
            )
            set_mute(user_id, 86400)
            reset_warns(user_id)
            await msg.answer(
                f"⛔ {msg.from_user.full_name}, вы заблокированы на 24 часа.\n"
                f"📌 Причина: 3 варна ({reason}).\n"
                f"🔄 Для обжалования напишите администратору.",
                auto_delete=10
            )
            if LOG_CHAT_ID:
                await bot.send_message(
                    LOG_CHAT_ID,
                    f"🟠 {msg.from_user.full_name} — мут 24ч (3 варна: {reason})"
                )
        else:
            await msg.answer(
                f"⚠️ {msg.from_user.full_name}, предупреждение {count}/3.\n"
                f"📌 Причина: {reason}.\n"
                f"💡 Ещё {3 - count} нарушения — и мут на 24 часа.",
                auto_delete=10
            )
            if LOG_CHAT_ID:
                await bot.send_message(
                    LOG_CHAT_ID,
                    f"⚠️ {msg.from_user.full_name} — варн {count}/3 ({reason})"
                )
        return

    elif action == "ban":
        await msg.delete()
        try:
            await bot.ban_chat_member(msg.chat.id, user_id)
            await msg.answer(
                f"🚫 {msg.from_user.full_name}, вы забанены навсегда.\n"
                f"📌 Причина: {reason}.\n"
                f"❌ Обжалование в личных сообщениях администрации.",
                auto_delete=10
            )
            if LOG_CHAT_ID:
                await bot.send_message(
                    LOG_CHAT_ID,
                    f"⛔ {msg.from_user.full_name} — БАН. Причина: {reason}"
                )
        except TelegramBadRequest:
            await msg.answer("Недостаточно прав для бана.")

# ========== ПРОВЕРКА СООБЩЕНИЙ ==========
@dp.message()
async def handle_message(msg: Message):
    user_id = msg.from_user.id
    if user_id in ADMIN_IDS:
        return
    if is_muted(user_id):
        await msg.delete()
        return

    text = msg.text or msg.caption or ""
    text_lower = text.lower()

    # 1.4 Призывы к насилию
    for word in VIOLENCE:
        if re.search(rf'\b{word}\b', text_lower):
            await punish(msg, "mute", 1440, "призывы к насилию")
            return

    # 1.3 Угрозы
    for word in THREATS:
        if re.search(rf'\b{word}\b', text_lower):
            await punish(msg, "mute", 1440, "угрозы")
            return

    # 1.2 Оскорбления
    for word in INSULTS:
        if re.search(rf'\b{word}\b', text_lower):
            await punish(msg, "mute", 30, "оскорбления")
            return

    # 1.1 Мат
    for word in BAD_WORDS:
        if re.search(rf'\b{word}\b', text_lower):
            await punish(msg, "mute", 5, "мат")
            return

    # 1.5 NSFW
    for word in NSFW_WORDS:
        if word in text_lower:
            await punish(msg, "mute", 60, "NSFW")
            return

    # 1.7 Фишинг/ссылки
    if URL_PATTERN.search(text):
        await punish(msg, "mute", 1440, "подозрительная ссылка", ban=True)
        return

    # 1.6 Реклама
    for word in SPAM_WORDS:
        if word in text_lower:
            await punish(msg, "warn", reason="реклама")
            return

    # 2.3 Политика
    for word in POLITICS:
        if re.search(rf'\b{word}\b', text_lower):
            await punish(msg, "warn", reason="политика")
            return

    # 2.5 Капслок
    upper_words = re.findall(r'[А-ЯA-Z]{4,}', text)
    if len(upper_words) >= 3:
        await punish(msg, "warn", reason="капслок")
        return

    # 2.1 Флуд
    if not hasattr(handle_message, "last_msgs"):
        handle_message.last_msgs = {}
    now = datetime.now()
    if user_id in handle_message.last_msgs:
        last_time = handle_message.last_msgs[user_id]
        if (now - last_time).total_seconds() < 5:
            if not hasattr(handle_message, "flood_counter"):
                handle_message.flood_counter = {}
            counter = handle_message.flood_counter.get(user_id, 0) + 1
            handle_message.flood_counter[user_id] = counter
            if counter >= 3:
                await punish(msg, "warn", reason="флуд")
                handle_message.flood_counter[user_id] = 0
                return
        else:
            handle_message.flood_counter[user_id] = 1
    handle_message.last_msgs[user_id] = now

    # 1.8 Спам (повтор)
    if not hasattr(handle_message, "last_msg_text"):
        handle_message.last_msg_text = {}
    if user_id in handle_message.last_msg_text and handle_message.last_msg_text[user_id] == text:
        await punish(msg, "mute", 10, "спам (повтор)")
        return
    handle_message.last_msg_text[user_id] = text

# ========== ЗАПУСК ==========
async def main():
    print("✅ Бот запущен. Правила активны.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
