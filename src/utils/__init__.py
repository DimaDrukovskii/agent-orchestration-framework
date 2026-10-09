from . import logger


def mask_phone(phone: str) -> str:
    """+79990000000 -> +7999***0000, для логов и уведомлений (PII)."""
    if not phone or len(phone) <= 7:
        return "***"
    return f"{phone[:5]}***{phone[-4:]}"


__all__ = ["logger", "mask_phone"]
