"""Liepin crawler error taxonomy."""


class LiepinCrawlerError(RuntimeError):
    """Base class for actionable crawler failures."""


class NetworkError(LiepinCrawlerError):
    """A bounded network retry budget was exhausted."""


class AccessDeniedError(LiepinCrawlerError):
    """The site rejected the current session or proxy identity."""


class ChallengeRequiredError(LiepinCrawlerError):
    """A CAPTCHA or human verification page was detected."""


class SchemaChangedError(LiepinCrawlerError):
    """The expected taxonomy or job-card schema was not present."""


class ConfigurationError(LiepinCrawlerError):
    """The supplied crawler configuration is invalid."""
