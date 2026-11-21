"""Read and write sequence files."""

from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from io import BytesIO, StringIO
import os
from pathlib import Path
import re
import stat
from struct import error as StructError
from tempfile import TemporaryDirectory
from textwrap import wrap
from threading import RLock
from typing import BinaryIO, Literal, TextIO
import warnings as parserWarnings
from xml.parsers.expat import ExpatError

import Bio
from Bio import SeqIO
from Bio.Data.IUPACData import ambiguous_dna_letters, ambiguous_rna_letters
from Bio.GenBank.Scanner import GenBankScanner
from Bio.Seq import Seq, UndefinedSequenceError
from Bio.SeqFeature import BetweenPosition, Location, OneOfPosition, WithinPosition
from Bio.SeqIO.InsdcIO import GenBankWriter
from Bio.SeqRecord import SeqRecord

from strandfolio.domain.features import PublicationReference, canonicalFeature
from strandfolio.domain.provenance import SourceArtifact
from strandfolio.domain.sequence import UNVERIFIED_TRAVERSAL_WARNING, MoleculeType, SequenceRevision


FASTA_EXTENSIONS = (".fasta", ".fa", ".fas", ".fna", ".faa", ".frn")
GENBANK_EXTENSIONS = (".gb", ".gbk", ".gbff", ".genbank")
SNAPGENE_EXTENSIONS = (".dna",)
TEXT_EXTENSIONS = (".txt",)
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 200
SequenceFormat = Literal["fasta", "genbank", "snapgene", "text"]
_FASTA_MOLECULE_HINTS: dict[str, MoleculeType] = {".faa": "protein", ".frn": "RNA"}
# Packet categories inspected in the installed Biopython 1.88 reader, plus its cookie.
_SNAPGENE_PARSED_PACKETS = frozenset((0x00, 0x05, 0x06, 0x09, 0x0A))
_SNAPGENE_COOKIE = b"\x09\x00\x00\x00\x0eSnapGene"
_GENBANK_LOCUS = re.compile(r"LOCUS {7}(\S+) +([0-9]+) +bp(?: +(.*?))? *\Z")
_GENBANK_TEXT_MARKER = re.compile(rb"LOCUS[ \t]+[^ \t\r\n]")
_GENBANK_DATE = re.compile(r"[0-9]{2}-[A-Z]{3}-[0-9]{4}")
_GENBANK_VERSION = re.compile(r"(\S+)(?: +GI:[0-9]+)?")
_GENBANK_DIVISIONS = frozenset(
    "PRI ROD MAM VRT INV PLN BCT VRL PHG SYN UNA EST PAT STS GSS HTG HTC ENV CON TSA WGS TPA UNK"
    .split()
)
_GENBANK_SEQUENCE_LINE = re.compile(r" *([1-9][0-9]*) +([A-Za-z]+(?: +[A-Za-z]+)*) *")
_GENBANK_BASE = r"0*[1-9][0-9]*"
_GENBANK_FUZZY_POSITION = (
    rf"(?:[<>]?{_GENBANK_BASE}|\({_GENBANK_BASE}\.{_GENBANK_BASE}\)"
    rf"|one-of\({_GENBANK_BASE}(?:,{_GENBANK_BASE})+\))"
)
_GENBANK_LOCATION_ATOM = (
    rf"(?:[A-Za-z0-9_.*'|\-]+:)?(?:{_GENBANK_FUZZY_POSITION}\.\.{_GENBANK_FUZZY_POSITION}"
    rf"|{_GENBANK_BASE}\^{_GENBANK_BASE}|[<>]?{_GENBANK_BASE})"
)
_GENBANK_SIMPLE_LOCATION = re.compile(_GENBANK_LOCATION_ATOM)
_GENBANK_LOCATION_PART = rf"(?:{_GENBANK_LOCATION_ATOM}|complement\({_GENBANK_LOCATION_ATOM}\))"
_GENBANK_COMPOUND_LOCATION = re.compile(
    rf"(join|order|bond)\({_GENBANK_LOCATION_PART}(?:,{_GENBANK_LOCATION_PART})+\)"
)
_GENBANK_BETWEEN_BASES = re.compile(
    r"(?:^|(?<=[(,]))(?:([^(),:^]+):)?([0-9]+)\^([0-9]+)(?=$|[,)])"
)
_GENBANK_FEATURE_NAME = re.compile(r"(?=[^A-Za-z]*[A-Za-z])[A-Za-z0-9_'*-]+")
_GENBANK_FEATURE_INDENT = " " * 21
_GENBANK_HEADER_FIELDS = frozenset((
    "DEFINITION", "ACCESSION", "VERSION", "NID", "PID", "DBSOURCE", "KEYWORDS", "SEGMENT",
    "SOURCE", "PROJECT", "DBLINK", "REFERENCE", "COMMENT",
))
_GENBANK_SINGLETON_HEADER_FIELDS = frozenset((
    "DEFINITION", "VERSION", "NID", "PID", "DBSOURCE", "SEGMENT", "SOURCE", "ORGANISM",
))
_GENBANK_REFERENCE_FIELDS = frozenset((
    "AUTHORS", "CONSRTM", "TITLE", "JOURNAL", "MEDLINE", "PUBMED", "REMARK",
))
_GENBANK_SOURCE_ONLY_FIELDS = frozenset((
    "PRIMARY", "BASE COUNT", "WGS", "WGS_SCAFLD", "TSA", "TLS",
))
_BIO_WARNING_LOCK = RLock()


@dataclass(frozen=True, slots=True)
class SequenceImportResult:
    """Hold parsed snapshots, original-byte evidence and format-specific limitations.

    sourcePath identifies the supplied file, or is None for pasted text.
    Captured hashes describe this intake; later external file changes cannot alter
    these snapshots. formatName is the parser identifier, not the filename suffix.
    sourceOrigin is the normalized absolute path supplied to the reader, before
    resolving symlinks. interpretation distinguishes declaration, automatic
    selection and an explicitly chosen molecule type.
    """

    records: tuple[SequenceRevision, ...]
    source: SourceArtifact
    sourcePath: Path | None
    warnings: tuple[str, ...]
    biopythonVersion: str
    formatName: SequenceFormat
    captureBytes: bytes
    sourceOrigin: Path | None
    interpretation: str


@dataclass(frozen=True, slots=True)
class TextNormalization:
    """Preview the exact letters and ASCII formatting removed from literal text."""

    sequence: str
    groupingCharactersRemoved: int
    lineBreaksRemoved: int


@dataclass(frozen=True, slots=True)
class PlainTextSource:
    """Keep one bounded file or clipboard capture for explicit text intake."""

    sourcePath: Path | None
    resolvedPath: Path | None
    captureBytes: bytes

    def preview(
        self, moleculeType: MoleculeType, *, maxLetters: int | None = None,
    ) -> tuple[SequenceImportResult, TextNormalization | None]:
        """Prepare a record and show the formatting removed before accepting the import."""
        _requireTextSource(self.sourcePath, _detectFormat(self.captureBytes))
        return _prepareTextBytes(
            self.sourcePath, self.resolvedPath, self.captureBytes, moleculeType, maxLetters,
        )


@contextmanager
def _captureBioWarnings(category: type[Warning]) -> Iterator[list[parserWarnings.WarningMessage]]:
    # CPython 3.12 warning hooks are process-wide, including nested parser checks.
    with _BIO_WARNING_LOCK, parserWarnings.catch_warnings(record=True) as notices:
        parserWarnings.simplefilter("always", category)
        yield notices


def _validateByteLimit(maxBytes: int) -> None:
    if type(maxBytes) is not int or maxBytes < 1:
        raise ValueError("The source byte limit must be a positive integer.")


def _checkCaptureSize(size: int, maxBytes: int) -> None:
    if size > maxBytes:
        limit = "64 MiB" if maxBytes == MAX_FILE_BYTES else f"{maxBytes:,} bytes"
        raise ValueError(f"The source exceeds the {limit} source-file limit.")


@contextmanager
def _openRegularInput(path: Path) -> Iterator[BinaryIO]:
    """Follow input aliases and reject pipes and devices before reading any bytes."""
    def openInput(name: str, flags: int) -> int:
        return os.open(name, flags | getattr(os, "O_NONBLOCK", 0))

    with open(path, "rb", opener=openInput) as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise OSError(f"Expected a regular file: {path}")
        yield source


def _capture(path: Path, maxBytes: int) -> bytes:
    _validateByteLimit(maxBytes)
    with _openRegularInput(path) as sourceFile:
        raw = sourceFile.read(maxBytes + 1)
    _checkCaptureSize(len(raw), maxBytes)
    return raw


def _captureSource(path: str | Path, maxBytes: int) -> tuple[Path, Path, bytes]:
    """Record the supplied path and its resolved target before reading up to the byte limit."""
    supplied = Path(path)
    resolved = supplied.resolve()
    origin = Path(os.path.abspath(supplied))
    # A parent step after a symlink can refer to a different actual origin.
    if ".." in supplied.parts and origin.resolve() != resolved:
        origin = supplied.absolute()
    return origin, resolved, _capture(resolved, maxBytes)


def _validateRecordLimit(maxRecords: int) -> None:
    if type(maxRecords) is not int or maxRecords < 1:
        raise ValueError("The record limit must be a positive integer.")


def _decode(raw: bytes, formatLabel: str, *, allowBom: bool = True) -> str:
    try:
        return raw.decode("utf-8-sig" if allowBom else "utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"Expected a UTF-8 {formatLabel} document.") from error


def _importResult(
    records: list[SequenceRevision],
    source: SourceArtifact,
    sourcePath: Path | None,
    warnings: list[str],
    formatName: SequenceFormat,
    raw: bytes,
    sourceOrigin: Path | None,
    interpretation: str = "declared",
) -> SequenceImportResult:
    return SequenceImportResult(
        tuple(records), source, sourcePath, tuple(warnings), Bio.__version__, formatName, raw,
        sourceOrigin, interpretation,
    )


def _validateFastaStructure(text: str, maxRecords: int) -> tuple[str, ...]:
    """Check plain FASTA framing before a permissive parser can discard content."""
    headers = []
    hasSequence = False
    for lineNumber, rawLine in enumerate(StringIO(text, newline=None), start=1):
        line = rawLine.removesuffix("\n")
        if not line.strip():
            continue
        if line.startswith(">"):
            if headers and not hasSequence:
                raise ValueError(f"Empty FASTA record {len(headers)} before line {lineNumber}.")
            ordinal = len(headers) + 1
            if ordinal > maxRecords:
                raise ValueError(f"FASTA record {ordinal} exceeds the limit of {maxRecords} records.")
            if not line[1:].strip():
                raise ValueError(f"Blank FASTA record {ordinal} header at line {lineNumber}.")
            if "\x00" in line:
                raise ValueError(f"FASTA record {ordinal} header contains a NUL at line {lineNumber}.")
            headers.append(line[1:])
            hasSequence = False
        else:
            if not headers:
                raise ValueError(f"Expected a FASTA header starting with > at line {lineNumber}.")
            if any(character.isspace() for character in line):
                raise ValueError(
                    f"FASTA record {len(headers)} has whitespace inside sequence line {lineNumber}."
                )
            if not line.isascii() or not all(
                character.isalpha() or character == "*" for character in line
            ):
                raise ValueError(
                    f"FASTA record {len(headers)} has an invalid sequence line at line {lineNumber}."
                )
            hasSequence = True
    if not headers:
        raise ValueError("The file contains no FASTA records.")
    if not hasSequence:
        raise ValueError(f"The final FASTA record {len(headers)} has no sequence.")
    return tuple(headers)


def _inferFastaMoleculeType(sequence: str) -> MoleculeType:
    """Choose a display interpretation; nucleotide-compatible letters remain ambiguous."""
    symbols = set(sequence.upper())
    if symbols.issubset(ambiguous_dna_letters):
        return "DNA"
    if symbols.issubset(ambiguous_rna_letters):
        return "RNA"
    if symbols.issubset(ambiguous_dna_letters + ambiguous_rna_letters):
        raise ValueError(
            "Cannot infer a molecule type for nucleotide-only FASTA containing both T and U."
        )
    return "protein"


def _readFastaBytes(
    sourcePath: Path, resolvedPath: Path, raw: bytes, moleculeType: MoleculeType | None,
    maxRecords: int = MAX_RECORDS,
) -> SequenceImportResult:
    """Parse the already captured source bytes without reopening the file."""
    _validateRecordLimit(maxRecords)
    text = _decode(raw, "plain FASTA")
    headers = _validateFastaStructure(text, maxRecords)
    source = SourceArtifact.fromCapture(sourcePath.name, raw)
    suffix = sourcePath.suffix.lower()
    hintedType = _FASTA_MOLECULE_HINTS.get(suffix)
    records = []
    for index, record in enumerate(SeqIO.parse(StringIO(text, newline=None), "fasta")):
        if index >= len(headers):
            raise ValueError("The FASTA parser changed the validated record count.")
        # SeqIO trims trailing header whitespace; the captured header is authoritative here.
        record.description = headers[index]
        try:
            recordType = moleculeType
            if recordType is None:
                recordType = hintedType or _inferFastaMoleculeType(str(record.seq))
            records.append(SequenceRevision.fromSeqRecord(
                record, moleculeType=recordType, sourceArtifactId=source.artifactId,
                sourceOrdinal=index + 1, sourcePath=resolvedPath, sourceFormat="fasta",
            ))
        except ValueError as error:
            raise ValueError(f"FASTA record {index + 1}: {error}") from error
    if len(records) != len(headers):
        raise ValueError("The FASTA parser did not preserve the validated record count.")
    hasNucleicRecords = any(record.moleculeType != "protein" for record in records)
    warnings = ["FASTA contains no structured features or construct history."]
    if moleculeType is None:
        if hintedType is not None:
            warnings.append(
                f"FASTA has no declared molecule type; {hintedType} was inferred "
                f"from the {suffix} filename hint."
            )
        else:
            warnings.append(
                "FASTA has no declared molecule type; types were inferred per record "
                "from sequence letters."
            )
        if hasNucleicRecords:
            warnings.append(
                "Nucleotide-compatible FASTA letters are ambiguous and may also represent proteins."
            )
    else:
        warnings.append(f"Caller-selected {moleculeType} interpretation applies to every FASTA record.")
    if hasNucleicRecords:
        warnings.append("Topology and single/duplex strand state remain unknown.")
    if any(count > 1 for count in Counter(record.sourceId for record in records).values()):
        warnings.append("Repeated external identifiers were kept as distinct internal records.")
    if suffix not in FASTA_EXTENSIONS:
        warnings.append("FASTA content was read from a nonstandard filename extension.")
    interpretation = "automatic" if moleculeType is None else f"chosen:{moleculeType}"
    return _importResult(
        records, source, resolvedPath, warnings, "fasta", raw, sourcePath, interpretation,
    )


def readFasta(
    path: str | Path, *, moleculeType: MoleculeType | None = None,
    maxBytes: int = MAX_FILE_BYTES, maxRecords: int = MAX_RECORDS,
) -> SequenceImportResult:
    """Read one or more plain, unaligned FASTA records through Biopython SeqIO.

    Args:
        path: UTF-8 FASTA file. Extensions are hints; content is validated.
        moleculeType: Optional DNA/RNA/protein interpretation for every record.
            Otherwise .faa hints protein and .frn hints RNA; generic FASTA records
            are inferred independently from their letters. DNA-compatible letters
            default to DNA, U without T indicates RNA, and other supported letters
            indicate protein. Nucleotide-compatible inferences remain ambiguous.

    Returns:
        Isolated snapshots, original bytes and SHA-256, and missing-metadata warnings.
        Full headers, record order and letter case are retained. The source file
        is not changed, archived or registered in a project database.

    Raises:
        OSError: The input cannot be read.
        ValueError: Encoding, FASTA structure or chosen alphabet is invalid,
            or automatic interpretation encounters nucleotide-only mixed T/U.
            Blank records, comments, aligned gaps and sequence-line whitespace
            are unsupported. No partial result is returned on invalid input.
    """
    return _readFastaBytes(*_captureSource(path, maxBytes), moleculeType, maxRecords)


def _genbankFrames(text: str, maxRecords: int) -> Iterator[list[str]]:
    """Find complete records without SeqIO's permissive search for the next LOCUS."""
    lines = []
    ordinal = 1
    for lineNumber, rawLine in enumerate(StringIO(text, newline=None), start=1):
        line = rawLine.rstrip("\n")
        isLocus = re.match(r"LOCUS(?:[ \t]|$)", line) is not None
        if not lines:
            if not line.strip():
                continue
            if not isLocus:
                raise ValueError(
                    f"GenBank record {ordinal}: expected LOCUS, found unexpected content "
                    f"at line {lineNumber}."
                )
            if ordinal > maxRecords:
                raise ValueError(
                    f"GenBank record {ordinal} exceeds the limit of {maxRecords} records."
                )
        elif isLocus:
            raise ValueError(f"GenBank record {ordinal}: missing // terminator before the next LOCUS.")
        if "\x00" in line:
            raise ValueError(f"GenBank record {ordinal}: unexpected NUL at line {lineNumber}.")
        lines.append(line)
        if line.rstrip() == "//":
            yield lines
            lines = []
            ordinal += 1
    if lines:
        raise ValueError(f"GenBank record {ordinal}: missing // terminator.")
    if ordinal == 1:
        raise ValueError("The file contains no GenBank records.")


def _genbankLocus(line: str) -> tuple[str, int, str, str | None, str | None, str | None]:
    match = _GENBANK_LOCUS.fullmatch(line)
    if match is None:
        raise ValueError("Malformed LOCUS or unsupported sequence units. GenBank requires DNA in bp.")
    name, lengthText, declarationFields = match.groups()
    length = int(lengthText)
    if not length:
        raise ValueError("LOCUS declares an empty sequence.")
    fields = (declarationFields or "").split()
    if not fields or fields[0] not in ("DNA", "ds-DNA"):
        raise ValueError("LOCUS requires a supported molecule declaration of DNA or ds-DNA.")
    declaration = fields.pop(0)
    date = fields.pop() if fields and _GENBANK_DATE.fullmatch(fields[-1]) else None
    division = fields.pop() if fields and fields[-1] in _GENBANK_DIVISIONS else None
    if division and any(
        line[unitStart:unitStart + 4] == " bp "
        and line[topologyStart:topologyStart + 3] == division
        and line[topologyStart + 3:].strip() == (date or "")
        for unitStart, topologyStart in ((29, 42), (40, 55))
    ):
        raise ValueError(f"LOCUS has an invalid explicit topology: {division!r}.")
    if fields not in ([], ["linear"], ["circular"]):
        raise ValueError(f"LOCUS has an invalid explicit topology or unrecognized fields: {' '.join(fields)!r}.")
    return name, length, declaration, fields[0] if fields else None, division, date


def _genbankSections(lines: list[str]) -> tuple[list[str], slice, list[str]]:
    """Find the header fields, feature table slice and ORIGIN lines in one frame."""
    headers = []
    featureStart = None
    featureEnd = None
    for index, line in enumerate(lines[1:-1], start=1):
        key = line[:12].strip()
        if key == "CONTIG":
            raise ValueError("CONTIG specifies undefined sequence data. Defined ORIGIN bases are required.")
        if key == "ORIGIN":
            featureStart = index if featureStart is None else featureStart
            featureEnd = index if featureEnd is None else featureEnd
            return headers, slice(featureStart, featureEnd), lines[index + 1:-1]
        if line.strip().split() == ["FEATURES", "Location/Qualifiers"] or line.startswith(
            "FEATURES"
        ):
            if not line.startswith("FEATURES"):
                raise ValueError("Malformed indentation before the FEATURES table.")
            if featureStart is not None:
                raise ValueError("Duplicate FEATURES table.")
            featureStart = index
        elif featureStart is None:
            headers.append(line)
        elif featureEnd is not None:
            if line.strip():
                raise ValueError("Unexpected content after BASE COUNT before ORIGIN.")
        elif key == "BASE COUNT":
            featureEnd = index
            headers.append(line)
    raise ValueError("Defined ORIGIN bases are required.")


def _genbankOrigin(lines: list[str], length: int) -> str:
    chunks = []
    offset = 0
    for line in lines:
        match = _GENBANK_SEQUENCE_LINE.fullmatch(line)
        if match is None:
            raise ValueError("Invalid ORIGIN sequence line.")
        if int(match[1]) != offset + 1:
            raise ValueError(f"Invalid ORIGIN line coordinate. Expected {offset + 1}.")
        letters = match[2].replace(" ", "")
        chunks.append(letters)
        offset += len(letters)
    if offset != length:
        raise ValueError(f"LOCUS declared length {length} does not match {offset} ORIGIN bases.")
    return "".join(chunks)


def _genbankFeatureLines(lines: list[str]) -> Iterator[tuple[str, list[str]]]:
    if lines and lines[0].split() != ["FEATURES", "Location/Qualifiers"]:
        raise ValueError("Malformed FEATURES table header.")
    key = None
    payload = []
    ordinal = 1
    for line in lines[1:]:
        if not line.strip():
            continue
        if line.startswith(_GENBANK_FEATURE_INDENT) and key is not None:
            payload.append(line[21:].strip())
        elif line.startswith(" " * 5) and line[5:21].strip() and len(line) > 21:
            if key is not None:
                yield key, payload
                ordinal += 1
            key = line[5:21].strip()
            payload = [line[21:].strip()]
        else:
            raise ValueError(f"Feature {ordinal}: malformed feature table line.")
    if key is not None:
        yield key, payload


def _genbankQualifierLayout(lines: list[str]) -> list[str]:
    """Unfold quoted values where a physical line ends with an escaped quote."""
    parserLines = []
    pending = []
    for line in lines:
        payload = line[21:].strip() if line.startswith(_GENBANK_FEATURE_INDENT) else ""
        if pending and payload:
            pending.append(line)
            if payload.count('"') % 2:
                # Biopython can take the escaped pair for the quoted value's end.
                if any(part.rstrip().endswith('"') for part in pending[:-1]):
                    continuation = " ".join(part[21:].strip() for part in pending[1:])
                    parserLines.append(f"{pending[0].rstrip()} {continuation}")
                else:
                    parserLines.extend(pending)
                pending.clear()
            continue
        if not line.strip():
            if not pending:
                parserLines.append(line)
            continue
        parserLines.extend(pending)
        pending.clear()
        value = payload.partition("=")[2].lstrip()
        if payload.startswith("/") and value.startswith('"') and value.count('"') % 2:
            pending.append(line)
        else:
            parserLines.append(line)
    parserLines.extend(pending)
    return parserLines


def _positionBounds(position: int) -> tuple[int, ...]:
    if isinstance(position, OneOfPosition):
        return tuple(int(choice) for choice in position.position_choices)
    if isinstance(position, (BetweenPosition, WithinPosition)):
        if position._left > position._right:
            raise ValueError(f"Reversed uncertain location bounds {position}.")
        return (position._left, position._right)
    return (int(position),)


def _checkedGenbankLocation(text: str, length: int, topology: str | None) -> Location:
    expression = text
    if text.startswith("complement(") and text.endswith(")"):
        expression = text.removeprefix("complement(").removesuffix(")")
    compound = _GENBANK_COMPOUND_LOCATION.fullmatch(expression)
    if compound is None and _GENBANK_SIMPLE_LOCATION.fullmatch(expression) is None:
        raise ValueError(f"Malformed location {text!r}.")
    betweenBaseCount = 0
    for match in _GENBANK_BETWEEN_BASES.finditer(text):
        remote, leftText, rightText = match.groups()
        left, right = int(leftText), int(rightText)
        if remote and right == 1:
            raise ValueError(f"Cannot verify remote origin in between-base location {text!r}.")
        isAdjacent = 0 < left and right == left + 1 and (remote is not None or right <= length)
        isOrigin = remote is None and topology == "circular" and left == length and right == 1
        if not (isAdjacent or isOrigin):
            raise ValueError(f"Invalid between-base location {text!r}.")
        betweenBaseCount += 1
    with _captureBioWarnings(Bio.BiopythonParserWarning) as notices:
        location = Location.fromstring(text, length=length, circular=topology == "circular")
    expectedOperator = compound[1] if compound else None
    if notices or getattr(location, "operator", None) != expectedOperator:
        raise ValueError(f"Unsupported location semantics in {text!r}.")
    if sum(part.start == part.end for part in location.parts) != betweenBaseCount:
        raise ValueError(f"Location {text!r} has a reversed or empty range.")
    for part in location.parts:
        if part.start == part.end:
            continue
        starts, ends = _positionBounds(part.start), _positionBounds(part.end)
        if min(starts) < 0 or min(ends) < 1:
            raise ValueError(f"Location {text!r} uses a non-positive base coordinate.")
        isLocal = part.ref is None and part.ref_db is None
        if isLocal and (max(starts) >= length or max(ends) > length):
            raise ValueError(f"Local location {text!r} lies outside its {length} bases.")
    return location


def _genbankFeatures(
    lines: list[str], length: int, topology: str | None,
) -> Iterator[tuple[str, Location, list[tuple[str, str | None]]]]:
    scanner = GenBankScanner()
    for index, (key, linesForFeature) in enumerate(_genbankFeatureLines(lines), start=1):
        try:
            if _GENBANK_FEATURE_NAME.fullmatch(key) is None:
                raise ValueError(f"Invalid feature key {key!r}.")
            _, locationText, qualifiers = scanner.parse_feature(key, linesForFeature)
            location = _checkedGenbankLocation(
                re.sub(r"(?<=,)[ \t]+", "", locationText), length, topology,
            )
            for qualifier, value in qualifiers:
                if _GENBANK_FEATURE_NAME.fullmatch(qualifier) is None:
                    raise ValueError(f"Invalid qualifier key {qualifier!r}.")
                if value is not None and '"' in value and (
                    len(value) < 2 or value[0] != '"' or value[-1] != '"'
                    or '"' in value[1:-1].replace('""', '')
                ):
                    raise ValueError(f"Qualifier /{qualifier} has malformed double quotes.")
        except (ValueError, AssertionError, IndexError) as error:
            raise ValueError(f"Feature {index}: invalid location or qualifier. {error}") from error
        yield key, location, qualifiers


def _genbankQualifierFlags(
    parsed: dict[str, list[str]], raw: list[tuple[str, str | None]],
) -> None:
    """Restore repeated valueless qualifiers suppressed by the SeqIO consumer."""
    byKey: dict[str, list[bool]] = {}
    for key, value in raw:
        byKey.setdefault(key, []).append(value is None)
    for key, flags in byKey.items():
        if not any(flags[1:]):
            continue
        values = iter(parsed[key])
        parsed[key] = [
            "" if isFlag and index else next(values) for index, isFlag in enumerate(flags)
        ]


def _genbankRecord(lines: list[str], warnings: list[str]) -> SeqRecord:
    name, length, declaration, topology, division, date = _genbankLocus(lines[0])
    headers, featureSection, sequenceLines = _genbankSections(lines)
    fields, links = _genbankHeaders(headers)
    letters = _genbankOrigin(sequenceLines, length)
    featureLines = _genbankQualifierLayout(lines[featureSection])
    # The frame is a parser view. SourceArtifact and captureBytes retain the original bytes.
    lines[featureSection] = featureLines
    with _captureBioWarnings(Bio.BiopythonParserWarning) as notices:
        features = list(_genbankFeatures(featureLines, length, topology))
        try:
            record = SeqIO.read(StringIO("\n".join(lines) + "\n"), "genbank")
        except AttributeError as error:
            raise ValueError("The parser could not read the GenBank record header or features.") from error
    warnings.extend(str(notice.message) for notice in notices)
    if record.name != name or str(record.seq) != letters.upper():
        raise ValueError("The parser did not preserve the LOCUS name or ORIGIN letters.")
    if len(record.features) != len(features):
        raise ValueError("The parser changed the feature count.")
    for index, (feature, (key, location, qualifiers)) in enumerate(
        zip(record.features, features), start=1,
    ):
        if feature.type != key or feature.location != location:
            raise ValueError(f"Feature {index}: the parser changed its key or location semantics.")
        _genbankQualifierFlags(feature.qualifiers, qualifiers)
    _genbankMetadata(record, fields, links, warnings)
    for key, value in (
        ("molecule_type", declaration), ("topology", topology),
        ("data_file_division", division), ("date", date),
    ):
        if value is not None:
            record.annotations[key] = value
        else:
            record.annotations.pop(key, None)
    if letters != letters.upper():
        warnings.append("GenBank ORIGIN letters were normalized to uppercase.")
    return record


def _isGenbankIdentifier(value: object) -> bool:
    return (
        isinstance(value, str) and value.isascii() and value.isprintable()
        and not any(character.isspace() for character in value)
        and any(character.isalnum() for character in value)
        and ";" not in value and not value.startswith("GI:")
    )


def _genbankHeaders(headers: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    fields: dict[str, list[str]] = {}
    links = []
    key = ""
    section = ""
    citationIdentifiers: set[str] = set()
    for line in headers:
        if not line.strip():
            continue
        prefix = line[:12].rstrip(" ")
        field = prefix.lstrip(" ")
        value = line[12:].strip()
        if field:
            parent = ""
            if field in _GENBANK_REFERENCE_FIELDS:
                parent = "REFERENCE"
            elif field == "ORGANISM":
                parent = "SOURCE"
            if parent:
                if section != parent:
                    raise ValueError(f"GenBank header {field} requires a preceding {parent} field.")
                if len(prefix) - len(field) not in (2, 3):
                    raise ValueError(f"Invalid indentation for GenBank header {field}.")
            elif prefix == field and (
                field in _GENBANK_HEADER_FIELDS or field in _GENBANK_SOURCE_ONLY_FIELDS
            ):
                section = field
            else:
                raise ValueError(f"Unexpected content in the GenBank record header: {prefix!r}.")
            if field in _GENBANK_SINGLETON_HEADER_FIELDS and field in fields:
                raise ValueError(f"Duplicate GenBank {field} header.")
            if field == "VERSION":
                version = _GENBANK_VERSION.fullmatch(value)
                if version is None or not _isGenbankIdentifier(version[1]):
                    raise ValueError("Invalid GenBank VERSION. Expected an identifier and optional GI.")
            if field == "REFERENCE":
                if re.match(r"[1-9][0-9]*(?: +\(|$)", value) is None:
                    raise ValueError("Invalid GenBank REFERENCE header. Expected a citation number.")
                citationIdentifiers.clear()
            elif field in ("MEDLINE", "PUBMED"):
                if field in citationIdentifiers:
                    raise ValueError(f"Duplicate GenBank {field} identifier in the same REFERENCE.")
                citationIdentifiers.add(field)
            key = field
        elif not key:
            raise ValueError("Unexpected GenBank header continuation before the first field.")
        elif key == "VERSION":
            raise ValueError("GenBank VERSION must fit on one line.")
        if key in ("MEDLINE", "PUBMED") and (
            len(value.split()) > 1 or (not field and fields[key][-1] and value)
        ):
            raise ValueError(f"GenBank {key} must contain a single citation identifier.")
        fields.setdefault(key, []).append(value)
        if key == "DBLINK":
            if field or ":" in value:
                links.append(re.sub(r": +", ":", value))
            elif value:
                links[-1] += " " + value
        elif key == "PROJECT":
            links.extend(value.replace("GenomeProject:", "Project:").split())
    return fields, links


def _genbankMetadata(
    record: SeqRecord, fields: dict[str, list[str]], links: list[str], warnings: list[str],
) -> None:
    if "structured_comment" in record.annotations:
        # Biopython accumulates parsed sections in a defaultdict. Snapshots store their entries.
        record.annotations["structured_comment"] = dict(record.annotations["structured_comment"])
    omitted = sorted(fields.keys() & _GENBANK_SOURCE_ONLY_FIELDS)
    if omitted:
        warnings.append(f"GenBank metadata fields outside the snapshot: {', '.join(omitted)}.")
    if definition := fields.get("DEFINITION"):
        record.description = " ".join(definition)
        if len(definition) > 1:
            warnings.append("GenBank definition text was unfolded across lines.")
    version = " ".join(fields.get("VERSION", ())).split()
    if version:
        record.id = record.annotations["genbank_version"] = version[0]
    if "ACCESSION" in fields:
        record.annotations["accessions"] = " ".join(fields["ACCESSION"]).split()
    else:
        record.annotations.pop("accessions", None)
    if links:
        record.dbxrefs = links
        warnings.append("GenBank cross-reference separators are normalized.")
    if "REFERENCE" in fields:
        warnings.append("GenBank reference text is unfolded; source numbering is retained by order.")
        if any(re.search(r"\(\s*(?:sites|bases)\s*\)", text) for text in fields["REFERENCE"]):
            warnings.append("A site or unspecified-base citation scope remains in the source.")


def _readGenbankBytes(
    sourcePath: Path, resolvedPath: Path, raw: bytes, maxRecords: int = MAX_RECORDS,
) -> SequenceImportResult:
    _validateRecordLimit(maxRecords)
    source = SourceArtifact.fromCapture(sourcePath.name, raw)
    records = []
    warnings = []
    for ordinal, lines in enumerate(_genbankFrames(_decode(raw, "GenBank"), maxRecords), start=1):
        recordWarnings: list[str] = []
        try:
            record = _genbankRecord(lines, recordWarnings)
            revision = SequenceRevision.fromSeqRecord(
                record, moleculeType="DNA", sourceArtifactId=source.artifactId,
                sourceOrdinal=ordinal, sourcePath=resolvedPath, sourceFormat="genbank",
            )
        except (ValueError, AssertionError, IndexError, UndefinedSequenceError) as error:
            raise ValueError(f"GenBank record {ordinal}: {error}") from error
        records.append(revision)
        for feature in revision.features:
            if revision.hasUnverifiedFeatureTraversal(feature):
                recordWarnings.append(f"Feature {feature.label!r}: {UNVERIFIED_TRAVERSAL_WARNING}")
        warnings.extend(f"GenBank record {ordinal}: {item}" for item in dict.fromkeys(recordWarnings))
    if sourcePath.suffix.lower() not in GENBANK_EXTENSIONS:
        warnings.append("GenBank content was read from a nonstandard filename extension.")
    return _importResult(records, source, resolvedPath, warnings, "genbank", raw, sourcePath)


def readGenbank(
    path: str | Path, *, maxBytes: int = MAX_FILE_BYTES, maxRecords: int = MAX_RECORDS,
) -> SequenceImportResult:
    """Read fully defined GenBank DNA in file order and retain the exact source bytes.

    Malformed records, unsupported declarations, or a breached limit raise ValueError
    for the entire intake. An unreadable path raises OSError.
    """
    return _readGenbankBytes(*_captureSource(path, maxBytes), maxRecords)


def _snapgenePacketTypes(raw: bytes) -> Counter[int]:
    """Check SnapGene packet headers and leave payload decoding to Biopython."""
    types = Counter()
    offset = 0
    while offset < len(raw):
        if len(raw) - offset < 5:
            raise ValueError("Invalid SnapGene file: incomplete packet header.")
        packetType = raw[offset]
        size = int.from_bytes(raw[offset + 1:offset + 5], "big")
        offset += 5
        if size > len(raw) - offset:
            raise ValueError("Invalid SnapGene file: truncated packet payload.")
        types[packetType] += 1
        offset += size
    return types


def _readSnapgeneBytes(sourcePath: Path, resolvedPath: Path, raw: bytes) -> SequenceImportResult:
    """Read the SnapGene record with Biopython and report fields it does not preserve."""
    packetTypes = _snapgenePacketTypes(raw)
    if packetTypes[0x09] != 1 or packetTypes[0x00] != 1:
        raise ValueError("Invalid SnapGene file: expected exactly one cookie and one DNA packet.")
    try:
        with _captureBioWarnings(Bio.BiopythonParserWarning) as notices:
            record = SeqIO.read(BytesIO(raw), "snapgene")
    except (ValueError, UnicodeError, ExpatError, StructError, TypeError, AttributeError,
            IndexError, OverflowError) as error:
        raise ValueError(f"Invalid or unsupported SnapGene file: {error}") from error
    # Biopython validates the cookie layout but ignores its declared sequence kind.
    if int.from_bytes(raw[13:15], "big") != 1:
        raise ValueError("Unsupported SnapGene molecule type: this importer supports DNA only.")
    generatedLabels = []
    for field in ("id", "name", "description"):
        value = getattr(record, field)
        if not value or value.startswith("<unknown"):
            setattr(record, field, sourcePath.stem)
            generatedLabels.append(field)
    source = SourceArtifact.fromCapture(sourcePath.name, raw)
    revision = SequenceRevision.fromSeqRecord(
        record, moleculeType="DNA", sourceArtifactId=source.artifactId,
        sourceOrdinal=1, sourcePath=resolvedPath, sourceFormat="snapgene",
    )
    warnings = [
        "SnapGene import preserves the sequence, topology and annotations exposed by Biopython; "
        "it is not a lossless SnapGene round trip. Keep the original .dna file.",
        "SnapGene history, colors/display settings, full primer oligos and thermal settings "
        "are not represented by this importer. Primer-binding annotations are not primer records.",
        "Biopython may normalize annotation text, omit feature gap segments, and filter primer-binding sites according to "
        "the stored hybridization settings. Imported translations are annotations, not recalculated results.",
        "Single/duplex strand state and terminal chemistry remain unknown.",
    ]
    if generatedLabels:
        warnings.append(f"Generated filename-based SnapGene labels: {', '.join(generatedLabels)}.")
    warnings.extend(str(notice.message) for notice in notices)
    if any(revision.hasUnverifiedFeatureTraversal(feature) for feature in revision.features):
        warnings.append(
            "Reverse-strand compound features at the circular origin retain Biopython's "
            "part order; verify whole-feature biological extraction before downstream use."
        )
    unknown = sorted(set(packetTypes) - _SNAPGENE_PARSED_PACKETS)
    if unknown:
        summary = ", ".join(f"0x{kind:02X} ({packetTypes[kind]})" for kind in unknown)
        warnings.append(f"Unhandled SnapGene packet types/counts: {summary}. Contents remain in the source file.")
    if sourcePath.suffix.lower() not in SNAPGENE_EXTENSIONS:
        warnings.append("SnapGene content was read from a nonstandard filename extension.")
    return _importResult([revision], source, resolvedPath, warnings, "snapgene", raw, sourcePath)


def readSnapgene(path: str | Path, *, maxBytes: int = MAX_FILE_BYTES) -> SequenceImportResult:
    """Read supported SnapGene DNA content without modifying or converting the source.

    Args:
        path: Binary SnapGene file; content is validated by Biopython.

    Returns:
        One annotated DNA snapshot, source checksum and explicit fidelity warnings.

    Raises:
        OSError: The source cannot be read.
        ValueError: The source is malformed or contains unsupported record values.
    """
    return _readSnapgeneBytes(*_captureSource(path, maxBytes))


def _detectFormat(raw: bytes) -> SequenceFormat | None:
    if raw.startswith(_SNAPGENE_COOKIE):
        return "snapgene"
    start = raw.removeprefix(b"\xef\xbb\xbf").lstrip(b" \t\r\n")
    if _GENBANK_TEXT_MARKER.match(start):
        return "genbank"
    if start.startswith(b">"):
        return "fasta"
    return None


def _requireTextSource(sourcePath: Path | None, detectedFormat: SequenceFormat | None) -> None:
    suffix = sourcePath.suffix.lower() if sourcePath is not None else ""
    if detectedFormat or suffix in FASTA_EXTENSIONS + GENBANK_EXTENSIONS + SNAPGENE_EXTENSIONS:
        raise ValueError("Plain text intake rejects structured sequence content or structured filenames.")


def _prepareTextBytes(
    sourcePath: Path | None, resolvedPath: Path | None, raw: bytes, moleculeType: MoleculeType | None,
    maxLetters: int | None = None,
) -> tuple[SequenceImportResult, TextNormalization | None]:
    text = _decode(raw, "plain text", allowBom=False)
    if not text.isascii():
        raise ValueError("Plain text contains non-ASCII sequence or grouping symbols.")
    sequence = text.translate(str.maketrans("", "", " \t\r\n"))
    if maxLetters is not None and len(sequence) > maxLetters:
        raise ValueError(f"The viewer supports up to {maxLetters:,} total sequence letters.")
    label = "Untitled sequence"
    record = SeqRecord(Seq(sequence), id=label, name=label, description="")
    source = SourceArtifact.fromCapture(
        sourcePath.name if sourcePath is not None else "Pasted sequence", raw,
    )
    try:
        revision = SequenceRevision.fromSeqRecord(
            record, moleculeType=moleculeType, sourceArtifactId=source.artifactId,
            sourceOrdinal=1, sourcePath=resolvedPath, sourceFormat="text",
        )
    except ValueError as error:
        raise ValueError(f"Plain text record 1: {error}") from error
    grouping = text.count(" ") + text.count("\t")
    lineBreaks = text.count("\n") + text.count("\r") - text.count("\r\n")
    warnings = [
        f"Caller-selected {moleculeType} interpretation applies to the plain-text sequence.",
        "Plain text has no biological name, so a display label was generated.",
        "Plain text contains no structured features or construct history.",
    ]
    preview = None
    if grouping or lineBreaks:
        preview = TextNormalization(sequence, grouping, lineBreaks)
        warnings.append(
            f"Removed {grouping} ASCII grouping characters and {lineBreaks} line breaks."
        )
    if moleculeType != "protein":
        warnings.append("Topology and single/duplex strand state remain unknown.")
    if sourcePath is not None and sourcePath.suffix.lower() not in TEXT_EXTENSIONS:
        warnings.append("Plain text was read from a nonstandard filename extension.")
    return _importResult(
        [revision], source, resolvedPath, warnings, "text", raw, sourcePath,
        f"chosen:{moleculeType}",
    ), preview


def _readTextBytes(
    sourcePath: Path | None, resolvedPath: Path | None, raw: bytes, moleculeType: MoleculeType | None,
    confirmNormalization: Callable[[TextNormalization], bool] | None = None,
) -> SequenceImportResult:
    _requireTextSource(sourcePath, _detectFormat(raw))
    result, preview = _prepareTextBytes(sourcePath, resolvedPath, raw, moleculeType)
    if preview is not None:
        if confirmNormalization is None:
            raise ValueError("Plain text record 1: normalization requires confirmation.")
        if confirmNormalization(preview) is not True:
            raise ValueError("Plain text record 1: normalization was declined.")
    return result


def captureTextFile(
    path: Path, *, allowStructured: bool = False, moleculeType: MoleculeType | None = None,
) -> PlainTextSource | SequenceImportResult:
    """Read the input once and select its parser as the desktop Open action does."""
    sourcePath, resolvedPath, raw = _captureSource(path, MAX_FILE_BYTES)
    detected = _detectFormat(raw)
    if allowStructured and detected is not None:
        return _readStructuredBytes(
            sourcePath, resolvedPath, raw, detected, moleculeType, None, MAX_RECORDS,
        )
    _requireTextSource(sourcePath, detected)
    return PlainTextSource(sourcePath, resolvedPath, raw)


def readText(
    path: str | Path, *, moleculeType: MoleculeType,
    confirmNormalization: Callable[[TextNormalization], bool] | None = None,
    maxBytes: int = MAX_FILE_BYTES,
) -> SequenceImportResult:
    """Read one explicitly typed UTF-8 text sequence with no structured header.

    When normalization removes ASCII spaces, tabs or line breaks, the callback
    receives the preview and must return True for intake. Invalid or unconfirmed
    input raises ValueError. An unreadable path raises OSError.
    """
    return _readTextBytes(*_captureSource(path, maxBytes), moleculeType, confirmNormalization)


def readPastedText(
    text: str, *, moleculeType: MoleculeType,
    confirmNormalization: Callable[[TextNormalization], bool] | None = None,
    maxBytes: int = MAX_FILE_BYTES,
) -> SequenceImportResult:
    """Capture and read literal pasted text with the same confirmation as readText.

    The capture retains the original text as UTF-8 bytes. Pasted input has no path.
    """
    capture = capturePastedText(text, maxBytes=maxBytes)
    return _readTextBytes(None, None, capture.captureBytes, moleculeType, confirmNormalization)


def capturePastedText(text: str, *, maxBytes: int = MAX_FILE_BYTES) -> PlainTextSource:
    """Capture literal pasted text independently of any external file."""
    _validateByteLimit(maxBytes)
    _checkCaptureSize(len(text), maxBytes)
    if not text.isascii():
        raise ValueError("Plain text contains non-ASCII sequence or grouping symbols.")
    return PlainTextSource(None, None, text.encode("utf-8"))


def readSequence(
    path: str | Path, *, moleculeType: MoleculeType | None = None,
    formatName: SequenceFormat | None = None,
    confirmNormalization: Callable[[TextNormalization], bool] | None = None,
    maxBytes: int = MAX_FILE_BYTES, maxRecords: int = MAX_RECORDS,
) -> SequenceImportResult:
    """Open a sequence exchange file with one bounded byte capture.

    Args:
        path: Input filename. Content markers pick a structured parser, except a
            .dna suffix without a GenBank marker selects binary SnapGene.
        moleculeType: Explicit choice for text, or optional override for FASTA.
        formatName: Structured format hint when neither content nor .dna applies,
            or "text" to request literal text explicitly.
        confirmNormalization: Required to accept text with grouping or line breaks.
        maxBytes: Maximum source byte count, including metadata.
        maxRecords: Maximum number of records in the captured file.

    Returns:
        A validated batch and its exact captured bytes.

    Raises:
        OSError: The source cannot be read.
        ValueError: The input is unsupported, invalid or exceeds a limit.
    """
    if formatName not in (None, "fasta", "genbank", "snapgene", "text"):
        raise ValueError(f"Unsupported input format {formatName!r}.")
    _validateRecordLimit(maxRecords)
    sourcePath, resolvedPath, raw = _captureSource(path, maxBytes)
    if formatName == "text":
        return _readTextBytes(sourcePath, resolvedPath, raw, moleculeType, confirmNormalization)
    return _readStructuredBytes(
        sourcePath, resolvedPath, raw, _detectFormat(raw), moleculeType, formatName, maxRecords,
    )


def _readStructuredBytes(
    sourcePath: Path, resolvedPath: Path, raw: bytes, detected: SequenceFormat | None,
    moleculeType: MoleculeType | None, formatName: SequenceFormat | None, maxRecords: int,
) -> SequenceImportResult:
    suffix = sourcePath.suffix.lower()
    if detected == "genbank":
        return _readGenbankBytes(sourcePath, resolvedPath, raw, maxRecords)
    if detected == "snapgene" or suffix in SNAPGENE_EXTENSIONS:
        try:
            return _readSnapgeneBytes(sourcePath, resolvedPath, raw)
        except ValueError as error:
            raise ValueError(f"SnapGene record 1: {error}") from error
    if detected == "fasta" or formatName == "fasta":
        return _readFastaBytes(sourcePath, resolvedPath, raw, moleculeType, maxRecords)
    if formatName == "genbank" or suffix in GENBANK_EXTENSIONS and formatName is None:
        return _readGenbankBytes(sourcePath, resolvedPath, raw, maxRecords)
    if formatName == "snapgene":
        return _readSnapgeneBytes(sourcePath, resolvedPath, raw)
    if suffix in FASTA_EXTENSIONS:
        return _readFastaBytes(sourcePath, resolvedPath, raw, moleculeType, maxRecords)
    raise ValueError(
        "No supported sequence format was detected. Headerless text requires explicit text intake "
        "and a molecule type."
    )


ExportFormat = Literal["genbank", "fasta", "text"]
_EXPORT_EXTENSIONS = {
    "genbank": GENBANK_EXTENSIONS,
    "fasta": FASTA_EXTENSIONS,
    "text": TEXT_EXTENSIONS,
}
_GENBANK_CORE_ANNOTATIONS = {
    "accession", "accessions", "genbank_version", "sequence_version", "references",
    "molecule_type", "topology", "date", "data_file_division",
}


@dataclass(frozen=True, slots=True)
class SequenceExportResult:
    """An export report with the destination, format, warnings, and serialization details."""

    destinationPath: Path
    formatName: ExportFormat
    emittedName: str | None
    warnings: tuple[str, ...]
    serializationDetails: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _StagedSequenceExport:
    path: Path
    raw: bytes
    report: SequenceExportResult
    protectedPaths: tuple[Path, ...]
    destinationVersion: tuple[int, ...] | None


class ExportDestinationChangedError(ValueError):
    """The chosen destination appeared, disappeared or changed during export review."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"The export destination changed during review. Choose the file again: {path}")


def fileStatVersion(info: os.stat_result) -> tuple[int, ...]:
    """Identify the observed file and its content/metadata version."""
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def exportDestinationVersion(path: Path) -> tuple[int, ...] | None:
    """Capture the chosen file's identity or its absence without following symlinks."""
    try:
        return fileStatVersion(path.lstat())
    except FileNotFoundError:
        return None


def checkExportDestinationVersion(path: Path, expected: tuple[int, ...] | None) -> None:
    """Reject replacement of a destination that changed since it was chosen."""
    if exportDestinationVersion(path) != expected:
        raise ExportDestinationChangedError(path)


def publishStagedExport(stagedPath: Path, destination: Path, version: tuple[int, ...] | None) -> None:
    """Publish a validated file on the same filesystem using the approved destination state."""
    if version is None:
        try:
            os.link(stagedPath, destination)
        except FileExistsError as error:
            raise ExportDestinationChangedError(destination) from error
    else:
        checkExportDestinationVersion(destination, version)
        os.replace(stagedPath, destination)


def _exportDestination(path: str | Path) -> Path:
    requestedPath = Path(path)
    return requestedPath.parent.resolve() / requestedPath.name


def _exportFormat(path: Path, formatName: ExportFormat | None) -> ExportFormat:
    suffix = path.suffix.lower()
    for supported, extensions in _EXPORT_EXTENSIONS.items():
        if suffix in extensions and formatName in (None, supported):
            return supported
    raise ValueError(
        f"Export suffix {suffix or '(none)'!r} does not select the requested "
        f"format {formatName or '(automatic)'!r}. Use a GenBank, FASTA or .txt suffix."
    )


def checkExportDestination(destinationPath: Path, protectedPaths: tuple[Path, ...]) -> None:
    """Reject direct, symlink and hardlink aliases of protected inputs."""
    resolvedDestination = destinationPath.resolve()
    for protectedPath in protectedPaths:
        isProtected = resolvedDestination == protectedPath.resolve()
        if not isProtected:
            try:
                isProtected = destinationPath.samefile(protectedPath)
            except FileNotFoundError:
                pass
        if isProtected:
            raise ValueError(
                f"Export destination would overwrite a protected source or artifact: {protectedPath}"
            )
    if destinationPath.is_dir():
        raise IsADirectoryError(f"Export destination is a directory: {destinationPath}")


def _exportHeader(revision: SequenceRevision, header: str | None) -> str:
    if header is None:
        originalHeader = (revision.sourceFormat == "fasta" and revision.name == revision.sourceId
                          and not revision.features)
        header = revision.description if originalHeader else revision.name
    if (
        not isinstance(header, str) or not header.strip()
        or header.splitlines() != [header] or "\x00" in header
    ):
        raise ValueError("FASTA export requires a nonempty, single-line header.")
    return header


def _genbankLocusName(name: str) -> str:
    if (
        name == "<unknown name>" or not name.isascii()
        or not all(character.isprintable() for character in name)
    ):
        raise ValueError("GenBank export requires a printable, single-line ASCII LOCUS name.")
    return name.replace(" ", "_")


def _validateGenbankIdentifier(identifier: str, field: str) -> None:
    if not _isGenbankIdentifier(identifier):
        raise ValueError(f"GenBank export cannot represent {field} {identifier!r}.")


def _genbankAccessions(record: SeqRecord) -> tuple[str, ...]:
    accessions = record.annotations.get("accessions")
    if accessions is None:
        accession = record.annotations.get("accession")
        accessions = () if accession is None else (accession,)
    if not isinstance(accessions, (tuple, list)):
        raise ValueError("GenBank export requires a list or tuple of accession identifiers.")
    return tuple(accessions)


def _wrapGenbankAccessions(accessions: tuple[str, ...]) -> str:
    return "\n".join(wrap(
        " ".join(accessions), width=79, initial_indent="ACCESSION   ",
        subsequent_indent=" " * 12, break_long_words=False, break_on_hyphens=False,
    ))


class _GenbankExportWriter(GenBankWriter):
    def _wrap_location(self, location: str) -> str:
        # Biopython uses the local record length to emit N^1 even for remote
        # zero-width parts. With no remote topology or length, encode adjacency.
        location = re.sub(
            r":([1-9][0-9]*)\^1(?=[,)]|$)",
            lambda match: f":{match[1]}^{int(match[1]) + 1}",
            location,
        )
        return super()._wrap_location(location)


def _writeGenbank(handle: TextIO, revision: SequenceRevision) -> tuple[str, tuple[str, ...]]:
    record = revision.toSeqRecord(preserveTraversalEvidence=True)
    record.name = _genbankLocusName(record.name)
    if revision.topology is None:
        record.annotations.pop("topology", None)
    if record.description.splitlines() not in ([], [record.description]):
        raise ValueError("GenBank export requires a single-line record definition.")
    # Biopython adds the terminal delimiter unconditionally. Preserve existing punctuation.
    record.description = record.description.removesuffix(".")
    if record.id == "<unknown id>":
        record.id = record.name
    _validateGenbankIdentifier(record.id, "record identifier")
    accessions = _genbankAccessions(record)
    for accession in accessions:
        _validateGenbankIdentifier(accession, "ACCESSION")
    if accessions:
        record.annotations["accession"] = accessions[0]
    for ordinal, feature in enumerate(record.features, start=1):
        if feature.location is None:
            raise ValueError(f"GenBank feature {ordinal} has no representable location.")
        if any(part.strand not in (-1, 1) for part in feature.location.parts):
            raise ValueError(
                f"GenBank feature {ordinal} has unknown or unsupported strand orientation."
            )

    with _captureBioWarnings(Bio.BiopythonWarning) as capturedWarnings:
        if len(accessions) <= 1:
            _GenbankExportWriter(handle).write_file((record,))
        else:
            # Biopython emits only the primary accession. Extend that header while
            # retaining its VERSION and the entire remainder of the serialization.
            staged = StringIO()
            _GenbankExportWriter(staged).write_file((record,))
            primary = f"\nACCESSION   {accessions[0]}\n"
            written = staged.getvalue()
            if primary not in written:
                raise ValueError("GenBank export did not emit the expected primary accession.")
            handle.write(written.replace(primary, f"\n{_wrapGenbankAccessions(accessions)}\n", 1))
    return record.name, tuple(f"Biopython: {item.message}" for item in capturedWarnings)


def _writeStagedSequence(
    stagedPath: Path, revision: SequenceRevision, formatName: ExportFormat, header: str | None,
) -> tuple[str | None, tuple[str, ...]]:
    with stagedPath.open("w", encoding="utf-8", newline="\n") as handle:
        if formatName == "genbank":
            try:
                emittedName, warnings = _writeGenbank(handle, revision)
            except (AssertionError, AttributeError, TypeError) as error:
                raise ValueError(f"GenBank metadata cannot be serialized: {error}") from error
        elif formatName == "fasta":
            emittedName = _exportHeader(revision, header)
            warnings = ()
            handle.write(f">{emittedName}\n")
            sequence = revision.sequence
            for offset in range(0, len(sequence), 60):
                handle.write(sequence[offset:offset + 60] + "\n")
        else:
            emittedName, warnings = None, ()
            handle.write(revision.sequence)
        handle.flush()
        os.fsync(handle.fileno())
    return emittedName, warnings


def _singleStagedRecord(result: SequenceImportResult, label: str) -> SequenceRevision:
    if len(result.records) != 1:
        raise ValueError(f"Staged {label} export must reopen as exactly one record.")
    return result.records[0]


def _sequenceOnlyWarnings(
    revision: SequenceRevision, path: Path, formatName: ExportFormat,
) -> list[str]:
    label = "FASTA" if formatName == "fasta" else "Plain text"
    warnings = [
        f"{label} omits molecule declaration, topology, strand state, annotated features, "
        "publication references, record cross-references, complete primer records, "
        "physical ends and project history."
    ]
    if formatName == "text":
        warnings.append("Plain text omits the record name and header.")
        return warnings
    try:
        inferredType = _FASTA_MOLECULE_HINTS.get(path.suffix.lower()) or _inferFastaMoleculeType(
            revision.sequence
        )
    except ValueError as error:
        warnings.append(f"Ordinary Open would reject this FASTA: {error}")
    else:
        if inferredType != revision.moleculeType:
            warnings.append(
                f"Ordinary Open would infer {inferredType} from this FASTA. "
                f"Use an explicit {revision.moleculeType} interpretation to recover the selected type."
            )
    return warnings


def _qualifierValues(value: object) -> tuple[str, ...]:
    """Project the supported textual representation of a GenBank qualifier."""
    values = value if isinstance(value, (list, tuple)) else (value,)
    if not values or any(type(item) not in (str, int, type(None)) for item in values):
        raise ValueError("GenBank qualifiers require nonempty lists of strings, integers or flags.")
    return tuple("" if item is None else str(item) for item in values)


def _validateGenbankFeatures(
    original: SeqRecord, reopened: SeqRecord, warnings: list[str],
) -> None:
    if len(original.features) != len(reopened.features):
        raise ValueError("Staged GenBank export changed the number of features.")
    for ordinal, (before, after) in enumerate(zip(original.features, reopened.features), start=1):
        prior = canonicalFeature(before, len(original))
        emitted = canonicalFeature(after, len(reopened))
        if prior["type"] != emitted["type"] or prior["location"] != emitted["location"]:
            raise ValueError(
                f"Staged GenBank export changed feature {ordinal}'s key or location semantics."
            )
        try:
            qualifiers = [(key, _qualifierValues(value)) for key, value in before.qualifiers.items()]
        except ValueError as error:
            raise ValueError(f"GenBank feature {ordinal}: {error}") from error
        if qualifiers != [(key, tuple(value)) for key, value in after.qualifiers.items()]:
            raise ValueError(
                f"Staged GenBank export changed feature {ordinal}'s qualifiers or their order."
            )
        if prior["qualifiers"] != emitted["qualifiers"]:
            warnings.append(f"GenBank serializes feature {ordinal} qualifier values as text lists.")
        if before.id != after.id:
            warnings.append(f"GenBank omits feature {ordinal}'s separate identifier {before.id!r}.")


def _validateGenbankReferences(
    references: tuple[PublicationReference, ...],
    emittedReferences: tuple[PublicationReference, ...],
    warnings: list[str],
) -> None:
    if len(references) != len(emittedReferences):
        raise ValueError("Staged GenBank export changed the number of publication references.")
    for ordinal, (before, after) in enumerate(zip(references, emittedReferences), start=1):
        if len(before.ranges) > 1 and not after.ranges:
            for interval in before.ranges:
                warnings.append(
                    f"GenBank dropped publication reference {ordinal} range "
                    f"[{interval.start},{interval.end}) (zero-based, half-open)."
                )
            before = replace(before, ranges=())
        if before != after:
            raise ValueError(f"Staged GenBank export changed publication reference {ordinal}.")


def _describeGenbankMetadata(
    original: SeqRecord, reopened: SeqRecord, details: list[str], warnings: list[str],
) -> None:
    before = original.annotations
    after = reopened.annotations
    accessions = _genbankAccessions(original)
    emittedAccessions = _genbankAccessions(reopened)
    if accessions and accessions != emittedAccessions:
        raise ValueError("Staged GenBank export changed deposited accessions or their order.")
    if not accessions:
        details.append(f"Generated ACCESSION {', '.join(emittedAccessions)!r} for serialization.")
    version = before.get("genbank_version")
    emittedVersion = after.get("genbank_version", reopened.id)
    if not version:
        details.append(f"Generated VERSION {emittedVersion!r} for serialization.")
    elif version != emittedVersion:
        warnings.append(f"GenBank changed VERSION from {version!r} to {emittedVersion!r}.")
    if original.id != reopened.id:
        warnings.append(f"GenBank changed identifier from {original.id!r} to {reopened.id!r}.")
    if original.description in ("", "<unknown description>") and reopened.description == ".":
        details.append("GenBank generated placeholder DEFINITION '.' for serialization.")
    elif original.description != reopened.description:
        warnings.append(
            f"GenBank changed DEFINITION from {original.description!r} to {reopened.description!r}."
        )
    for key, label, placeholder in (
        ("date", "date", "01-JAN-1980"), ("data_file_division", "division", "UNK"),
    ):
        oldValue, newValue = before.get(key), after.get(key)
        if oldValue == newValue:
            continue
        if newValue == placeholder:
            details.append(f"GenBank generated placeholder {label} {newValue!r} for serialization.")
        else:
            details.append(f"GenBank serialized {label} {newValue!r} from {oldValue!r}.")
    for key, value in before.items():
        if key in _GENBANK_CORE_ANNOTATIONS or key in after and value == after[key]:
            continue
        if key in after:
            warnings.append(f"GenBank changed record annotation {key!r} during serialization.")
        else:
            warnings.append(f"GenBank omits record annotation {key!r}.")
    for key in ("source", "organism", "taxonomy", "keywords"):
        if key not in before and key in after:
            details.append(f"GenBank generated placeholder {key} {after[key]!r} for serialization.")


def _validateStagedGenbank(
    revision: SequenceRevision, stagedPath: Path, raw: bytes, emittedName: str,
    warnings: list[str], details: list[str],
) -> None:
    reopened = _singleStagedRecord(_readGenbankBytes(stagedPath, stagedPath, raw), "GenBank")
    if reopened.name != emittedName:
        raise ValueError("Staged GenBank export changed the emitted LOCUS name.")
    if revision.name != emittedName:
        warnings.append(f"GenBank uses LOCUS {emittedName!r} for record name {revision.name!r}.")
    if reopened.sequence != revision.sequence.upper():
        raise ValueError("Staged GenBank export changed the stored bases.")
    if reopened.sequence != revision.sequence:
        warnings.append("GenBank round trip normalizes case to uppercase and loses lowercase masking.")
    if reopened.topology != revision.topology:
        raise ValueError("Staged GenBank export changed topology.")
    declaration = revision.moleculeDeclaration
    if reopened.moleculeDeclaration != (declaration or revision.moleculeType):
        raise ValueError("Staged GenBank export changed the molecule declaration.")
    if declaration is None:
        details.append(f"GenBank generated the {reopened.moleculeDeclaration} molecule declaration.")
    originalRecord = revision.toSeqRecord(preserveTraversalEvidence=True)
    reopenedRecord = reopened.toSeqRecord()
    if revision.dbxrefs != reopened.dbxrefs:
        raise ValueError("Staged GenBank export changed the record cross-references or their order.")
    _validateGenbankFeatures(originalRecord, reopenedRecord, warnings)
    for feature in revision.features:
        if revision.hasUnverifiedFeatureTraversal(feature):
            warnings.append(
                f"Feature {feature.label!r}: {UNVERIFIED_TRAVERSAL_WARNING} "
                "The exported feature qualifier records this limitation."
            )
    _validateGenbankReferences(revision.references, reopened.references, warnings)
    _describeGenbankMetadata(originalRecord, reopenedRecord, details, warnings)
    warnings.append(
        "GenBank has no representation here for appearance beyond stored qualifiers, "
        "complete primer records, physical ends or project history."
    )


def _validateStagedExport(
    revision: SequenceRevision, destinationPath: Path, stagedPath: Path, raw: bytes,
    formatName: ExportFormat, emittedName: str | None, writerWarnings: tuple[str, ...],
) -> SequenceExportResult:
    warnings = list(writerWarnings)
    details = []
    if revision.duplexGeometry is not None:
        warnings.append(
            "Fragment export writes the full aligned reference in forward orientation, "
            "with coordinates on that reference. Strand spans, physical ends and terminal "
            "chemistry are omitted."
        )
    if formatName == "genbank":
        _validateStagedGenbank(revision, stagedPath, raw, emittedName, warnings, details)
    else:
        if formatName == "fasta":
            result = _readFastaBytes(stagedPath, stagedPath, raw, revision.moleculeType)
            reopened = _singleStagedRecord(result, "FASTA")
            if reopened.description != emittedName:
                raise ValueError("Staged FASTA export changed the requested header.")
        else:
            result = _readTextBytes(stagedPath, stagedPath, raw, revision.moleculeType)
            if raw != revision.sequence.encode("ascii"):
                raise ValueError("Staged plain-text export must contain only the stored sequence.")
            reopened = _singleStagedRecord(result, formatName)
        if reopened.sequence != revision.sequence:
            raise ValueError(f"Staged {formatName} export changed the stored sequence.")
        warnings.extend(_sequenceOnlyWarnings(revision, destinationPath, formatName))
    return SequenceExportResult(
        destinationPath, formatName, emittedName, tuple(warnings), tuple(details),
    )


@contextmanager
def _stageSequenceExport(
    revision: SequenceRevision,
    path: str | Path,
    *,
    formatName: ExportFormat | None = None,
    header: str | None = None,
    confirm: Callable[[SequenceExportResult], bool] | None = None,
    protectedPaths: Iterable[str | Path] = (),
) -> Iterator[_StagedSequenceExport | None]:
    """Stage bytes for a file or project export, and ask for approval when required."""
    destinationPath = _exportDestination(path)
    selectedFormat = _exportFormat(destinationPath, formatName)
    if header is not None and selectedFormat != "fasta":
        raise ValueError("An explicit export header applies only to FASTA.")
    if selectedFormat == "genbank" and revision.moleculeType != "DNA":
        raise ValueError("GenBank export requires a defined DNA revision.")
    hintedType = _FASTA_MOLECULE_HINTS.get(destinationPath.suffix.lower())
    if hintedType is not None and hintedType != revision.moleculeType:
        raise ValueError(f"FASTA suffix {destinationPath.suffix!r} requires {hintedType}.")
    protected = tuple(Path(item) for item in protectedPaths)
    if revision.sourcePath is not None:
        protected += (Path(revision.sourcePath),)
    checkExportDestination(destinationPath, protected)
    destinationVersion = exportDestinationVersion(destinationPath)

    with TemporaryDirectory(prefix=".strandfolio-export-", dir=destinationPath.parent) as directory:
        stagedPath = Path(directory) / destinationPath.name
        emittedName, writerWarnings = _writeStagedSequence(stagedPath, revision, selectedFormat, header)
        raw = _capture(stagedPath, MAX_FILE_BYTES)
        report = _validateStagedExport(
            revision, destinationPath, stagedPath, raw, selectedFormat, emittedName, writerWarnings,
        )
        if confirm is None:
            if report.warnings or report.serializationDetails:
                raise ValueError(
                    "Export requires a confirmation callback to review the staged fidelity report."
                )
        elif confirm(report) is not True:
            yield None
            return
        checkExportDestination(destinationPath, protected)
        yield _StagedSequenceExport(stagedPath, raw, report, protected, destinationVersion)


def writeSequence(
    revision: SequenceRevision,
    path: str | Path,
    *,
    formatName: ExportFormat | None = None,
    header: str | None = None,
    confirm: Callable[[SequenceExportResult], bool] | None = None,
    protectedPaths: Iterable[str | Path] = (),
) -> SequenceExportResult | None:
    """Validate and publish one exchange file without mutating the input revision.

    The destination suffix selects GenBank, FASTA or text unless formatName is
    supplied, in which case they must agree. A supplied header applies to FASTA.
    The source path and any caller-supplied managed paths are protected from export.
    The confirmation callback sees the staged fidelity report. Returning False
    cancels and returns None. A callback is required when the report lists losses
    or serialization details. Cancellation and errors before replacement preserve
    prior destination bytes.
    """
    with _stageSequenceExport(
        revision, path, formatName=formatName, header=header, confirm=confirm,
        protectedPaths=protectedPaths,
    ) as staged:
        if staged is None:
            return None
        publishStagedExport(staged.path, staged.report.destinationPath, staged.destinationVersion)
        return staged.report
