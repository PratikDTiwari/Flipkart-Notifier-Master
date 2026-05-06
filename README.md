📦 Flipkart Advanced Notifier Bot
A fast ⚡ Telegram bot to track Flipkart products for:


✅ Stock availability (In Stock alerts)


📉 Price drop alerts


📊 Lowest price tracking


👥 Works in personal chat & groups



🚀 Features


⚡ Fast async checking (near real-time)


📦 Track multiple products


📉 Detect price drops automatically


🔔 Telegram instant notifications


👥 Group support (multi-user)


💾 Persistent storage (auto-save products)


⚙️ Adjustable check interval



🛠 Requirements


Python 3.9+


pip



📦 Installation
pip install aiohttp beautifulsoup4 python-dotenv python-telegram-bot

⚙️ Setup
1. Create Telegram Bot


Open Telegram


Search BotFather


Run:


/newbot


Copy your Bot Token



2. Create .env file
In project folder:
TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKENCHECK_INTERVAL=20DATA_FILE=products.jsonPRICE_DROP_PERCENT=3
⚠️ Do NOT use quotes

3. Run bot
py bot_fk.py

🤖 Telegram Commands
Start bot
/start

Add product
/add https://www.flipkart.com/product-url

View tracked products
/list

Remove product
/remove 1

Manual check
/check

Change speed (seconds)
/interval 20

👥 Use in Group


Add bot to group


Disable privacy:


BotFather → Bot Settings → Group Privacy → Turn OFF


Use commands normally:


/add https://www.flipkart.com/...

🔔 Alerts
Stock Alert
✅ IN STOCK!Product NamePrice: ₹49999Link

Price Drop Alert
📉 PRICE DROP!Old: ₹50000New: ₹45000Drop: 10%

⚠️ Notes


Flipkart UI changes can break scraping (selectors updated periodically)


Keep interval ≥ 10 seconds to avoid blocking


Use responsibly (avoid excessive requests)



🔐 Security
If you expose your bot token:
BotFather → /revoke

📄 Disclaimer
This project is for educational purposes only.
Use at your own risk.
