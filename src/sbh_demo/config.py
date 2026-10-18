from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class DemoConfig:
    port: int = 9747
    site_count: int = 10
    include_man_site: bool = True
    seed: int = 1337
    snmp_call_log_size: int = 2000

    def __post_init__(self) -> None:
        for name, lower, upper in (
            ("port", 1, 65535), ("site_count", 1, 200),
            ("snmp_call_log_size", 1, 10000),
        ):
            if not lower <= getattr(self, name) <= upper:
                raise ValueError(f"{name} must be between {lower} and {upper}")


def load_config() -> DemoConfig:
    """Read environment at startup, rather than at module import time."""
    options = {}
    for name in ("port", "site_count", "seed", "snmp_call_log_size"):
        raw = os.getenv(f"SBH_DEMO_{name.upper()}")
        if raw is not None and raw.strip():
            options[name] = int(raw)
    raw = os.getenv("SBH_DEMO_INCLUDE_MAN_SITE")
    if raw is not None:
        if raw.lower() not in {"true", "false", "1", "0"}:
            raise ValueError("SBH_DEMO_INCLUDE_MAN_SITE must be true, false, 1 or 0")
        options["include_man_site"] = raw.lower() in {"true", "1"}
    return DemoConfig(**options)
