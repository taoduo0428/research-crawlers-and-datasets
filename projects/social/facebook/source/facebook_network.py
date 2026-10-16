"""Shared network settings; importing/loading never changes the process environment."""
from dataclasses import dataclass
import re
from urllib.parse import urlsplit


LEGACY_UPSTREAM = "http://127.0.0.1:7897"


@dataclass(frozen=True)
class NetworkConfig:
    mode: str = "direct"
    upstream_url: str = ""

    def __post_init__(self):
        if self.mode not in ("direct", "upstream"):
            raise ValueError("network.mode must be direct or upstream")
        if not isinstance(self.upstream_url, str):
            raise ValueError("network.upstream_url must be a string")
        if self.upstream_url:
            _validate_url(self.upstream_url)
        if self.mode == "upstream" and not self.upstream_url:
            raise ValueError("network.upstream_url is required in upstream mode")

    def environment(self) -> dict[str, str]:
        """Merge these exports into a child environment (including the empty value)."""
        return {"FACEBOOK_NETWORK_MODE": self.mode,
                "FACEBOOK_UPSTREAM_PROXY": self.upstream_url if self.mode == "upstream" else ""}

    @property
    def mitmproxy_mode(self) -> str:
        return "regular" if self.mode == "direct" else "upstream:" + self.upstream_url


def _validate_url(value: str) -> None:
    # Do not interpolate supplied values or chain parser errors: they may contain credentials.
    valid = False
    try:
        u = urlsplit(value)
        valid = (u.scheme in ("http", "https") and bool(u.hostname)
                 and u.username is None and u.password is None
                 and u.path in ("", "/") and not u.query and not u.fragment
                 and not re.search(r'[\s\x00-\x1f\x7f\\"<>?#@]', value)
                 and (u.port is None or 1 <= u.port <= 65535)
                 and not u.netloc.endswith(":"))
    except (ValueError, TypeError):
        pass
    if not valid:
        raise ValueError("network.upstream_url must be an HTTP(S) proxy origin without credentials, query or fragment") from None


def load_network(settings: dict, *, legacy: bool = False) -> NetworkConfig:
    """Read settings['network']; only an absent section can select the legacy default."""
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object")
    if "network" not in settings:
        return NetworkConfig("upstream", LEGACY_UPSTREAM) if legacy else NetworkConfig()
    network = settings["network"]
    if not isinstance(network, dict):
        raise ValueError("network must be an object")
    return NetworkConfig(network.get("mode", "direct"), network.get("upstream_url", ""))
