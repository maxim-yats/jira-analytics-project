"""Executable entry point for the Jira analytics application."""

if __package__:
    from .utils.cli import main
else:  # Support direct execution: python libs/main.py
    from utils.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
