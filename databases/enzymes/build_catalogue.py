"""Build a versioned SQLite catalogue from the installed Biopython enzyme snapshot.

This development tool reads package data locally; it does not download REBASE,
simulate digestion, or modify the Biopython installation. Existing outputs are
never overwritten. The database preserves source attribution and license text.
"""

import argparse
from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import tempfile

from strandfolio.storage import build_restriction_catalogue
from strandfolio.storage.build_restriction_catalogue import (
    buildDatabase, installedSourceFiles, validateDatabase as _validate,
)


def _validationReport(connection: sqlite3.Connection) -> dict:
    """Check the catalogue against the installed source and independent cut references."""
    reconciled = _validate(connection)
    references = {
        "EcoRI": ("GAATTC", [(1, 5)], "r0101-ecori"),
        "SmaI": ("CCCGGG", [(3, 3)], "r0141-smai"),
        "KpnI": ("GGTACC", [(5, 1)], "r0142-kpni"),
        "BsaI": ("GGTCTC", [(7, 11)], "r0535-bsai"),
        "BcgI": ("CGANNNNNNTGC", [(-10, -12), (24, 22)], "r0545-bcgi"),
    }
    knownAnswers = []
    for name, (motif, expected, product) in references.items():
        actual = connection.execute(
            "SELECT top_offset, bottom_offset FROM cleavage_pairs WHERE enzyme_name = ? "
            "ORDER BY pair_number", (name,),
        ).fetchall()
        storedMotif = connection.execute(
            "SELECT recognition_site FROM enzymes WHERE name = ?", (name,),
        ).fetchone()
        if actual != expected or storedMotif != (motif,):
            raise ValueError(f"Independent motif/cut reference check failed for {name}")
        knownAnswers.append({
            "enzyme": name, "recognition_site": motif, "expected_pairs": expected,
            "actual_pairs": actual, "source_url": f"https://www.neb.com/products/{product}",
            "passed": True,
        })
    queries = {
        "enzymes": "SELECT COUNT(*) FROM enzymes",
        "known_cleavage": "SELECT COUNT(*) FROM enzymes WHERE cleavage_known = 1",
        "unknown_cleavage": "SELECT COUNT(*) FROM enzymes WHERE cleavage_known = 0",
        "cleavage_pairs": "SELECT COUNT(*) FROM cleavage_pairs",
        "multi_cut_enzymes": "SELECT COUNT(*) FROM cleavage_pairs WHERE pair_number = 2",
        "commercial_enzymes": "SELECT COUNT(*) FROM enzymes WHERE commercial_in_snapshot = 1",
        "flagged_enzymes": "SELECT COUNT(DISTINCT enzyme_name) FROM data_issues",
    }
    issues = [dict(zip(("enzyme", "code", "detail"), row)) for row in connection.execute(
        "SELECT enzyme_name, code, detail FROM data_issues ORDER BY enzyme_name, code",
    )]
    return {
        "validation_version": 1, "verdict": "passed", "integrity_check": "ok",
        "foreign_key_violations": 0, "source_reconciliation": reconciled,
        "counts": {key: connection.execute(query).fetchone()[0] for key, query in queries.items()},
        "known_answers": knownAnswers, "retained_issues": issues,
        "limitations": [
            "Coverage is the complete installed source set, not a current authoritative REBASE export.",
            "Catalogue offsets are normalized from installed Biopython source; the five "
            "known-answer checks use official NEB notation in the documented interbase convention.",
            "Known geometry is not reaction/simulation support; BcgI requires multiple sites.",
            "Package thermal defaults and matching regexes are not imported as verified biology.",
        ],
    }


def _fileInfo(path: Path) -> dict:
    raw = path.read_bytes()
    return {"sha256": hashlib.sha256(raw).hexdigest(), "byte_count": len(raw)}


def _writeJson(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _prepareSnapshot(snapshot: Path, source: Path, snapshotTarget: Path, sourceTarget: Path) -> None:
    """Prepare all validated files in private staging directories before publication."""
    for name, installed in installedSourceFiles().items():
        (source / name).write_bytes(installed.read_bytes())
    database = snapshot / "restriction_enzymes.sqlite3"
    buildDatabase(database)
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        validation = _validationReport(connection)
    finally:
        connection.close()
    files = {name: _fileInfo(source / name) for name in installedSourceFiles()}
    if files["Restriction_Dictionary.py"]["sha256"] != metadata["source_dictionary_sha256"]:
        raise ValueError("Installed dictionary changed during source capture")
    if (source / "LICENSE").read_text() != metadata["biopython_license"]:
        raise ValueError("Installed license changed during source capture")
    for name, installed in installedSourceFiles().items():
        if installed.read_bytes() != (source / name).read_bytes():
            raise ValueError(f"Installed source changed during build: {name}")
    _writeJson(source / "manifest.json", {
        "manifest_version": 1, "capture_kind": "installed_biopython",
        "biopython_version": metadata["biopython_version"], "files": files,
        "source_release_description": metadata["source_release_description"],
        "captured_utc": metadata["created_utc"],
        "redistribution_status": metadata["redistribution_status"],
        "limitations": ["Captured installed package files; not a fresh official REBASE download."],
    })
    _writeJson(snapshot / "validation.json", validation)
    _writeJson(snapshot / "manifest.json", {
        "manifest_version": 1, "snapshot_id": snapshotTarget.name,
        "created_utc": metadata["created_utc"], "coverage": metadata["coverage"],
        "redistribution_status": metadata["redistribution_status"],
        "database": {"filename": database.name, "schema_version": 1, **_fileInfo(database)},
        "source": {
            "capture_kind": "installed_biopython",
            "directory": Path(os.path.relpath(sourceTarget, snapshotTarget)).as_posix(),
            "manifest_sha256": _fileInfo(source / "manifest.json")["sha256"],
            **{key: metadata[key] for key in (
                "biopython_version", "source_release_description", "source_dictionary_sha256",
            )},
        },
        "builder": {"path": "databases/enzymes/build_catalogue.py",
                    "sha256": _fileInfo(Path(__file__))["sha256"],
                    "runtime_module": {"path": "strandfolio/storage/build_restriction_catalogue.py",
                                       **_fileInfo(Path(build_restriction_catalogue.__file__))},
                    "python_version": platform.python_version()},
        "validation": {"filename": "validation.json", "verdict": "passed",
                       "sha256": _fileInfo(snapshot / "validation.json")["sha256"]},
    })


def _publishDirectory(
    staged: Path, destination: Path,
    createdFiles: list[tuple[Path, os.stat_result]],
    createdDirs: list[tuple[Path, os.stat_result]],
) -> None:
    """Reserve a fresh destination and publish its manifest last as the completion marker."""
    destination.mkdir()
    createdDirs.append((destination, destination.lstat()))
    for path in sorted(staged.iterdir(), key=lambda filePath: (
        filePath.name == "manifest.json", filePath.name,
    )):
        target = destination / path.name
        identity = path.lstat()
        target.hardlink_to(path)
        createdFiles.append((target, identity))


def _rollbackPaths(
    created: list[tuple[Path, os.stat_result]], remove: Callable[[Path], None], error: OSError,
) -> None:
    for path, identity in reversed(created):
        try:
            if os.path.samestat(path.lstat(), identity):
                remove(path)
            else:
                error.add_note(f"Rollback preserved a replacement at {path}.")
        except FileNotFoundError:
            continue
        except OSError as cleanupError:
            detail = cleanupError.strerror or str(cleanupError)
            error.add_note(f"Rollback could not remove {path} ({detail}).")


def buildSnapshot(snapshotDir: Path, *, sourceDir: Path) -> Path:
    """Build and validate a catalogue and save a copy of its source in two new directories.

    Args:
        snapshotDir: Fresh destination for SQLite, manifest and validation report.
        sourceDir: Fresh non-nested destination for installed package source/attribution.

    Returns:
        The published SQLite path. No network acquisition or GUI activation is performed.

    Raises:
        FileExistsError: Either destination already exists, including an empty directory/link.
        ValueError: Destinations overlap or validation/source preservation fails.
        OSError: Publication fails. Rollback issues are attached to the publication error.

    Prior snapshots are never replaced. Validation precedes publication and manifests
    are published last. Abrupt process/OS termination can leave an incomplete directory;
    a directory without its manifest is not a completed snapshot.
    """
    snapshot, source = Path(snapshotDir), Path(sourceDir)
    for target in (snapshot, source):
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Keep existing material and choose a new directory: {target}")
    snapshot, source = snapshot.resolve(), source.resolve()
    if snapshot == source or snapshot in source.parents or source in snapshot.parents:
        raise ValueError("Snapshot and source directories must be distinct and non-nested")
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    source.parent.mkdir(parents=True, exist_ok=True)
    createdFiles, createdDirs = [], []
    with (
        tempfile.TemporaryDirectory(prefix=".enzyme-snapshot-", dir=snapshot.parent) as stageDb,
        tempfile.TemporaryDirectory(prefix=".enzyme-source-", dir=source.parent) as stageSource,
    ):
        _prepareSnapshot(Path(stageDb), Path(stageSource), snapshot, source)
        try:
            _publishDirectory(Path(stageSource), source, createdFiles, createdDirs)
            _publishDirectory(Path(stageDb), snapshot, createdFiles, createdDirs)
        except OSError as error:
            _rollbackPaths(createdFiles, Path.unlink, error)
            _rollbackPaths(createdDirs, Path.rmdir, error)
            raise
    return Path(snapshotDir) / "restriction_enzymes.sqlite3"


def main() -> None:
    """Create the development catalogue using the active environment's Biopython."""
    parser = argparse.ArgumentParser(description=__doc__)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument(
        "--output", type=Path,
        help="New SQLite snapshot path; existing files are never replaced",
    )
    destination.add_argument("--snapshot-dir", dest="snapshotDir", type=Path,
                             help="New complete snapshot directory; requires --source-dir")
    parser.add_argument("--source-dir", dest="sourceDir", type=Path,
                        help="New installed-source capture directory")
    args = parser.parse_args()
    if (args.snapshotDir is None) != (args.sourceDir is None):
        parser.error("--snapshot-dir and --source-dir must be supplied together")
    if args.snapshotDir is not None:
        print(f"Created {buildSnapshot(args.snapshotDir, sourceDir=args.sourceDir)}")
    else:
        buildDatabase(args.output)
        print(f"Created {args.output}")


if __name__ == "__main__":
    main()
