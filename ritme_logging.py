# RITME Logging Configuration
# Structured logging for pipeline stages
import logging
import sys

# Configure logging format
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

def get_logger(name: str = "ritme") -> logging.Logger:
    "Get a configured logger for a pipeline stage."
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger

# Convenience function that mimics print() with structured logging
def log(stage: str, message: str, level: str = "info"):
    "Log a message with stage prefix."
    logger = get_logger(stage)
    getattr(logger, level)(message)
