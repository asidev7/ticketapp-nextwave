from .base import (
    FAILED,
    PAID,
    PENDING,
    BasePaymentProvider,
    InvalidSignature,
    PaymentError,
    VerificationResult,
    WebhookEvent,
)
from .fedapay import FedaPayProvider
from .feexpay import FeexPayProvider
from .flutterwave import FlutterwaveProvider
from .paystack import PaystackProvider

__all__ = [
    "FAILED", "PAID", "PENDING", "BasePaymentProvider", "InvalidSignature", "PaymentError",
    "VerificationResult", "WebhookEvent", "PROVIDERS", "get_provider",
]

PROVIDERS: dict[str, type[BasePaymentProvider]] = {
    provider.code: provider
    for provider in (FedaPayProvider, FeexPayProvider, PaystackProvider, FlutterwaveProvider)
}


def get_provider(gateway) -> BasePaymentProvider:
    try:
        return PROVIDERS[gateway.code](gateway)
    except KeyError as exc:
        raise PaymentError(f"No provider for gateway '{gateway.code}'") from exc
