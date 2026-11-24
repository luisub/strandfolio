"""Build and validate a restriction catalogue from the installed Biopython dictionary."""

from datetime import datetime, timezone
import hashlib
from importlib.metadata import distribution
from pathlib import Path
import sqlite3
import tempfile

import Bio
from Bio import Restriction
from Bio.Restriction import Restriction_Dictionary


_SCHEMA = """
PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE enzymes (
    name TEXT PRIMARY KEY COLLATE NOCASE,
    rebase_id INTEGER,
    source_url TEXT,
    recognition_site TEXT NOT NULL,
    recognition_length INTEGER NOT NULL CHECK (recognition_length > 0),
    cut_notation TEXT,
    cleavage_known INTEGER NOT NULL CHECK (cleavage_known IN (0, 1)),
    overhang_type TEXT NOT NULL,
    overhang_length INTEGER,
    overhang_pattern TEXT,
    commercial_in_snapshot INTEGER NOT NULL CHECK (commercial_in_snapshot IN (0, 1))
);
CREATE INDEX enzymes_recognition_site ON enzymes(recognition_site);
CREATE TABLE cleavage_pairs (
    enzyme_name TEXT NOT NULL REFERENCES enzymes(name),
    pair_number INTEGER NOT NULL CHECK (pair_number IN (1, 2)),
    top_offset INTEGER NOT NULL,
    bottom_offset INTEGER NOT NULL,
    biopython_bottom_from_end INTEGER NOT NULL,
    PRIMARY KEY (enzyme_name, pair_number)
);
CREATE TABLE suppliers (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE enzyme_suppliers (
    enzyme_name TEXT NOT NULL REFERENCES enzymes(name),
    supplier_code TEXT NOT NULL REFERENCES suppliers(code),
    PRIMARY KEY (enzyme_name, supplier_code)
);
CREATE TABLE data_issues (
    enzyme_name TEXT NOT NULL REFERENCES enzymes(name),
    code TEXT NOT NULL,
    detail TEXT NOT NULL,
    PRIMARY KEY (enzyme_name, code)
);
"""


def installedSourceFiles() -> dict[str, Path]:
    """Locate source and attribution without modifying the installed package."""
    package = distribution("biopython")
    licenseFiles = [
        entry for entry in package.files or []
        if entry.name in {"LICENSE", "LICENSE.rst", "LICENSE.txt"}
    ]
    if not licenseFiles:
        raise RuntimeError("Biopython license text was not found; source attribution is required.")
    return {
        "Restriction_Dictionary.py": Path(Restriction_Dictionary.__file__),
        "Restriction.py": Path(Restriction.Restriction.__file__),
        "LICENSE": Path(package.locate_file(sorted(licenseFiles, key=str)[0])),
    }


def _sourceMetadata() -> dict[str, str]:
    """Capture the exact installed source, header, and supplied license."""
    sources = installedSourceFiles()
    source = sources["Restriction_Dictionary.py"]
    return {
        "schema_version": "1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Installed Bio.Restriction.AllEnzymes; derived from REBASE",
        "biopython_version": Bio.__version__,
        "source_release_description": (Restriction_Dictionary.__doc__ or "Unspecified").strip(),
        "source_dictionary_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_dictionary_header": source.read_text().split('"""', 1)[0].strip(),
        "biopython_license": sources["LICENSE"].read_text(),
        "reference_url": "https://rebase.neb.com/rebase/rebase.html",
        "coverage": "Installed Biopython snapshot; not all known enzymes or a live REBASE mirror",
        "redistribution_status": "Upstream data redistribution terms not independently verified",
        "coordinate_convention": (
            "0-based interbase offsets from the left edge of the forward recognition motif. "
            "Top is 5'-to-3' left-to-right; bottom is 3'-to-5' left-to-right. "
            "Negative offsets and offsets beyond motif length are allowed. "
            "bottom_offset = recognition_length + biopython_bottom_from_end. "
            "Reverse-strand hits require orientation conversion before sequence mapping."
        ),
        "limitations": (
            "Recognition motifs may contain IUPAC codes and alternative motifs separated by |. "
            "Missing cut positions remain unknown. No methylation model, reaction efficiency, "
            "star activity, buffer compatibility, or digestion implementation is provided."
        ),
    }


def _populate(connection: sqlite3.Connection) -> None:
    """Copy every enzyme and known cleavage pair without inferring unknown cuts."""
    connection.executescript(_SCHEMA)
    connection.executemany("INSERT INTO metadata VALUES (?, ?)", _sourceMetadata().items())
    connection.executemany(
        "INSERT INTO suppliers VALUES (?, ?)",
        [(code, record[0]) for code, record in sorted(Restriction_Dictionary.suppliers.items())],
    )
    for enzyme in sorted(Restriction.AllEnzymes, key=str):
        pairs = [(enzyme.fst5, enzyme.fst3), (enzyme.scd5, enzyme.scd3)]
        if any((top is None) != (bottom is None) for top, bottom in pairs):
            raise ValueError(f"Incomplete cleavage pair for {enzyme}")
        known = enzyme.fst5 is not None
        connection.execute(
            "INSERT INTO enzymes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(enzyme), enzyme.id, Restriction_Dictionary.rest_dict[str(enzyme)]["uri"],
                enzyme.site, enzyme.size,
                enzyme.elucidate() if known and enzyme.scd5 is None else None,
                int(known), enzyme.overhang(),
                abs(enzyme.ovhg) if enzyme.ovhg is not None else None,
                enzyme.ovhgseq, int(bool(enzyme.suppl)),
            ),
        )
        for number, (top, bottom) in enumerate(pairs, 1):
            if top is not None:
                connection.execute(
                    "INSERT INTO cleavage_pairs VALUES (?, ?, ?, ?, ?)",
                    (str(enzyme), number, top, enzyme.size + bottom, bottom),
                )
                difference = top - (enzyme.size + bottom)
                if difference != enzyme.ovhg:
                    connection.execute(
                        "INSERT OR IGNORE INTO data_issues VALUES (?, ?, ?)",
                        (str(enzyme), "overhang_mismatch",
                         f"Source signed overhang {enzyme.ovhg}; recorded cut pair "
                         f"{number} implies {difference}. "
                         "Original values retained; review required."),
                    )
        connection.executemany(
            "INSERT INTO enzyme_suppliers VALUES (?, ?)",
            [(str(enzyme), code) for code in sorted(enzyme.suppl)],
        )
    for key, query in (
        ("enzyme_count", "SELECT COUNT(*) FROM enzymes"),
        ("known_cleavage_count", "SELECT COUNT(*) FROM enzymes WHERE cleavage_known = 1"),
        ("cleavage_pair_count", "SELECT COUNT(*) FROM cleavage_pairs"),
    ):
        value = str(connection.execute(query).fetchone()[0])
        connection.execute("INSERT INTO metadata VALUES (?, ?)", (key, value))


def validateDatabase(connection: sqlite3.Connection) -> dict:
    """Reject incomplete catalogues, invalid links, or inconsistent cut coordinates."""
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("SQLite integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Invalid enzyme/supplier references")
    names = {row[0] for row in connection.execute("SELECT name FROM enzymes")}
    if names != {str(enzyme) for enzyme in Restriction.AllEnzymes}:
        raise ValueError("Catalogue does not contain exactly the source enzyme set")
    invalid = connection.execute("""
        SELECT e.name FROM enzymes e
        WHERE e.cleavage_known != EXISTS (
            SELECT 1 FROM cleavage_pairs c WHERE c.enzyme_name = e.name AND c.pair_number = 1
        )
        UNION
        SELECT e.name FROM enzymes e JOIN cleavage_pairs c ON e.name = c.enzyme_name
        WHERE c.bottom_offset != e.recognition_length + c.biopython_bottom_from_end
    """).fetchall()
    if invalid:
        raise ValueError(f"Inconsistent cleavage metadata: {invalid}")
    mismatches = {row[0] for row in connection.execute("""
        SELECT e.name FROM enzymes e JOIN cleavage_pairs c ON e.name = c.enzyme_name
        WHERE c.top_offset - c.bottom_offset != CASE e.overhang_type
            WHEN '5'' overhang' THEN -e.overhang_length
            WHEN '3'' overhang' THEN e.overhang_length ELSE 0 END
    """)}
    flagged = {row[0] for row in connection.execute(
        "SELECT enzyme_name FROM data_issues WHERE code = 'overhang_mismatch'"
    )}
    if mismatches != flagged:
        raise ValueError(
            "Source overhang inconsistencies must be explicitly recorded in data_issues"
        )
    return _reconcileSource(connection)


def _reconcileSource(connection: sqlite3.Connection) -> dict:
    """Compare every stored source field and relationship, independently of row counts."""
    expected = {table: set() for table in (
        "enzymes", "cleavage_pairs", "suppliers", "enzyme_suppliers", "data_issues",
    )}
    expected["suppliers"] = {
        (code, values[0]) for code, values in Restriction_Dictionary.suppliers.items()
    }
    for name, source in Restriction_Dictionary.rest_dict.items():
        enzyme = getattr(Restriction, name)
        known = source["fst5"] is not None
        overhang = source["ovhg"]
        expected["enzymes"].add((
            name, source["id"], source["uri"], source["site"], source["size"],
            enzyme.elucidate() if known and source["scd5"] is None else None,
            int(known), enzyme.overhang(), abs(overhang) if overhang is not None else None,
            source["ovhgseq"], int(bool(source["suppl"])),
        ))
        for number, prefix in ((1, "fst"), (2, "scd")):
            top, bottom = source[prefix + "5"], source[prefix + "3"]
            if top is None:
                continue
            normalizedBottom = source["size"] + bottom
            expected["cleavage_pairs"].add((name, number, top, normalizedBottom, bottom))
            difference = top - normalizedBottom
            if difference != overhang:
                expected["data_issues"].add((
                    name, "overhang_mismatch", f"Source signed overhang {overhang}; recorded cut pair "
                    f"{number} implies {difference}. Original values retained; review required.",
                ))
        expected["enzyme_suppliers"].update((name, code) for code in source["suppl"])
    for table, rows in expected.items():
        # Table names are the fixed internal set above, never user-supplied SQL.
        actual = set(connection.execute(f"SELECT * FROM {table}"))
        if actual != rows:
            raise ValueError(f"Source reconciliation failed for {table}")
    return {**{table: len(rows) for table, rows in expected.items()},
            "all_represented_fields_match": True}


def buildDatabase(output: Path) -> None:
    """Build and validate a complete snapshot, refusing to replace an existing file.

    Args:
        output: Destination SQLite file; the parent directory is created if needed.

    Raises:
        FileExistsError: The output already exists.
        ValueError: Source data or the resulting database fails validation.
    """
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Keep the existing snapshot and choose a new output path: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".restriction-build-", dir=output.parent) as temporary:
        database = Path(temporary) / "catalogue.sqlite3"
        connection = sqlite3.connect(database)
        try:
            with connection:
                _populate(connection)
            validateDatabase(connection)
        finally:
            connection.close()
        # Same-filesystem hard linking publishes a complete file without replacing an existing path.
        output.hardlink_to(database)
