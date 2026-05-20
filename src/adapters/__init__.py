"""Raw data adapters for external course systems."""

from .base_adapter import AdapterConfig, AdapterError, RawPayload
from .jwch_adapter import JwchAdapter
from .learn_adapter import LearnAdapter
from .mail_adapter import MailAdapter

__all__ = [
    "AdapterConfig",
    "AdapterError",
    "RawPayload",
    "JwchAdapter",
    "LearnAdapter",
    "MailAdapter",
]
