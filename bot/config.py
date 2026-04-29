import os
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
GROUP_CHAT_ID: int = int(os.environ["GROUP_CHAT_ID"])
ADMIN_IDS: set[int] = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
DB_PATH: str = os.environ.get("DB_PATH", "/data/football.db")
TIMEZONE: ZoneInfo = ZoneInfo(os.environ.get("TIMEZONE", "Europe/Madrid"))
WEBHOOK_URL: str = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_PORT: int = int(os.environ.get("WEBHOOK_PORT", "5000"))
WEBHOOK_PATH: str = os.environ.get("WEBHOOK_PATH", "").strip("/")
