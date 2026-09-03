ERROR_PREFIX = "QUICK_STUDY_ERROR:"


def format_error(message) -> str:
    text = str(message).strip()
    if text.startswith(ERROR_PREFIX):
        return text
    return f"{ERROR_PREFIX} {text}"
