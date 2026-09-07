class ProviderAuthError(Exception):
    """Raised when the stored token/cookie for a provider is missing or rejected (401/403)."""


class ProviderError(Exception):
    """Any other failure while calling a provider (network, unexpected shape, etc.)."""
