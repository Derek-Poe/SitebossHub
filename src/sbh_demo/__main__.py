from __future__ import annotations

import os

import uvicorn

from .config import load_config


def main() -> None:
    cfg = load_config()
    host = os.getenv("SBH_DEMO_HOST", "127.0.0.1")
    uvicorn.run(
        "sbh_demo.app:create_app",
        factory=True,
        host=host,
        port=cfg.port,
        log_level=os.getenv("SBH_DEMO_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()

