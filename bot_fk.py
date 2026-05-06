import os
import re
import json
import time
import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

load_dotenv()

BOT_TOKEN = "8590685237:AAFL9NYTMIPrGgT2dqGRs1BssWUjeA-_jEE"
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "20"))
DATA_FILE = Path(os.getenv("DATA_FILE", "products.json"))
PRICE_DROP_PERCENT = float(os.getenv("PRICE_DROP_PERCENT", "3"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
}


def load_data():
    if not DATA_FILE.exists() or DATA_FILE.stat().st_size == 0:
        return {"chats": {}}

    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        DATA_FILE.rename(DATA_FILE.with_suffix(".broken.json"))
        return {"chats": {}}


def save_data():
    DATA_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


data = load_data()


def valid_flipkart_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return "flipkart.com" in parsed.netloc
    except Exception:
        return False


def get_chat_products(chat_id: str):
    data["chats"].setdefault(chat_id, {"products": []})
    return data["chats"][chat_id]["products"]


def extract_title(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(" ", strip=True)

    title = soup.find("span", class_=re.compile(r"B_NuCI|VU-ZEz|_35KyD6"))
    if title:
        return title.get_text(" ", strip=True)

    page_title = soup.find("title")
    return page_title.get_text(" ", strip=True) if page_title else "Unknown product"


def extract_price(soup: BeautifulSoup):
    selectors = [
        "._30jeq3",
        ".Nx9bqj",
        "._16Jk6d",
        "._1_WHN1",
    ]

    for selector in selectors:
        tag = soup.select_one(selector)
        if tag:
            text = tag.get_text(strip=True)
            digits = re.sub(r"[^\d]", "", text)
            if digits:
                return int(digits)

    text = soup.get_text(" ", strip=True)
    match = re.search(r"₹\s?([\d,]+)", text)
    if match:
        return int(match.group(1).replace(",", ""))

    return None


def detect_stock(soup: BeautifulSoup) -> bool:
    text = soup.get_text(" ", strip=True).lower()

    out_words = [
        "currently out of stock",
        "sold out",
        "notify me",
        "coming soon",
        "not available",
        "temporarily unavailable"
    ]

    in_words = [
        "add to cart",
        "buy now"
    ]

    if any(word in text for word in out_words):
        return False

    if any(word in text for word in in_words):
        return True

    return False


async def fetch_product(session: aiohttp.ClientSession, url: str):
    async with session.get(url, headers=HEADERS, timeout=25) as resp:
        html = await resp.text(errors="ignore")

    soup = BeautifulSoup(html, "html.parser")

    return {
        "title": extract_title(soup),
        "price": extract_price(soup),
        "in_stock": detect_stock(soup)
    }


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🟢 Flipkart tracker is running.\n\n"
        "Commands:\n"
        "/add <url>\n"
        "/list\n"
        "/remove <number>\n"
        "/check\n"
        "/interval <seconds>\n\n"
        "Example:\n"
        "/add https://www.flipkart.com/..."
    )


async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)

    if not context.args:
        await update.message.reply_text("Usage:\n/add https://www.flipkart.com/...")
        return

    url = " ".join(context.args).strip()

    if not valid_flipkart_url(url):
        await update.message.reply_text("Send a valid Flipkart product URL.")
        return

    products = get_chat_products(chat_id)

    if any(p["url"] == url for p in products):
        await update.message.reply_text("Already tracking this product.")
        return

    products.append({
        "url": url,
        "title": "Checking...",
        "price": None,
        "lowest_price": None,
        "in_stock": None,
        "last_stock_alert": 0,
        "last_price_alert": 0
    })

    save_data()

    await update.message.reply_text(
        "✅ Added.\n"
        "Tracking stock and price drops for this chat/group."
    )


async def list_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    products = get_chat_products(chat_id)

    if not products:
        await update.message.reply_text("No products tracked yet.")
        return

    msg = "📦 Tracked products:\n\n"

    for i, p in enumerate(products, start=1):
        stock = "✅ In stock" if p.get("in_stock") else "❌ Out/unknown"
        price = f"₹{p['price']}" if p.get("price") else "Unknown"
        low = f"₹{p['lowest_price']}" if p.get("lowest_price") else "Unknown"

        msg += (
            f"{i}. {p.get('title', 'Unknown')}\n"
            f"{stock}\n"
            f"Price: {price}\n"
            f"Lowest: {low}\n"
            f"{p['url']}\n\n"
        )

    await update.message.reply_text(msg[:4000])


async def remove_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    products = get_chat_products(chat_id)

    if not context.args:
        await update.message.reply_text("Usage:\n/remove 1")
        return

    try:
        index = int(context.args[0]) - 1
        removed = products.pop(index)
        save_data()
        await update.message.reply_text(f"🗑 Removed:\n{removed['title']}")
    except Exception:
        await update.message.reply_text("Invalid number. Use /list first.")


async def manual_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Checking now...")
    await check_all(context.application)
    await update.message.reply_text("Done.")


async def interval_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHECK_INTERVAL

    if not context.args:
        await update.message.reply_text(f"Current interval: {CHECK_INTERVAL} seconds")
        return

    try:
        sec = int(context.args[0])
        if sec < 10:
            await update.message.reply_text("Minimum interval is 10 seconds.")
            return

        CHECK_INTERVAL = sec
        await update.message.reply_text(f"Interval changed to {CHECK_INTERVAL} seconds.")
    except Exception:
        await update.message.reply_text("Usage:\n/interval 20")


async def check_all(application):
    async with aiohttp.ClientSession() as session:
        for chat_id, chat_data in data.get("chats", {}).items():
            for product in chat_data.get("products", []):
                try:
                    result = await fetch_product(session, product["url"])

                    old_stock = product.get("in_stock")
                    old_price = product.get("price")

                    product["title"] = result["title"]
                    product["in_stock"] = result["in_stock"]
                    product["price"] = result["price"]

                    now = time.time()

                    if product["lowest_price"] is None and result["price"]:
                        product["lowest_price"] = result["price"]

                    if result["price"] and product["lowest_price"]:
                        if result["price"] < product["lowest_price"]:
                            product["lowest_price"] = result["price"]

                    if result["in_stock"] is True and old_stock is not True:
                        product["last_stock_alert"] = now
                        await application.bot.send_message(
                            chat_id=int(chat_id),
                            text=(
                                f"✅ IN STOCK!\n\n"
                                f"{result['title']}\n"
                                f"Price: ₹{result['price'] if result['price'] else 'Unknown'}\n\n"
                                f"{product['url']}"
                            )
                        )

                    if (
                        old_price
                        and result["price"]
                        and result["price"] < old_price
                    ):
                        drop_percent = ((old_price - result["price"]) / old_price) * 100

                        if drop_percent >= PRICE_DROP_PERCENT:
                            if now - product.get("last_price_alert", 0) > 900:
                                product["last_price_alert"] = now
                                await application.bot.send_message(
                                    chat_id=int(chat_id),
                                    text=(
                                        f"📉 PRICE DROP!\n\n"
                                        f"{result['title']}\n"
                                        f"Old: ₹{old_price}\n"
                                        f"New: ₹{result['price']}\n"
                                        f"Drop: {drop_percent:.1f}%\n\n"
                                        f"{product['url']}"
                                    )
                                )

                    logging.info(
                        "%s | Stock=%s | Price=%s",
                        result["title"],
                        result["in_stock"],
                        result["price"]
                    )

                except Exception as e:
                    logging.warning("Error checking %s: %s", product["url"], e)

    save_data()


async def tracker_loop(application):
    while True:
        await check_all(application)
        await asyncio.sleep(CHECK_INTERVAL)


async def post_init(application):
    application.create_task(tracker_loop(application))


def main():
    if not BOT_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN missing in .env")

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler(["add", "Add"], add_url))
    app.add_handler(CommandHandler("list", list_urls))
    app.add_handler(CommandHandler("remove", remove_url))
    app.add_handler(CommandHandler("check", manual_check))
    app.add_handler(CommandHandler("interval", interval_cmd))

    app.run_polling()


if __name__ == "__main__":
    main()