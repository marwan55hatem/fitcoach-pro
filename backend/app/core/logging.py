import logging


def configure_logging(level: int = logging.INFO) -> None:
    """Configure app logging. Never log image bytes or personal health data."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
