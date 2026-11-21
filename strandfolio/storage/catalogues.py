"""Read-only restriction and size-marker catalogues shared by the GUI and notebooks."""

from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from pathlib import Path
from string import hexdigits
from typing import Literal
import sqlite3

from strandfolio.data import DATABASE_DIRECTORY

@dataclass(frozen=True, slots=True)
class CatalogueRelease:
    """Identify the SQLite snapshot and its captured upstream dictionary.

    The snapshot checksum identifies the database image used for reads. The
    dictionary checksum identifies the captured source, which may have produced
    more than one database build.
    """

    source: str
    biopythonVersion: str
    sourceReleaseDescription: str
    sourceDictionarySha256: str
    snapshotSha256: str
    schemaVersion: int
    createdUtc: str


@dataclass(frozen=True, slots=True)
class CleavagePair:
    """Keep source cut offsets relative to the forward motif, not a user sequence.

    Offsets are zero-based interbase positions. Negative and out-of-motif values
    are valid. The original bottom-from-end offset remains available for audit.
    """

    pairNumber: int
    topOffset: int
    bottomOffset: int
    biopythonBottomFromEnd: int


@dataclass(frozen=True, slots=True)
class EnzymeSupplier:
    """Describe a supplier recorded by this snapshot, not current availability."""

    code: str
    name: str


@dataclass(frozen=True, slots=True)
class CatalogueIssue:
    """Retain a source-quality warning without correcting the source values."""

    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class EnzymeRecord:
    """Expose one catalogue entry without claiming that digestion is supported.

    Recognition text may include IUPAC ambiguity and alternative motifs separated
    by ``|``. Unknown cuts have an empty cleavagePairs tuple; other unknown source
    values remain None. A motif or known offset is not a simulation-support flag.
    """

    name: str
    rebaseId: int | None
    sourceUrl: str | None
    recognitionSite: str
    recognitionLength: int
    cutNotation: str | None
    cleavageKnown: bool
    overhangType: str
    overhangLength: int | None
    overhangPattern: str | None
    commercialInSnapshot: bool
    cleavagePairs: tuple[CleavagePair, ...]
    suppliers: tuple[EnzymeSupplier, ...]
    issues: tuple[CatalogueIssue, ...]


_REQUIRED_COLUMNS = {
    "metadata": ("key", "value"),
    "enzymes": (
        "name", "rebase_id", "source_url", "recognition_site", "recognition_length",
        "cut_notation", "cleavage_known", "overhang_type", "overhang_length",
        "overhang_pattern", "commercial_in_snapshot",
    ),
    "cleavage_pairs": (
        "enzyme_name", "pair_number", "top_offset", "bottom_offset", "biopython_bottom_from_end",
    ),
    "suppliers": ("code", "name"),
    "enzyme_suppliers": ("enzyme_name", "supplier_code"),
    "data_issues": ("enzyme_name", "code", "detail"),
}
_RELEASE_METADATA = (
    "source", "biopython_version", "source_release_description", "source_dictionary_sha256",
    "created_utc",
)


class RestrictionCatalogue:
    """Own a read-only SQLite connection to one explicitly selected snapshot.

    Use as a context manager or call close(). One read transaction binds the release
    checksum to the rows returned by the reader. The reader never creates a missing
    database or updates catalogue data. Connections follow sqlite3's thread ownership;
    open a reader in each worker.
    """

    def __init__(self, path: str | Path) -> None:
        """Open an existing schema-1 catalogue and validate its structure.

        Args:
            path: Existing SQLite snapshot; there is no implicit active catalogue.

        Raises:
            FileNotFoundError: The supplied path does not exist.
            ValueError: The path is not a file or its SQLite/schema data is invalid.
        """
        self.path = Path(path).resolve()
        self._connection: sqlite3.Connection | None = None
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        if not self.path.is_file():
            raise ValueError("The restriction catalogue path must identify a file.")
        try:
            self._connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA query_only = ON")
            self._connection.execute("BEGIN")
            metadata = self._validateSchema()
            self._release = CatalogueRelease(
                source=metadata["source"],
                biopythonVersion=metadata["biopython_version"],
                sourceReleaseDescription=metadata["source_release_description"],
                sourceDictionarySha256=metadata["source_dictionary_sha256"],
                snapshotSha256=sha256(self._connection.serialize()).hexdigest(),
                schemaVersion=int(metadata["schema_version"]),
                createdUtc=metadata["created_utc"],
            )
        except (sqlite3.Error, ValueError) as error:
            self.close()
            raise ValueError(f"Invalid restriction catalogue: {error}") from error

    def _requireConnection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise ValueError("This restriction catalogue is closed.")
        return self._connection

    def _validateSchema(self) -> dict[str, str]:
        connection = self._requireConnection()
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Expected restriction catalogue schema version 1.")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        for name, required in _REQUIRED_COLUMNS.items():
            if name not in tables:
                raise ValueError(f"Missing required table: {name}.")
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({name})")}
            missing = set(required) - columns
            if missing:
                raise ValueError(f"Missing columns in {name}: {', '.join(sorted(missing))}.")
        metadata = self.metadata
        if metadata.get("schema_version") != "1":
            raise ValueError("Catalogue metadata does not identify schema version 1.")
        if not all(isinstance(key, str) and isinstance(value, str)
                   for key, value in metadata.items()):
            raise ValueError("Catalogue metadata keys and values must be text.")
        for key in _RELEASE_METADATA:
            if not metadata.get(key, "").strip():
                raise ValueError(f"Missing catalogue release metadata: {key}.")
        sourceDigest = metadata["source_dictionary_sha256"]
        if len(sourceDigest) != 64 or any(digit not in hexdigits for digit in sourceDigest):
            raise ValueError("Invalid SHA-256 in catalogue release metadata.")
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed.")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Catalogue contains invalid foreign-key references.")
        invalidCleavage = connection.execute("""
            SELECT e.name FROM enzymes e
            WHERE e.cleavage_known != EXISTS (
                SELECT 1 FROM cleavage_pairs c WHERE c.enzyme_name = e.name
            )
            OR (e.cleavage_known = 1 AND NOT EXISTS (
                SELECT 1 FROM cleavage_pairs c
                WHERE c.enzyme_name = e.name AND c.pair_number = 1
            ))
            UNION
            SELECT e.name FROM enzymes e JOIN cleavage_pairs c ON c.enzyme_name = e.name
            WHERE c.bottom_offset != e.recognition_length + c.biopython_bottom_from_end
        """).fetchone()
        if invalidCleavage is not None:
            raise ValueError(f"Inconsistent cleavage metadata for {invalidCleavage[0]}.")
        return metadata

    @property
    def release(self) -> CatalogueRelease:
        """Return the immutable provenance of the snapshot supplying the definitions."""
        self._requireConnection()
        return self._release

    @property
    def metadata(self) -> dict[str, str]:
        """Return a fresh metadata dictionary, preserving source notices and limitations."""
        return dict(self._requireConnection().execute("SELECT key, value FROM metadata"))

    def summary(self) -> dict[str, int]:
        """Count catalogue coverage; these counts do not measure simulation support."""
        connection = self._requireConnection()
        row = connection.execute("""
            SELECT COUNT(*), COALESCE(SUM(cleavage_known), 0),
                   COALESCE(SUM(commercial_in_snapshot), 0)
            FROM enzymes
        """).fetchone()
        return {
            "enzymes": row[0], "known_cleavage": row[1],
            "unknown_cleavage": row[0] - row[1], "commercial_enzymes": row[2],
            "cleavage_pairs": connection.execute("SELECT COUNT(*) FROM cleavage_pairs").fetchone()[0],
            "flagged_enzymes": connection.execute(
                "SELECT COUNT(DISTINCT enzyme_name) FROM data_issues"
            ).fetchone()[0],
        }

    def get(self, name: str) -> EnzymeRecord:
        """Look up an exact enzyme name case-insensitively.

        Raises:
            KeyError: No enzyme has that name.
            ValueError: The name is not a nonempty string or this reader is closed.
        """
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Choose a nonempty enzyme name.")
        columns = ", ".join(_REQUIRED_COLUMNS["enzymes"])
        row = self._requireConnection().execute(
            f"SELECT {columns} FROM enzymes WHERE name = ? COLLATE NOCASE", (name,),
        ).fetchone()
        if row is None:
            raise KeyError(name)
        return self._record(row)

    def search(
        self, text: str = "", *, commercialOnly: bool = False,
        cleavageKnown: bool | None = None, includeFlagged: bool = True,
        limit: int = 100, offset: int = 0,
    ) -> tuple[EnzymeRecord, ...]:
        """Find literal name/motif substrings in stable case-insensitive name order.

        Args:
            text: Literal substring; SQL wildcard characters have no special meaning.
            commercialOnly: Require supplier availability recorded in this snapshot.
            cleavageKnown: True/False filters known/unknown cuts; None includes both.
            includeFlagged: Include source-quality issues unless explicitly excluded.
            limit: Maximum returned entries, from 1 through 1,000.
            offset: Nonnegative number of matching entries to skip.

        Raises:
            ValueError: An argument is invalid or this reader is closed.
        """
        self._validateSearch(text, commercialOnly, cleavageKnown, includeFlagged, limit, offset)
        literal = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses = ["(name LIKE ? ESCAPE '\\' OR recognition_site LIKE ? ESCAPE '\\')"]
        parameters = [f"%{literal}%", f"%{literal}%"]
        if commercialOnly:
            clauses.append("commercial_in_snapshot = 1")
        if cleavageKnown is not None:
            clauses.append("cleavage_known = ?")
            parameters.append(int(cleavageKnown))
        if not includeFlagged:
            clauses.append("NOT EXISTS (SELECT 1 FROM data_issues WHERE enzyme_name = enzymes.name)")
        columns = ", ".join(_REQUIRED_COLUMNS["enzymes"])
        query = (f"SELECT {columns} FROM enzymes WHERE {' AND '.join(clauses)} "
                 "ORDER BY name COLLATE NOCASE LIMIT ? OFFSET ?")
        rows = self._requireConnection().execute(query, (*parameters, limit, offset)).fetchall()
        return tuple(self._record(row) for row in rows)

    @staticmethod
    def _validateSearch(
        text: str, commercialOnly: bool, cleavageKnown: bool | None,
        includeFlagged: bool, limit: int, offset: int,
    ) -> None:
        if not isinstance(text, str):
            raise ValueError("Search text must be a string.")
        if type(commercialOnly) is not bool or type(includeFlagged) is not bool:
            raise ValueError("Commercial and quality filters must be booleans.")
        if cleavageKnown is not None and type(cleavageKnown) is not bool:
            raise ValueError("cleavageKnown must be True, False, or None.")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("Search limit must be an integer from 1 through 1000.")
        if type(offset) is not int or offset < 0:
            raise ValueError("Search offset must be a nonnegative integer.")

    def _record(self, row: sqlite3.Row) -> EnzymeRecord:
        connection = self._requireConnection()
        name = row["name"]
        pairs = tuple(CleavagePair(*values) for values in connection.execute(
            "SELECT pair_number, top_offset, bottom_offset, biopython_bottom_from_end "
            "FROM cleavage_pairs WHERE enzyme_name = ? ORDER BY pair_number", (name,),
        ))
        suppliers = tuple(EnzymeSupplier(*values) for values in connection.execute(
            "SELECT s.code, s.name FROM suppliers s JOIN enzyme_suppliers es "
            "ON es.supplier_code = s.code WHERE es.enzyme_name = ? ORDER BY s.code", (name,),
        ))
        issues = tuple(CatalogueIssue(*values) for values in connection.execute(
            "SELECT code, detail FROM data_issues WHERE enzyme_name = ? ORDER BY code", (name,),
        ))
        return EnzymeRecord(
            name=name, rebaseId=row["rebase_id"], sourceUrl=row["source_url"],
            recognitionSite=row["recognition_site"], recognitionLength=row["recognition_length"],
            cutNotation=row["cut_notation"], cleavageKnown=bool(row["cleavage_known"]),
            overhangType=row["overhang_type"], overhangLength=row["overhang_length"],
            overhangPattern=row["overhang_pattern"],
            commercialInSnapshot=bool(row["commercial_in_snapshot"]),
            cleavagePairs=pairs, suppliers=suppliers, issues=issues,
        )

    def close(self) -> None:
        """Close this connection; repeated calls are harmless."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "RestrictionCatalogue":
        self._requireConnection()
        return self

    def __exit__(self, excType, excValue, traceback) -> None:
        self.close()


DEFAULT_LADDER_ID = "psu-1kb-extended"
DEFAULT_LADDER_CATALOGUE_PATH = DATABASE_DIRECTORY / "ladders/ladders.sqlite3"
LadderUnit = Literal["bp", "kDa"]


@dataclass(frozen=True, slots=True)
class LadderRelease:
    """Identify the released SQLite image used for a size-marker profile."""

    name: str
    snapshotSha256: str
    schemaVersion: int
    biopythonVersion: str
    reviewed: str

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value.strip() or "\x00" in value for value in (
            self.name, self.biopythonVersion, self.reviewed,
        )):
            raise ValueError("A ladder release requires a name, review date and Biopython version.")
        if (type(self.snapshotSha256) is not str or len(self.snapshotSha256) != 64
                or any(digit not in "0123456789abcdef" for digit in self.snapshotSha256)):
            raise ValueError("Invalid SHA-256 for the ladder snapshot.")
        if type(self.schemaVersion) is not int or self.schemaVersion != 1:
            raise ValueError("Expected ladder catalogue schema version 1.")


@dataclass(frozen=True, slots=True)
class LadderComponent:
    """A marker component identified by preparation and ordinal, with size in profile units.

    A nominal size is an optional DNA label in bp. Loading mass is unknown in
    this catalogue and cannot be inferred from repeated components.
    """

    preparation: int
    ordinal: int
    size: int | float
    nominalSize: int | None
    sourceId: str

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 1 for value in (self.preparation, self.ordinal)):
            raise ValueError("Ladder preparations and component ordinals must be positive integers.")
        if (type(self.size) not in (int, float) or self.size <= 0
                or (type(self.size) is float and not isfinite(self.size))):
            raise ValueError("A ladder component requires a finite positive exact size.")
        if self.nominalSize is not None and (
            type(self.nominalSize) is not int or self.nominalSize <= 0
        ):
            raise ValueError("A nominal bp label must be a positive integer.")
        if type(self.sourceId) is not str or not self.sourceId.strip() or "\x00" in self.sourceId:
            raise ValueError("A ladder component requires its source identity.")


@dataclass(frozen=True, slots=True)
class LadderProfile:
    """Preserve ordered marker components, release and attribution for offline display."""

    ladderId: str
    name: str
    unit: LadderUnit
    release: LadderRelease
    components: tuple[LadderComponent, ...]
    attributions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value.strip() or "\x00" in value for value in (
            self.ladderId, self.name,
        )):
            raise ValueError("A ladder profile requires a valid ID and name.")
        if self.unit not in ("bp", "kDa") or not isinstance(self.release, LadderRelease):
            raise ValueError("A ladder profile requires a supported unit and release.")
        if (type(self.components) is not tuple or not self.components
                or any(not isinstance(component, LadderComponent) for component in self.components)):
            raise ValueError("A ladder profile requires immutable component records.")
        if self.unit == "bp" and any(type(component.size) is not int for component in self.components):
            raise ValueError("DNA marker sizes must be integer bp values.")
        if self.unit == "kDa" and any(component.nominalSize is not None for component in self.components):
            raise ValueError("Protein marker sizes cannot have nominal bp labels.")
        identities = [(component.preparation, component.ordinal) for component in self.components]
        if len(set(identities)) != len(identities):
            raise ValueError("A ladder profile contains duplicate component identities.")
        if type(self.attributions) is not tuple or any(
            type(text) is not str or not text.strip() or "\x00" in text for text in self.attributions
        ):
            raise ValueError("Ladder attributions must be immutable nonblank text values.")

    @property
    def range(self) -> tuple[int | float, int | float]:
        """Return shortest and longest actual sizes in the profile's own unit."""
        sizes = tuple(component.size for component in self.components)
        return min(sizes), max(sizes)


def loadDnaLadders(
    path: str | Path = DEFAULT_LADDER_CATALOGUE_PATH,
) -> tuple[LadderProfile, ...]:
    """Read DNA profiles, exact components and attribution from one SQLite snapshot.

    Raise FileNotFoundError for a missing path and ValueError for an invalid
    catalogue. One read transaction binds copied values to the snapshot digest.
    Keep preparation order and component identity, including equal-size entries.
    """
    sourcePath = Path(path).resolve()
    if not sourcePath.exists():
        raise FileNotFoundError(sourcePath)
    if not sourcePath.is_file():
        raise ValueError("The ladder catalogue path must identify a file.")
    try:
        with closing(sqlite3.connect(sourcePath.as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
            if (connection.execute("PRAGMA user_version").fetchone()[0] != 1
                    or metadata.get("schema_version") != "1"):
                raise ValueError("Expected ladder catalogue schema version 1.")
            if (connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                    or connection.execute("PRAGMA foreign_key_check").fetchone() is not None):
                raise ValueError("Ladder catalogue failed SQLite integrity checks.")
            release = LadderRelease(
                metadata["release"], sha256(connection.serialize()).hexdigest(), 1,
                metadata["biopython"], metadata["reviewed"],
            )
            return _readDnaProfiles(connection, release)
    except (sqlite3.Error, KeyError, ValueError) as error:
        raise ValueError(f"Invalid ladder catalogue: {error}") from error


def _readDnaProfiles(
    connection: sqlite3.Connection, release: LadderRelease,
) -> tuple[LadderProfile, ...]:
    rows = connection.execute("""
        SELECT l.id, l.name, c.prep, c.ordinal, c.length_bp, c.nominal_bp,
               p.source_id, s.attribution, s.terms_url
        FROM ladders l
        LEFT JOIN components c ON c.ladder_id = l.id
        LEFT JOIN preparations p ON p.ladder_id = l.id AND p.prep = c.prep
        LEFT JOIN sources s ON s.id = p.source_id
        WHERE l.unit = 'bp'
        ORDER BY l.name COLLATE NOCASE, l.id, c.prep, c.ordinal
    """)
    groups: dict[str, tuple[str, list[LadderComponent], dict[str, None]]] = {}
    for ladderId, name, prep, ordinal, exact, nominal, sourceId, attribution, termsUrl in rows:
        _, components, attributions = groups.setdefault(ladderId, (name, [], {}))
        components.append(LadderComponent(prep, ordinal, exact, nominal, sourceId))
        if any(type(text) is not str or not text.strip() for text in (attribution, termsUrl)):
            raise ValueError(f"Missing source attribution for ladder {ladderId}.")
        attributions[f"{attribution} {termsUrl}"] = None
    return tuple(
        LadderProfile(ladderId, name, "bp", release, tuple(components), tuple(attributions))
        for ladderId, (name, components, attributions) in groups.items()
    )
