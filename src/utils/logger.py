import logging
from src.config import settings

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# httpx на уровне INFO пишет полный URL запроса, а в нём токен Telegram-бота
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

info = logger.info
error = logger.error
warning = logger.warning
debug = logger.debug
