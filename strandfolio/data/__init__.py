"""Bundled package resources; importing this package does not initialize the GUI."""

from pathlib import Path


_PACKAGE_DIRECTORY = Path(__file__).resolve().parent
_BUNDLED_DATABASES = _PACKAGE_DIRECTORY / "databases"
HAS_BUNDLED_DATABASES = _BUNDLED_DATABASES.is_dir()
DATABASE_DIRECTORY = _BUNDLED_DATABASES if HAS_BUNDLED_DATABASES else _PACKAGE_DIRECTORY.parents[1] / "databases"
