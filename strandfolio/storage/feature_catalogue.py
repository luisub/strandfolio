"""Read-only queries and exact DNA identification for a feature catalogue."""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from hashlib import sha256
from itertools import chain, groupby
from operator import itemgetter
from pathlib import Path
import sqlite3
from typing import Literal

from Bio.Data.IUPACData import ambiguous_dna_letters

from strandfolio.data import DATABASE_DIRECTORY
from strandfolio.operations.conversion import reverseComplementBases


FeatureTopology = Literal["linear", "circular"]
MatchStrand = Literal[1, -1] | None
DEFAULT_FEATURE_CATALOGUE_PATH = DATABASE_DIRECTORY / "features/features.sqlite3"
_DNA_LETTERS = frozenset(ambiguous_dna_letters)
_REQUIRED_COLUMNS = {
    "sources": ("id", "title", "url", "license", "license_url", "attribution"),
    "features": ("id", "label", "kind", "source_id", "source_record", "length_bp",
                 "sequence_sha256", "topology"),
    "aliases": ("feature_id", "alias"),
}
_SELECT_FEATURES = """
    SELECT f.id, f.label, f.kind, f.source_record, f.length_bp, f.sequence_sha256, f.topology,
           s.id AS source_id, s.title, s.url, s.license, s.license_url, s.attribution, a.alias
    FROM features f JOIN sources s ON s.id = f.source_id
    LEFT JOIN aliases a ON a.feature_id = f.id
"""
_ORDER_FEATURES = " ORDER BY f.id, a.alias COLLATE NOCASE, a.alias"


@dataclass(frozen=True, slots=True)
class FeatureSource:
    """Provenance and reuse terms recorded by the catalogue."""

    id: str
    title: str
    url: str
    license: str
    licenseUrl: str
    attribution: str


@dataclass(frozen=True, slots=True)
class FeatureRecord:
    """One reference identity and its recorded sequence fingerprint."""

    id: str
    label: str
    kind: str
    source: FeatureSource
    sourceRecord: str
    lengthBp: int
    sequenceSha256: str
    topology: FeatureTopology
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FeatureMatch:
    """An exact match spanning the supplied interval or entire circular molecule.

    Strand is +1 or -1 relative to the stored linear reference or the canonical
    circular sequence. None means the two orientations are indistinguishable.
    """

    record: FeatureRecord
    strand: MatchStrand


@dataclass(frozen=True, slots=True)
class FeatureHit:
    """One exact reference and locus in a scanned DNA sequence.

    Segments are zero-based, end-exclusive intervals in the supplied sequence's
    forward order, with a second interval starting at zero for a wrapped hit.
    Strand is +1 for the stored reference orientation, -1 for its reverse
    complement, or None if both orientations match the same locus.
    """

    record: FeatureRecord
    segments: tuple[tuple[int, int], ...]
    strand: MatchStrand

    @property
    def start0(self) -> int:
        """Return the zero-based start in the supplied sequence's forward order."""
        return self.segments[0][0]

    @property
    def lengthBp(self) -> int:
        """Return the full matched length in base pairs."""
        return self.record.lengthBp


def _normalizeDna(sequence: str, circular: bool) -> str:
    if type(circular) is not bool:
        raise ValueError("circular must be a boolean.")
    if not isinstance(sequence, str) or not sequence.isascii():
        raise ValueError("Supply a DNA string using IUPAC letters.")
    dna = sequence.upper()
    if set(dna) - _DNA_LETTERS:
        raise ValueError("Supply a DNA string using IUPAC letters.")
    return dna


def _leastRotation(sequence: str) -> str:
    """Find the lexicographically least rotation of a nonempty string in linear time."""
    length = len(sequence)
    doubled = sequence + sequence
    first, second, matched = 0, 1, 0
    while first < length and second < length and matched < length:
        firstBase, secondBase = doubled[first + matched], doubled[second + matched]
        if firstBase == secondBase:
            matched += 1
            continue
        # The losing start and the next `matched` starts cannot be minimal.
        if firstBase > secondBase:
            first = max(first + matched + 1, second + 1)
        else:
            second = max(second + matched + 1, first + 1)
        matched = 0
    start = min(first, second)
    return doubled[start:start + length]


def _orientedSequences(dna: str, circular: bool) -> tuple[str, str]:
    reverse = reverseComplementBases(dna, "DNA")
    if circular:
        return _leastRotation(dna), _leastRotation(reverse)
    return dna, reverse


def _fingerprint(dna: str) -> str:
    return sha256(dna.encode("ascii")).hexdigest()


def sequenceFingerprint(sequence: str, *, circular: bool = False) -> str:
    """Hash uppercase IUPAC DNA, interpreting ambiguity symbols literally.

    Linear fingerprints preserve the supplied orientation. Circular fingerprints
    use the lexicographically least rotation across both orientations. Empty DNA,
    invalid symbols and a non-boolean circular flag raise ValueError.
    """
    dna = _normalizeDna(sequence, circular)
    if not dna:
        raise ValueError("A sequence fingerprint requires at least one DNA base.")
    if circular:
        dna = min(_orientedSequences(dna, circular))
    return _fingerprint(dna)


class FeatureCatalogue:
    """Open one explicit SQLite snapshot read-only. Use as a context manager or close."""

    def __init__(self, path: str | Path) -> None:
        """Open a schema-1 database. Raise FileNotFoundError for a missing path."""
        self.path = Path(path).resolve()
        self._connection: sqlite3.Connection | None = None
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        if not self.path.is_file():
            raise ValueError("The feature catalogue path must identify a file.")
        try:
            self._connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA query_only = ON")
            self._connection.execute("BEGIN")
            self._validateSchema()
            self._release = sha256(self._connection.serialize()).hexdigest()
        except (sqlite3.Error, ValueError) as error:
            self.close()
            raise ValueError(f"Invalid feature catalogue: {error}") from error

    def _requireConnection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise ValueError("This feature catalogue is closed.")
        return self._connection

    @property
    def release(self) -> str:
        """SHA-256 of the SQLite snapshot supplying this reader's exact matches."""
        return self._release

    def _validateSchema(self) -> None:
        connection = self._requireConnection()
        if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Expected feature catalogue schema version 1.")
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
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Feature catalogue contains a broken foreign key.")

    @staticmethod
    def _source(row: sqlite3.Row) -> FeatureSource:
        return FeatureSource(
            id=row["source_id"], title=row["title"], url=row["url"], license=row["license"],
            licenseUrl=row["license_url"], attribution=row["attribution"],
        )

    def _records(self, rows: sqlite3.Cursor) -> Iterator[FeatureRecord]:
        for _, values in groupby(rows, key=itemgetter("id")):
            row = next(values)
            aliases = tuple(value["alias"] for value in chain((row,), values)
                            if value["alias"] is not None)
            yield FeatureRecord(
                id=row["id"], label=row["label"], kind=row["kind"], source=self._source(row),
                sourceRecord=row["source_record"], lengthBp=row["length_bp"],
                sequenceSha256=row["sequence_sha256"], topology=row["topology"], aliases=aliases,
            )

    def sources(self) -> tuple[FeatureSource, ...]:
        """List provenance entries in source ID order, even if they have no references."""
        rows = self._requireConnection().execute(
            "SELECT id AS source_id, title, url, license, license_url, attribution "
            "FROM sources ORDER BY id"
        )
        return tuple(self._source(row) for row in rows)

    def get(self, featureId: str) -> FeatureRecord:
        """Look up a feature by exact ID. Raise KeyError if the ID is absent."""
        if not isinstance(featureId, str) or not featureId.strip():
            raise ValueError("Choose a nonempty feature ID.")
        rows = self._requireConnection().execute(
            _SELECT_FEATURES + " WHERE f.id = ?" + _ORDER_FEATURES, (featureId,),
        )
        record = next(self._records(rows), None)
        if record is None:
            raise KeyError(featureId)
        return record

    def __iter__(self) -> Iterator[FeatureRecord]:
        """Iterate feature records in ID order while this reader stays open."""
        rows = self._requireConnection().execute(_SELECT_FEATURES + _ORDER_FEATURES)
        return self._records(rows)

    def identify(self, sequence: str, *, circular: bool = False) -> tuple[FeatureMatch, ...]:
        """Identify exactly the supplied DNA by reference length and fingerprint.

        Pass a complete selected interval for linear references or the whole
        molecule for circular references. There is no substring search. Empty DNA
        yields no matches. Invalid DNA or a non-boolean circular flag raises
        ValueError. IUPAC ambiguity symbols compare literally.
        """
        connection = self._requireConnection()
        dna = _normalizeDna(sequence, circular)
        if not dna:
            return ()
        forward, reverse = _orientedSequences(dna, circular)
        strands: dict[str, MatchStrand]
        if forward == reverse:
            strands = {_fingerprint(forward): None}
        elif circular:
            canonical = min(forward, reverse)
            strands = {_fingerprint(canonical): 1 if forward == canonical else -1}
        else:
            strands = {_fingerprint(forward): 1, _fingerprint(reverse): -1}

        topology = "circular" if circular else "linear"
        placeholders = ", ".join("?" for _ in strands)
        rows = connection.execute(
            _SELECT_FEATURES + f" WHERE f.topology = ? AND f.length_bp = ? "
            f"AND f.sequence_sha256 IN ({placeholders})" + _ORDER_FEATURES,
            (topology, len(dna), *strands),
        )
        return tuple(
            FeatureMatch(record, strands[record.sequenceSha256]) for record in self._records(rows)
        )

    def scan(
        self, sequence: str, *, circular: bool = False,
        isCanceled: Callable[[], bool] | None = None,
    ) -> tuple[FeatureHit, ...]:
        """Find exact linear-reference windows on either strand of the supplied DNA.

        Circular sequences can have origin-crossing windows. Use identify with
        circular=True for whole circular references. Hits are ordered by start,
        length, then reference ID. Empty DNA yields no hits. Invalid DNA or a
        non-boolean circular flag raises ValueError. IUPAC symbols compare literally.

        Hashes are computed for both orientations at each possible start and
        distinct eligible reference length. The amount of DNA hashed is bounded
        by twice the sequence length times the sum of those reference lengths.
        Cancellation raises InterruptedError without returning partial matches.
        """
        connection = self._requireConnection()
        if isCanceled is not None and isCanceled():
            raise InterruptedError("Label detection canceled.")
        dna = _normalizeDna(sequence, circular)
        if not dna:
            return ()
        moleculeLength = len(dna)
        rows = connection.execute(
            _SELECT_FEATURES + " WHERE f.topology = 'linear' AND f.length_bp BETWEEN 1 AND ?"
            + _ORDER_FEATURES,
            (moleculeLength,),
        )
        referencesByLength: dict[int, dict[bytes, list[FeatureRecord]]] = {}
        for record in self._records(rows):
            fingerprints = referencesByLength.setdefault(record.lengthBp, {})
            fingerprints.setdefault(bytes.fromhex(record.sequenceSha256), []).append(record)
        if not referencesByLength:
            return ()

        forwardBytes = dna.encode("ascii")
        reverseBytes = reverseComplementBases(dna, "DNA").encode("ascii")
        if circular:
            tailLength = max(referencesByLength) - 1
            forwardBytes += forwardBytes[:tailLength]
            reverseBytes += reverseBytes[:tailLength]
        forward, reverse = memoryview(forwardBytes), memoryview(reverseBytes)
        hits: list[FeatureHit] = []
        for lengthBp, fingerprints in referencesByLength.items():
            positions = moleculeLength if circular else moleculeLength - lengthBp + 1
            for start0 in range(positions):
                if start0 % 256 == 0 and isCanceled is not None and isCanceled():
                    raise InterruptedError("Label detection canceled.")
                end0 = start0 + lengthBp
                # The last forward base is the first base of this reverse window.
                reverseStart = (moleculeLength - end0) % moleculeLength
                forwardDigest = sha256(forward[start0:end0]).digest()
                reverseDigest = sha256(reverse[reverseStart:reverseStart + lengthBp]).digest()
                forwardMatches = fingerprints.get(forwardDigest, ())
                reverseMatches = fingerprints.get(reverseDigest, ())
                if not forwardMatches and not reverseMatches:
                    continue
                if end0 > moleculeLength:
                    segments = ((start0, moleculeLength), (0, end0 - moleculeLength))
                else:
                    segments = ((start0, end0),)
                if forwardDigest == reverseDigest:
                    hits.extend(FeatureHit(record, segments, None) for record in forwardMatches)
                else:
                    hits.extend(FeatureHit(record, segments, 1) for record in forwardMatches)
                    hits.extend(FeatureHit(record, segments, -1) for record in reverseMatches)
        return tuple(sorted(hits, key=lambda hit: (hit.start0, hit.lengthBp, hit.record.id)))

    def close(self) -> None:
        """Close the reader. Repeated calls are harmless."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "FeatureCatalogue":
        self._requireConnection()
        return self

    def __exit__(self, excType, excValue, traceback) -> None:
        self.close()
