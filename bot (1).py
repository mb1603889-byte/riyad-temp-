import os
import secrets
import string
import asyncio
import logging
import requests

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
MAILTM_API = "https://api.mail.tm"

# Telegram user_id -> temporary mail account
users = {}

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


def random_username(length=10):
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def random_password(length=18):
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(chars) for _ in range(length))


def get_domain():
    r = requests.get(f"{MAILTM_API}/domains", params={"page": 1}, timeout=20)
    r.raise_for_status()
    domains = r.json().get("hydra:member", [])
    if not domains:
        raise RuntimeError("No active Mail.tm domain found.")
    return domains[0]["domain"]


def create_temp_mail():
    domain = get_domain()
    address = f"{random_username()}@{domain}"
    password = random_password()

    r = requests.post(
        f"{MAILTM_API}/accounts",
        json={"address": address, "password": password},
        timeout=20,
    )
    r.raise_for_status()
    account = r.json()

    r = requests.post(
        f"{MAILTM_API}/token",
        json={"address": address, "password": password},
        timeout=20,
    )
    r.raise_for_status()

    return {
        "id": account["id"],
        "address": address,
        "password": password,
        "token": r.json()["token"],
    }


def get_messages(token):
    r = requests.get(
        f"{MAILTM_API}/messages",
        headers={"Authorization": f"Bearer {token}"},
        params={"page": 1},
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("hydra:member", [])


def get_message(token, message_id):
    r = requests.get(
        f"{MAILTM_API}/messages/{message_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📧 Create Temp Mail", callback_data="create")],
        [
            InlineKeyboardButton("📥 Inbox", callback_data="inbox"),
            InlineKeyboardButton("🔄 Refresh", callback_data="refresh"),
        ],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to Temp Mail Bot!\n\n"
        "Create a temporary email address and receive emails here.",
        reply_markup=main_menu(),
    )


async def create_mail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    await query.edit_message_text("⏳ Creating your temporary email...")

    try:
        account = await asyncio.to_thread(create_temp_mail)
        users[user_id] = account

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Inbox", callback_data="inbox")],
            [InlineKeyboardButton("🔄 New Email", callback_data="create")],
        ])

        await query.edit_message_text(
            "✅ Temporary email created!\n\n"
            f"📧 Email:\n`{account['address']}`\n\n"
            "📥 Press Inbox to check incoming emails.",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    except Exception:
        logging.exception("Create mail error")
        await query.edit_message_text(
            "❌ Could not create a temporary email.\nPlease try again."
        )


async def inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    account = users.get(user_id)

    if not account:
        await query.edit_message_text(
            "❌ You don't have a temporary email yet.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📧 Create Temp Mail", callback_data="create")]
            ]),
        )
        return

    try:
        messages = await asyncio.to_thread(get_messages, account["token"])

        if not messages:
            await query.edit_message_text(
                "📭 Inbox is empty.\n\n"
                f"📧 `{account['address']}`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh", callback_data="refresh")],
                    [InlineKeyboardButton("📧 New Email", callback_data="create")],
                ]),
            )
            return

        keyboard = []
        for msg in messages[:10]:
            subject = msg.get("subject") or "(No subject)"
            if len(subject) > 35:
                subject = subject[:32] + "..."
            keyboard.append([
                InlineKeyboardButton(
                    f"✉️ {subject}",
                    callback_data=f"msg:{msg['id']}",
                )
            ])

        keyboard += [
            [InlineKeyboardButton("🔄 Refresh", callback_data="refresh")],
            [InlineKeyboardButton("📧 New Email", callback_data="create")],
        ]

        await query.edit_message_text(
            f"📥 Inbox\n\n📧 `{account['address']}`\n\n"
            f"Messages: {len(messages)}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    except Exception:
        logging.exception("Inbox error")
        await query.edit_message_text(
            "❌ Could not load inbox.\nPlease press Refresh."
        )


async def show_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    account = users.get(query.from_user.id)
    if not account:
        await query.edit_message_text("❌ Temporary email not found.")
        return

    message_id = query.data.split(":", 1)[1]

    try:
        message = await asyncio.to_thread(
            get_message, account["token"], message_id
        )

        sender = message.get("from", {})
        sender_email = sender.get("address", "Unknown")
        sender_name = sender.get("name", "")
        subject = message.get("subject") or "(No subject)"
        body = (message.get("text") or message.get("intro") or "").strip()

        if len(body) > 3500:
            body = body[:3500] + "\n\n...[truncated]"

        await query.edit_message_text(
            "📨 EMAIL\n\n"
            f"👤 From: {sender_name} <{sender_email}>\n"
            f"📌 Subject: {subject}\n\n"
            f"{body}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Inbox", callback_data="inbox")]
            ]),
        )
    except Exception:
        logging.exception("Message error")
        await query.edit_message_text(
            "❌ Could not open this email.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Inbox", callback_data="inbox")]
            ]),
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "ℹ️ How to use\n\n"
        "1️⃣ Create a temporary email\n"
        "2️⃣ Copy the email address\n"
        "3️⃣ Use it where appropriate\n"
        "4️⃣ Open Inbox to check received emails\n"
        "5️⃣ Tap an email to read it\n\n"
        "⚠️ Temporary emails are not suitable for important "
        "accounts or sensitive information.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Back", callback_data="back")]
        ]),
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data

    if data == "create":
        await create_mail(update, context)
    elif data in ("inbox", "refresh"):
        await inbox(update, context)
    elif data.startswith("msg:"):
        await show_message(update, context)
    elif data == "help":
        await help_command(update, context)
    elif data == "back":
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "🏠 Main Menu",
            reply_markup=main_menu(),
        )


def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
