"""Console entry point for a single alignment request over standard streams."""

import sys


def main() -> int:
    """Dispatch only the two operations used by the desktop and Python callers."""
    arguments = sys.argv[1:]
    if arguments == ["pairwise"]:
        from strandfolio.operations.alignment import _writePath

        perform = _writePath
    elif arguments == ["shared-columns"]:
        from strandfolio.operations.multiple_alignment import _writeMatrix

        perform = _writeMatrix
    else:
        print("Choose pairwise or shared-columns.", file=sys.stderr)
        return 2
    try:
        perform()
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        # The parent displays the last stderr line. Keep PyInstaller's unhandled
        # exception footer from replacing the actual engine/input diagnostic.
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
