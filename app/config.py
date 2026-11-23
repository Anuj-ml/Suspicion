import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # plain env vars still work without python-dotenv


def get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


TELEGRAM_TOKEN = get("SUSPICION_TELEGRAM_TOKEN")
BOT_HANDLE = get("SUSPICION_BOT_HANDLE", "@suspicion_check_bot")
LLM_API_KEY = get("SUSPICION_LLM_API_KEY") or get("GROQ_API_KEY")
LLM_BASE_URL = get("SUSPICION_LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL = get("SUSPICION_LLM_MODEL", "qwen/qwen3.8-27b")
DB_PATH = get("SUSPICION_DB_PATH", "suspicion.db")
HASH_SALT = get("SUSPICION_HASH_SALT", "change-me")
EVIDENCE_TTL_DAYS = 90
# Set automatically on Render. Used only to keep a free instance awake.
PUBLIC_URL = get("RENDER_EXTERNAL_URL") or get("SUSPICION_PUBLIC_URL")
