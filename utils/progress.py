import sys


def emit_step(name: str) -> None:
    """Print a stable step marker the web job parser and progress bar can read."""
    print(f"QUICK_STUDY_STEP: {name}", flush=True, file=sys.stdout)
