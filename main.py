import os
import re
import json
import time
import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = "8590685237:AAFL9NYTMIPrGgT2dqGRs1BssWUjeA-_jEE"
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
DATA_FILE = Path(os.getenv("DATA_FILE", "products.json"))
PRICE_DROP_PERCENT = float(os.getenv("PRICE_DROP_PERCENT", "3"))

SERVICEABILITY_URL = "https://2.rome.api.flipkart.net/3/product/serviceability"

HEADERS = {
    "content-type": "application/json",
    "user-agent": "okhttp/4.9.2",
    "x-user-agent": "Mozilla/5.0 (Linux; Android 11; Pixel 5) FKUA/Retail/2270300/Android/Mobile",
    "accept-language": "en-IN,en;q=0.9",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

PID_REGEX = re.compile(r"[?&]pid=([A-Za-z0-9]+)")
PID_ENCODED_REGEX = re.compile(r"pid%3D([A-Za-z0-9]+)", re.I)
URL_REGEX = re.compile(r"https?://[^\s\]]+")


def load_data():
    if not DATA_FILE.exists() or DATA_FILE.stat().st_size == 0:
        return {"chats": {}}
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        DATA_FILE.rename(DATA_FILE.with_suffix(".broken.json"))
        return {"chats": {}}


data = load_data()


def save_data():
    DATA_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_chat(chat_id: str):
    data["chats"].setdefault(chat_id, {"pincodes": [], "products": []})
    return data["chats"][chat_id]


def valid_flipkart_url(url: str):
    try:
        return "flipkart.com" in urlparse(url).netloc or "dl.flipkart.com" in urlparse(url).netloc
    except Exception:
        return False


def extract_pid(url: str):
    m = PID_REGEX.search(url) or PID_ENCODED_REGEX.search(url)
    return m.group(1) if m else None


async def get_pid(url: str):
    pid = extract_pid(url)
    if pid:
        return pid

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=HEADERS, allow_redirects=True) as r:
                final_url = str(r.url)
                return extract_pid(final_url)
    except Exception as e:
        logging.warning("PID extract failed: %s", e)

    return None


def build_payload(pid: str, pin: str):
    return {
        "requestContext": {
            "marketplace": "FLIPKART",
            "products": [{"productId": pid}]
        },
        "locationContext": {"pincode": str(pin)}
    }


async def check_serviceability(session, pid: str, pin: str):
    try:
        async with session.post(
            SERVICEABILITY_URL,
            json=build_payload(pid, pin),
            headers=HEADERS
        ) as r:
            if r.status != 200:
                return {"pin": pin, "available": False, "price": None, "title": f"PID: {pid}"}

            js = await r.json(content_type=None)
            product = js.get("RESPONSE", {}).get(pid, {})
            listing = product.get("listingSummary", {})

            available = bool(listing.get("serviceable", False))
            title = product.get("productInfo", {}).get("title") or f"PID: {pid}"

            price = None
            price_data = listing.get("price")
            if isinstance(price_data, dict):
                price = price_data.get("finalPrice") or price_data.get("value") or price_data.get("sellingPrice")

            if isinstance(price, str):
                digits = re.sub(r"[^\d]", "", price)
                price = int(digits) if digits else None

            return {"pin": pin, "available": available, "price": price, "title": title}

    except Exception as e:
        logging.warning("Check failed %s %s: %s", pid, pin, e)
        return {"pin": pin, "available": False, "price": None, "title": f"PID: {pid}"}


async def check_product(pid: str, pincodes: list[str]):
    timeout = aiohttp.ClientTimeout(total=30)
    sem = asyncio.Semaphore(8)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async def worker(pin):
            async with sem:
                return await check_serviceability(session, pid, pin)

        results = await asyncio.gather(*[worker(pin) for pin in pincodes])

    available = [r for r in results if r["available"]]
    prices = [r["price"] for r in results if r.get("price")]
    title = next((r["title"] for r in results if r.get("title")), f"PID: {pid}")

    return {
        "available": bool(available),
        "available_pins": [r["pin"] for r in available],
        "price": min(prices) if prices else None,
        "title": title
    }


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🟢 Flipkart Stock Delivery Alert Bot\n\n"
        "/pins 411001 411002\n"
        "/add <url>\n"
        "/bulkadd <url1> <url2> ...\n"
        "/list\n"
        "/remove <number>\n"
        "/check\n"
        "/interval <seconds>"
    )


async def set_pins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = get_chat(str(update.effective_chat.id))
    pins = [p for p in context.args if re.fullmatch(r"\d{6}", p)]

    if not pins:
        await update.message.reply_text("Usage:\n/pins 411001 560001")
        return

    chat["pincodes"] = sorted(set(pins))
    save_data()
    await update.message.reply_text(f"✅ Saved {len(chat['pincodes'])} pincodes:\n{', '.join(chat['pincodes'])}")


async def add_single_product(chat, url: str):
    url = url.strip()

    if not valid_flipkart_url(url):
        return "invalid"

    pid = await get_pid(url)
    if not pid:
        return "failed"

    if any(p["pid"] == pid for p in chat["products"]):
        return "duplicate"

    chat["products"].append({
        "url": url,
        "pid": pid,
        "title": f"PID: {pid}",
        "price": None,
        "lowest_price": None,
        "in_stock": False,
        "available_pins": [],
        "last_stock_alert": 0,
        "last_price_alert": 0
    })

    return "added"


async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = get_chat(str(update.effective_chat.id))

    if not chat["pincodes"]:
        await update.message.reply_text("First set pincodes:\n/pins 411001 560001")
        return

    if not context.args:
        await update.message.reply_text("Usage:\n/add https://www.flipkart.com/...")
        return

    await update.message.reply_text("Adding product...")

    url = " ".join(context.args).strip()
    result = await add_single_product(chat, url)
    save_data()

    replies = {
        "added": "✅ Product added.",
        "duplicate": "Already tracking this product.",
        "invalid": "Invalid Flipkart URL.",
        "failed": "Could not extract product ID. URL must contain pid=..."
    }

    await update.message.reply_text(replies[result])


async def bulk_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = get_chat(str(update.effective_chat.id))

    if not chat["pincodes"]:
        await update.message.reply_text("First set pincodes:\n/pins 411001 560001")
        return

    urls = URL_REGEX.findall(update.message.text or "")

    if not urls:
        await update.message.reply_text("Usage:\n/bulkadd <url1> <url2>")
        return

    await update.message.reply_text(f"Adding {len(urls)} URLs...")

    added = duplicate = invalid = failed = 0

    for url in urls:
        result = await add_single_product(chat, url)

        if result == "added":
            added += 1
        elif result == "duplicate":
            duplicate += 1
        elif result == "invalid":
            invalid += 1
        else:
            failed += 1

    save_data()

    await update.message.reply_text(
        "✅ Bulk add completed.\n\n"
        f"Added: {added}\n"
        f"Duplicate: {duplicate}\n"
        f"Invalid: {invalid}\n"
        f"Failed: {failed}"
    )


async def list_urls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = get_chat(str(update.effective_chat.id))
    products = chat["products"]

    if not products:
        await update.message.reply_text("No products tracked.")
        return

    msg = f"📍 Pincodes: {', '.join(chat['pincodes'])}\n\n"

    for i, p in enumerate(products, 1):
        stock = "✅ Available" if p.get("in_stock") else "❌ Not available"
        price = f"₹{p['price']}" if p.get("price") else "Unknown"
        pins = ", ".join(p.get("available_pins", [])) or "None"

        msg += (
            f"{i}. {p.get('title', 'Unknown')}\n"
            f"{stock}\n"
            f"Price: {price}\n"
            f"Pins: {pins}\n"
            f"{p['url']}\n\n"
        )

    await update.message.reply_text(msg[:4000])


async def remove_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = get_chat(str(update.effective_chat.id))

    try:
        index = int(context.args[0]) - 1
        removed = chat["products"].pop(index)
        save_data()
        await update.message.reply_text(f"🗑 Removed:\n{removed['title']}")
    except Exception:
        await update.message.reply_text("Usage:\n/remove 1")


async def manual_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔎 Checking...")
    await check_all(context.application)
    await update.message.reply_text("✅ Done.")


async def interval_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CHECK_INTERVAL

    try:
        sec = int(context.args[0])
        if sec < 30:
            await update.message.reply_text("Minimum interval is 30 seconds.")
            return
        CHECK_INTERVAL = sec
        await update.message.reply_text(f"✅ Interval updated to {sec}s")
    except Exception:
        await update.message.reply_text(f"Current interval: {CHECK_INTERVAL}s\nUsage:\n/interval 60")


async def check_all(application):
    for chat_id, chat in data.get("chats", {}).items():
        pincodes = chat.get("pincodes", [])

        if not pincodes:
            continue

        for product in chat.get("products", []):
            result = await check_product(product["pid"], pincodes)

            old_stock = product.get("in_stock")
            old_price = product.get("price")

            product["title"] = result["title"]
            product["price"] = result["price"]
            product["in_stock"] = result["available"]
            product["available_pins"] = result["available_pins"]

            now = time.time()

            if result["price"]:
                if product["lowest_price"] is None or result["price"] < product["lowest_price"]:
                    product["lowest_price"] = result["price"]

            if result["available"] and old_stock is not True:
                product["last_stock_alert"] = now
                await application.bot.send_message(
                    chat_id=int(chat_id),
                    text=(
                        "🚨 PRODUCT AVAILABLE FOR DELIVERY!\n\n"
                        f"{result['title']}\n"
                        f"Price: ₹{result['price'] or 'Unknown'}\n"
                        f"Pincodes: {', '.join(result['available_pins'])}\n\n"
                        f"{product['url']}"
                    )
                )

            if old_price and result["price"] and result["price"] < old_price:
                drop = ((old_price - result["price"]) / old_price) * 100

                if drop >= PRICE_DROP_PERCENT and now - product.get("last_price_alert", 0) > 900:
                    product["last_price_alert"] = now
                    await application.bot.send_message(
                        chat_id=int(chat_id),
                        text=(
                            "📉 PRICE DROP!\n\n"
                            f"{result['title']}\n"
                            f"Old: ₹{old_price}\n"
                            f"New: ₹{result['price']}\n"
                            f"Drop: {drop:.1f}%\n\n"
                            f"{product['url']}"
                        )
                    )

    save_data()


async def tracker_loop(application):
    while True:
        await check_all(application)
        await asyncio.sleep(CHECK_INTERVAL)


async def post_init(application):
    application.create_task(tracker_loop(application))


def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("pins", set_pins))
    app.add_handler(CommandHandler("add", add_url))
    app.add_handler(CommandHandler("bulkadd", bulk_add))
    app.add_handler(CommandHandler("list", list_urls))
    app.add_handler(CommandHandler("remove", remove_url))
    app.add_handler(CommandHandler("check", manual_check))
    app.add_handler(CommandHandler("interval", interval_cmd))

    print("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
