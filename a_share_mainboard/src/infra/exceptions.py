class AppError(Exception):
    """Base application error."""


class ConfigError(AppError):
    """Configuration loading error."""


class ProviderUnavailableError(AppError):
    """Raised when a market or LLM provider cannot be used."""


class AIResponseError(AppError):
    """Raised when the LLM response cannot be parsed."""

