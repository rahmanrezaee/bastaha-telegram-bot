import logging
import os
from abc import ABC
from typing import Final
from urllib.parse import quote_plus

_env_logger = logging.getLogger(__name__)


class EnvKeys(ABC):
    """Secure environment configuration with validation"""

    @staticmethod
    def _get_required(key: str) -> str:
        val = os.getenv(key)
        if not val or val.startswith("${{"):
            if os.getenv("PYTEST_CURRENT_TEST"):
                return "test_dummy_value"
            raise ValueError(f"Missing required environment variable: {key}")
        return val

    @staticmethod
    def _get_required_int(key: str, default_for_test: int = 0) -> int:
        val = os.getenv(key)
        if not val or val.startswith("${{"):
            if os.getenv("PYTEST_CURRENT_TEST"):
                return default_for_test
            raise ValueError(f"Missing required environment variable: {key}")
        try:
            return int(val)
        except ValueError:
            if os.getenv("PYTEST_CURRENT_TEST"):
                return default_for_test
            raise

    @staticmethod
    def _get_optional(key: str, default: str = "") -> str:
        val = os.getenv(key)
        if not val or val.startswith("${{"):
            return default
        return val

    @staticmethod
    def _get_int(key: str, default: int) -> int:
        val = os.getenv(key)
        if not val or val.startswith("${{"):
            return default
        try:
            return int(val)
        except ValueError:
            return default

    @staticmethod
    def _get_float(key: str, default: float) -> float:
        val = os.getenv(key)
        if not val or val.startswith("${{"):
            return default
        try:
            return float(val)
        except ValueError:
            return default

    # Telegram
    TOKEN: Final = _get_required('TOKEN')
    OWNER_ID: Final = _get_required_int('OWNER_ID', 999999)

    # Database
    POSTGRES_DB: Final = _get_required("POSTGRES_DB")
    POSTGRES_USER: Final = _get_required("POSTGRES_USER")
    POSTGRES_PASSWORD: Final = _get_required("POSTGRES_PASSWORD")
    DB_PORT: Final = _get_int("DB_PORT", 5432)
    DB_DRIVER: Final = _get_optional("DB_DRIVER", "postgresql+asyncpg")
    POSTGRES_HOST: Final = _get_optional("POSTGRES_HOST", "localhost")

    # Redis
    REDIS_ENABLED: Final = _get_optional("REDIS_ENABLED", "1")
    REDIS_HOST: Final = _get_optional("REDIS_HOST", "localhost")
    REDIS_PORT: Final = _get_int("REDIS_PORT", 6379)
    REDIS_DB: Final = _get_int("REDIS_DB", 0)
    REDIS_PASSWORD: Final = _get_optional("REDIS_PASSWORD", "")

    # Payments
    TELEGRAM_PROVIDER_TOKEN: Final = _get_optional("TELEGRAM_PROVIDER_TOKEN", "")
    CRYPTO_PAY_TOKEN: Final = _get_optional("CRYPTO_PAY_TOKEN", "")
    WALLET_PAY_TOKEN: Final = _get_optional("WALLET_PAY_TOKEN", "")
    BINANCE_PAY_KEY: Final = _get_optional("BINANCE_PAY_KEY", "")
    BINANCE_PAY_SECRET: Final = _get_optional("BINANCE_PAY_SECRET", "")
    STARS_PER_VALUE: Final = _get_float("STARS_PER_VALUE", 0.91)
    REFERRAL_PERCENT: Final = _get_int("REFERRAL_PERCENT", 0)
    PAY_CURRENCY: Final = _get_optional("PAY_CURRENCY", "RUB")
    PAYMENT_TIME: Final = _get_int("PAYMENT_TIME", 1800)
    MIN_AMOUNT: Final = _get_int("MIN_AMOUNT", 20)
    MAX_AMOUNT: Final = _get_int("MAX_AMOUNT", 10000)

    # Links / UI
    CHANNEL_URL: Final = _get_optional("CHANNEL_URL", "")
    CHANNEL_ID: Final = _get_optional("CHANNEL_ID", "")
    HELPER_ID: Final = _get_optional("HELPER_ID", "")
    RULES: Final = _get_optional("RULES", "")

    # Locale & logs
    BOT_LOCALE: Final = _get_optional("BOT_LOCALE", "ru")
    BOT_LOGFILE: Final = _get_optional("BOT_LOGFILE", "logs/bot.log")
    BOT_AUDITFILE: Final = _get_optional("BOT_AUDITFILE", "logs/audit.log")
    LOG_TO_STDOUT: Final = _get_optional("LOG_TO_STDOUT", "1")
    LOG_TO_FILE: Final = _get_optional("LOG_TO_FILE", "1")
    DEBUG: Final = _get_optional("DEBUG", "0")
    REVIEWS_ENABLED: Final = _get_optional("REVIEWS_ENABLED", "1")

    # Web admin panel
    ADMIN_HOST: Final = _get_optional("ADMIN_HOST", _get_optional("MONITORING_HOST", "0.0.0.0"))
    
    @staticmethod
    def _get_admin_port() -> int:
        val = os.getenv("PORT") or os.getenv("ADMIN_PORT") or os.getenv("MONITORING_PORT") or "9090"
        if not val or val.startswith("${{"):
            return 9090
        try:
            return int(val)
        except ValueError:
            return 9090
            
    ADMIN_PORT: Final = _get_admin_port()
    ADMIN_USERNAME: Final = _get_optional("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: Final = _get_optional("ADMIN_PASSWORD", "admin")
    SECRET_KEY: Final = _get_optional("SECRET_KEY", "change-me-in-production")

    # Webhook
    WEBHOOK_ENABLED: Final = _get_optional("WEBHOOK_ENABLED", "0")
    WEBHOOK_URL: Final = _get_optional("WEBHOOK_URL", "")
    WEBHOOK_PATH: Final = _get_optional("WEBHOOK_PATH", "/webhook")
    WEBHOOK_SECRET: Final = _get_optional("WEBHOOK_SECRET", "")

    # Cleanup
    AUDIT_RETENTION_DAYS: Final = _get_int("AUDIT_RETENTION_DAYS", 90)
    PAYMENTS_RETENTION_DAYS: Final = _get_int("PAYMENTS_RETENTION_DAYS", 90)

    DATABASE_URL: Final = f"postgresql+asyncpg://{POSTGRES_USER}:{quote_plus(POSTGRES_PASSWORD)}@{POSTGRES_HOST}:{DB_PORT}/{POSTGRES_DB}"

    # Startup validation
    if ADMIN_PASSWORD == "admin":
        _env_logger.warning(
            "SECURITY: ADMIN_PASSWORD is set to the default value 'admin'. "
            "Change it immediately via the ADMIN_PASSWORD env variable."
        )
    if SECRET_KEY == "change-me-in-production":
        _env_logger.warning(
            "SECURITY: SECRET_KEY is set to the default value. "
            "Set a strong random SECRET_KEY env variable for session security."
        )
    if int(MIN_AMOUNT) >= int(MAX_AMOUNT):
        _env_logger.warning(
            "CONFIG: MIN_AMOUNT (%s) >= MAX_AMOUNT (%s). "
            "Payment amounts will always be rejected.", MIN_AMOUNT, MAX_AMOUNT
        )
    if int(REFERRAL_PERCENT) < 0 or int(REFERRAL_PERCENT) > 99:
        _env_logger.warning(
            "CONFIG: REFERRAL_PERCENT=%s is outside the valid range [0, 99].",
            REFERRAL_PERCENT,
        )
