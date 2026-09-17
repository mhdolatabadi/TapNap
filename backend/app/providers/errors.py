class ProviderAuthError(Exception):
    """Raised when the stored token/cookie for a provider is missing or rejected (401/403)."""


class ProviderError(Exception):
    """Any other failure while calling a provider (network, unexpected shape, etc.)."""


class ProviderRateLimited(ProviderError):
    """The provider itself said "quota/rate exceeded" (HTTP 429, or Neshan's
    481) -- distinct from ProviderError because the caller can act on it
    specifically: back off for a while instead of retrying every cycle and
    burning more of an already-exhausted quota."""
