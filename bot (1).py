import os
import logging
from openai import OpenAI
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ParseMode, ChatAction

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPERATOR_ID = 8425559302

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

client = OpenAI(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api/v1")

SYSTEM_PROMPT = """
Ты — ИИ-ассистент сервиса InfoSearch. Ты общаешься с потенциальными клиентами в Telegram.

ТВОЯ РОЛЬ:
Помогаешь клиентам оформить запрос на поиск информации, объясняешь услугу, отвечаешь на вопросы и ведёшь диалог до момента готовности оплатить. Когда клиент готов оплатить — уведомляешь оператора.

О СЕРВИСЕ:
Мы ищем людей и данные о них по открытым базам данных — всё легально, только публичные источники.

ЧТО МЫ ИЩЕМ:
- По ФИО (полное или частичное) → телефон, email, адрес, соцсети
- По ИИН / номеру документа → регистрационные данные, связанные лица
- По номеру телефона → мессенджеры, аккаунты, владелец
- По email → утечки баз, привязанные профили, сервисы
- По никнейму / username → аккаунты на разных платформах
- Перекрёстный поиск по нескольким данным сразу

КАК РАБОТАЕТ ОПЛАТА:
1. Клиент присылает данные для поиска
2. МЫ БЕСПЛАТНО проверяем — есть информация или нет
3. Сообщаем клиенту: что нашли (категории, не детали)
4. ТОЛЬКО ПОСЛЕ этого клиент решает — платить или нет
5. Нет данных = нет оплаты. Это железное правило.

ПРАЙС:
- Предпроверка наличия: БЕСПЛАТНО
- Базовый отчёт: от 1 000 тенге
- Расширенный отчёт: от 3 000 тенге
- Срочный запрос (15-30 мин): +50% к тарифу
Оплата: криптовалюта (USDT/BTC) или банковская карта (Kaspi / перевод)

СРОКИ:
- Предпроверка: 10–30 минут
- Базовый отчёт: до 1 часа
- Расширенный: до 3 часов

ГАРАНТИИ:
- Только открытые и легальные источники
- Конфиденциальность запроса клиента
- Деньги не берём если данных нет
- Честный ответ даже при нулевом результате

ПРАВИЛА ОБЩЕНИЯ:
- Будь дружелюбным, чётким и профессиональным
- Отвечай на русском языке
- Не раскрывай технические детали источников
- Если спрашивают о незаконном (взлом, слежка, шантаж) — вежливо отказывай
- Когда клиент говорит что готов оплатить — напиши ровно эту фразу без изменений: [ГОТОВ_К_ОПЛАТЕ]
- Не придумывай цены, не давай гарантий которых нет в прайсе
- Если не знаешь ответа — скажи что уточнишь у оператора

Веди естественный диалог. Задавай уточняющие вопросы если данных недостаточно для запроса.
""".strip()

user_histories: dict[int, list] = {}
pending_orders: dict[int, dict] = {}

def get_history(user_id: int) -> list:
    if user_id not in user_histories:
        user_histories[user_id] = []
    return user_histories[user_id]

def add_message(user_id: int, role: str, content: str):
    history = get_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > 20:
        user_histories[user_id] = history[-20:]

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔍 Сделать запрос",    callback_data="make_request"),
            InlineKeyboardButton("💰 Прайс",             callback_data="show_price"),
        ],
        [
            InlineKeyboardButton("❓ Как это работает",  callback_data="how_it_works"),
            InlineKeyboardButton("🛡 Гарантии",          callback_data="guarantees"),
        ],
        [
            InlineKeyboardButton("👨💼 Оператор",        callback_data="call_operator"),
        ]
    ])

def payment_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💳 Оплата картой",    callback_data="pay_card"),
            InlineKeyboardButton("₿ Криптовалюта",      callback_data="pay_crypto"),
        ],
        [
            InlineKeyboardButton("🔙 Назад",            callback_data="back_main"),
        ]
    ])

def back_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Главное меню", callback_data="back_main")]
    ])


WELCOME_TEXT = """
👋 *Добро пожаловать в InfoSearch!*

Я помогу найти информацию о человеке или организации по открытым базам данных.

🔍 *Что умею искать:*
• ФИО → контакты, адрес, соцсети
• ИИН → регистрации, связанные лица  
• Телефон → аккаунты, мессенджеры
• Email → профили, утечки баз
• Никнейм → следы в сети

✅ *Главное правило:*
Сначала бесплатная проверка — потом вы решаете платить или нет.
_Нет данных = нет оплаты._

Выбери с чего начать 👇
""".strip()

PRICE_TEXT = """
💰 *Прайс InfoSearch*

━━━━━━━━━━━━━━━━━━━
🆓 *Предпроверка наличия* — БЕСПЛАТНО
_Есть / нет — всегда без оплаты_

📄 *Базовый отчёт* — от 5 000 ₸
_Основные найденные сведения_

📊 *Расширенный отчёт* — от 10 000 ₸
_Перекрёстный анализ, полный профиль_

⚡ *Срочный запрос* — +50% к тарифу
_Приоритет 15–30 минут_

━━━━━━━━━━━━━━━━━━━
💳 *Способы оплаты:*
• Банковская карта (Kaspi / перевод)
• Криптовалюта (USDT TRC-20 / BTC)

⚠️ _Оплата ТОЛЬКО после подтверждения наличия данных_
""".strip()

HOW_IT_WORKS_TEXT = """
📋 *Как работает InfoSearch*

━━━━━━━━━━━━━━━━━━━
*Шаг 1* — Вы пишете запрос
Присылаете любые данные: ФИО, телефон, ИИН, email, никнейм

*Шаг 2* — Бесплатная проверка
Я проверяю базы (10–30 мин). Деньги не нужны.

*Шаг 3* — Анонс результата
Сообщаю что нашёл (категории). Называю точную стоимость.

*Шаг 4* — Ваше решение
Если устраивает — оплачиваете. Нет — ничего не должны.

*Шаг 5* — Полный отчёт
Сразу после оплаты отправляю все найденные данные.

━━━━━━━━━━━━━━━━━━━
⏱ *Сроки:*
• Проверка наличия: 10–30 мин
• Базовый отчёт: до 1 часа
• Расширенный: до 3 часов
• Срочный: 15–30 минут
""".strip()

GUARANTEES_TEXT = """
🛡 *Наши гарантии*

━━━━━━━━━━━━━━━━━━━
🔒 *Законно*
Работаем исключительно с открытыми и публичными источниками

🤝 *Честно*
Нет данных — нет оплаты. Это железное правило без исключений.

🕶 *Конфиденциально*
Ваш запрос не разглашается третьим лицам

⚡ *Быстро*
Ответ о наличии данных в течение 10–30 минут

📋 *Прозрачно*
Перед оплатой вы знаете что именно найдено

━━━━━━━━━━━━━━━━━━━
_Все поиски ведутся в рамках действующего законодательства_
""".strip()

OPERATOR_NOTIFY_TEXT = """
📩 *Оператор уведомлён!*

Живой оператор скоро напишет вам в этот чат.
Обычно это занимает 5–15 минут.

_Пока ждёте — можете описать детали запроса прямо здесь_ 👇
""".strip()

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    user_histories[user_id] = []

    await update.message.reply_text(
        WELCOME_TEXT,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_keyboard()
    )

async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_histories[user_id] = []
    await update.message.reply_text(
        "🔄 Диалог сброшен. Начинаем заново!",
        reply_markup=main_keyboard()
    )

async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data == "make_request":
        add_message(user.id, "user", "Хочу сделать запрос на поиск")
        await query.edit_message_text(
            "🔍 *Отлично! Напишите что нужно найти.*\n\n"
            "Укажите любые данные которые есть:\n"
            "• ФИО (полностью или частично)\n"
            "• Номер телефона\n"
            "• ИИН\n"
            "• Email или никнейм\n\n"
            "_Чем больше данных — тем точнее результат._",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard()
        )

    elif data == "show_price":
        await query.edit_message_text(
            PRICE_TEXT,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard()
        )

    elif data == "how_it_works":
        await query.edit_message_text(
            HOW_IT_WORKS_TEXT,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard()
        )

    elif data == "guarantees":
        await query.edit_message_text(
            GUARANTEES_TEXT,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard()
        )

    elif data == "call_operator":
        await notify_operator(ctx, user, "Клиент запросил живого оператора")
        await query.edit_message_text(
            OPERATOR_NOTIFY_TEXT,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard()
        )

    elif data == "pay_card":
        await query.edit_message_text(
            "💳 *Оплата банковской картой*\n\n"
            "Реквизиты для оплаты:\n"
            "• Kaspi: `+7 123 23 23`\n"
            "• Перевод: уточните у оператора\n\n"
            "После оплаты пришлите скриншот — и отчёт сразу в работу ⚡",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard()
        )

    elif data == "pay_crypto":
        await query.edit_message_text(
            "₿ *Оплата криптовалютой*\n\n"
            "Принимаем:\n"
            "• USDT TRC-20: `1234`\n"
            "• BTC: `1234`\n\n"
            "_После отправки пришлите TxID транзакции — подтверждаем и берём в работу_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_keyboard()
        )

    elif data == "back_main":
        await query.edit_message_text(
            WELCOME_TEXT,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_keyboard()
        )

async def notify_operator(ctx: ContextTypes.DEFAULT_TYPE, user, reason: str, extra: str = ""):
    username = f"@{user.username}" if user.username else f"ID: {user.id}"
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()

    history = get_history(user.id)
    last_msgs = ""
    if history:
        recent = history[-6:]
        lines = []
        for m in recent:
            role = "👤 Клиент" if m["role"] == "user" else "🤖 Бот"
            lines.append(f"{role}: {m['content'][:200]}")
        last_msgs = "\n".join(lines)

    text = (
        f"🔔 *НОВЫЙ КЛИЕНТ ГОТОВ К ОПЛАТЕ*\n\n"
        f"👤 *Имя:* {name}\n"
        f"📱 *Username:* {username}\n"
        f"🆔 *ID:* `{user.id}`\n\n"
        f"📋 *Причина:* {reason}\n"
        f"{extra}\n\n"
        f"💬 *Последние сообщения:*\n{last_msgs or '_нет истории_'}"
    )

    try:
        await ctx.bot.send_message(
            chat_id=OPERATOR_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить оператора: {e}")

async def message_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    text = update.message.text.strip()

    await ctx.bot.send_chat_action(chat_id=user_id, action=ChatAction.TYPING)

    add_message(user_id, "user", text)

    try:
        response = client.chat.completions.create(
            model="openai/gpt-4o-mini",
            max_tokens=800,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + get_history(user_id)
        )
        reply = response.choices[0].message.content
    except Exception as e:
        logger.error(f"Ошибка OpenRouter API: {e}")
        reply = "⚠️ Технический сбой. Попробуйте через минуту или нажмите /start"

    if "[ГОТОВ_К_ОПЛАТЕ]" in reply:
        reply = reply.replace("[ГОТОВ_К_ОПЛАТЕ]", "").strip()
        add_message(user_id, "assistant", reply)

        await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
        await notify_operator(ctx, user, "Клиент готов к оплате")

        await update.message.reply_text(
            "💳 *Отлично! Выбирайте удобный способ оплаты:*\n\n"
            "Параллельно уведомили оператора — он подключится в течение нескольких минут.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=payment_keyboard()
        )
        return

    add_message(user_id, "assistant", reply)

    history_len = len(get_history(user_id))
    markup = main_keyboard() if history_len <= 2 else None

    await update.message.reply_text(
        reply,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=markup
    )

async def cmd_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Оператор пишет: /reply 123456789 Ваш текст"""
    if update.effective_user.id != OPERATOR_ID:
        return

    try:
        parts = update.message.text.split(" ", 2)
        target_id = int(parts[1])
        message = parts[2]
        await ctx.bot.send_message(
            chat_id=target_id,
            text=f"👨💼 *Оператор:*\n{message}",
            parse_mode=ParseMode.MARKDOWN
        )
        await update.message.reply_text("✅ Сообщение отправлено клиенту")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}\nФормат: /reply ID текст")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("reset",  cmd_reset))
    app.add_handler(CommandHandler("reply",  cmd_reply))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("🚀 InfoSearch Bot запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
