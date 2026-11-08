"""Keep server tracebacks and HTTP client URLs out of local support logs."""
import logging


class ServerErrorCategory(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.exc_info or "\n" in record.getMessage():
            category = record.exc_info[0].__name__ if record.exc_info else "startup"
            record.msg, record.args = "Backend server error (%s)", (category,)
            record.exc_info = record.exc_text = None
        return True


def configure_logging() -> None:
    logging.getLogger("uvicorn.error").addFilter(ServerErrorCategory())
    # These clients log request URLs at INFO and headers at DEBUG.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
