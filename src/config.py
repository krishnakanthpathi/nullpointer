import os
import logging
from dotenv import load_dotenv

# Set up logging format
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)-8s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("nullpointer-bot")

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
OLLAMA_HOST = os.getenv('OLLAMA_HOST', 'http://localhost:11434')
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'qwen2.5-coder:7b')
TTS_URL = os.getenv('TTS_URL', 'http://localhost:8998/tts')

LLM_PROVIDER = os.getenv('LLM_PROVIDER', 'gemini').lower()
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash-lite')
OPENAI_MODEL = os.getenv('OPENAI_MODEL', 'gpt-4o-mini')

MAX_HISTORY = 16  # keeps the last 8 turns of conversation

if not DISCORD_TOKEN:
    logger.error("DISCORD_TOKEN is not set in the environment or .env file.")
