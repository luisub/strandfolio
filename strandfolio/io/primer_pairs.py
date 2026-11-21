"""Exchange two named, unmodified DNA oligos through ordinary nucleotide FASTA."""

from collections.abc import Callable
from dataclasses import dataclass
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from textwrap import wrap

from strandfolio.domain.primers import PrimerEntry, PrimerRevision
from strandfolio.io.formats import publishStagedExport, readFasta


_MARKER = "strandfolio:"
_METADATA = re.compile(r"strandfolio:v1 role=(forward|reverse) tail=(0|[1-9][0-9]{0,3})")


def _checkIdentifier(name: str) -> None:
    if not name or ">" in name or any(char.isspace() or not char.isprintable() for char in name):
        raise ValueError("Use a FASTA identifier without spaces, '>' or control characters.")


@dataclass(frozen=True, slots=True)
class PairRecord:
    """A FASTA oligo with a declared role, or an unassigned external record."""

    primer: PrimerRevision
    role: str | None


def readPrimerPair(path: Path) -> tuple[PairRecord, PairRecord]:
    """Validate both records before exposing any of the pair to the editor."""
    records = readFasta(path, moleculeType="DNA", maxRecords=2, maxBytes=64 * 1024).records
    if len(records) != 2 or records[0].sourceId == records[1].sourceId:
        raise ValueError("Choose two FASTA records with different identifiers.")
    parsed = []
    for record in records:
        _checkIdentifier(record.sourceId)
        role, tail = None, 0
        header = record.description.split(maxsplit=1)
        if len(header) == 2 and header[1].lower().startswith(_MARKER):
            metadata = _METADATA.fullmatch(header[1].rstrip())
            if metadata is None:
                raise ValueError("The Strandfolio pair header is incomplete or unsupported.")
            role, tail = metadata[1], int(metadata[2])
        sequence = record.sequence
        if tail >= len(sequence):
            raise ValueError(f"{record.sourceId}: A 5′ tail must leave at least one 3′ annealing base.")
        label = role.capitalize() if role else f"Record {record.sourceId}"
        entry = PrimerEntry(label, sequence[tail:], sequence[:tail])
        if entry.problem:
            raise ValueError(entry.problem)
        parsed.append(PairRecord(entry.toRevision(record.sourceId), role))
    roles = {item.role for item in parsed}
    if roles not in ({None}, {"forward", "reverse"}):
        raise ValueError("A marked pair needs one forward record and one reverse record.")
    return parsed[0], parsed[1]


def writePrimerPair(
    path: Path, names: tuple[str, str], entries: tuple[PrimerEntry, PrimerEntry],
    approvedVersion: tuple[int, ...] | None, checkDestination: Callable[[], None],
) -> None:
    """Stage both oligos, verify the chosen destination and publish the FASTA atomically."""
    from strandfolio.storage.projects import _syncDirectory, _syncFile

    if len(set(names)) != 2:
        raise ValueError("The two oligos need different FASTA identifiers.")
    lines = []
    for role, name, entry in zip(("forward", "reverse"), names, entries, strict=True):
        _checkIdentifier(name)
        if entry.problem:
            raise ValueError(entry.problem)
        lines.append(f">{name} strandfolio:v1 role={role} tail={len(entry.tail)}")
        lines.extend(wrap(entry.sequence, 80))
    checkDestination()
    with TemporaryDirectory(dir=path.parent, prefix=".strandfolio-primers-") as directory:
        staged = Path(directory) / "pair.fasta"
        with staged.open("wb") as handle:
            handle.write(("\n".join(lines) + "\n").encode("utf-8"))
            _syncFile(handle)
        verified = readPrimerPair(staged)
        for record, name, entry, role in zip(verified, names, entries, ("forward", "reverse"), strict=True):
            if (record.role, record.primer.name, record.primer.sequence, record.primer.tailLength) != (
                role, name, entry.sequence, len(entry.tail),
            ):
                raise ValueError("The saved FASTA differs from the reviewed primer pair.")
        checkDestination()
        publishStagedExport(staged, path, approvedVersion)
    if getattr(os, "O_DIRECTORY", None) is None:
        return
    try:
        _syncDirectory(path.parent)
    except OSError as error:
        raise OSError(
            f"The primer pair was written. The destination folder sync failed. {error}"
        ) from error
