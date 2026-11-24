"""Launch the strandfolio desktop application."""


def main() -> int:
    """Delegate startup to the application and return its exit status."""
    from .app import main as run_application

    return run_application()


if __name__ == "__main__":
    raise SystemExit(main())
