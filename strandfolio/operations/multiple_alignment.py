"""Run global multiple sequence alignments with Kalign and map columns to source coordinates."""

from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.metadata import version
from itertools import combinations
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from urllib.parse import quote

from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord

from strandfolio.operations.alignment import (
    MAX_ALIGNMENT_INPUT_LENGTH, AlignmentCanceled, AlignmentInput,
    alignmentSourcePositions, runAlignmentWorker,
)


KALIGN_VERSION = "4.0.0"
MULTIPLE_ALIGNMENT_ENGINE = f"Kalign {KALIGN_VERSION} · fast preset · 1 thread"
MAX_MULTIPLE_ALIGNMENT_ROWS = 9
MAX_MULTIPLE_ALIGNMENT_LENGTH = MAX_ALIGNMENT_INPUT_LENGTH
MAX_MULTIPLE_ALIGNMENT_SYMBOLS = 80_000
MAX_MULTIPLE_ALIGNMENT_WORK = 2_000_000_000
MULTIPLE_ALIGNMENT_TIMEOUT_SECONDS = 30
_ALPHABETS = {
    "DNA": frozenset("ACGTRYSWKMBDHVN"),
    "RNA": frozenset("ACGURYSWKMBDHVN"),
    "protein": frozenset("ACDEFGHIKLMNPQRSTVWYBZX"),
}


def _rowId(row: int) -> str:
    return "ref" if row == 0 else f"query{row}"


def _validateInputs(inputs: tuple[AlignmentInput, ...]) -> None:
    if not 3 <= len(inputs) <= MAX_MULTIPLE_ALIGNMENT_ROWS:
        raise ValueError("Choose one reference and 2–8 queries for a shared-column alignment.")
    if any(not isinstance(source, AlignmentInput) for source in inputs):
        raise ValueError("Shared-column alignment requires revision-bound sequence inputs.")
    moleculeType = inputs[0].moleculeType
    if moleculeType not in _ALPHABETS or any(source.moleculeType != moleculeType for source in inputs):
        raise ValueError("Choose shared-column inputs of the same molecule type (DNA, RNA or protein).")
    for row, source in enumerate(inputs):
        if not 1 <= len(source) <= MAX_MULTIPLE_ALIGNMENT_LENGTH:
            raise ValueError(
                f"Each shared-column input must contain 1–{MAX_MULTIPLE_ALIGNMENT_LENGTH:,} symbols."
            )
        if len(source.selection) != len(source) or any(
            start < 0 or end > source.sourceLength for start, end in source.selection.segments
        ):
            raise ValueError(f"The source coordinates of {_rowId(row)} do not match its sequence.")
        unsupported = set(source.sequence) - _ALPHABETS[moleculeType]
        if unsupported:
            symbols = ", ".join(sorted(unsupported))
            raise ValueError(
                f"Shared-column {moleculeType} row {_rowId(row)} contains unsupported symbols ({symbols})."
            )
    if sum(map(len, inputs)) > MAX_MULTIPLE_ALIGNMENT_SYMBOLS:
        raise ValueError(f"Select at most {MAX_MULTIPLE_ALIGNMENT_SYMBOLS:,} symbols across all rows.")
    work = sum((len(left) + 1) * (len(right) + 1) for left, right in combinations(inputs, 2))
    if work > MAX_MULTIPLE_ALIGNMENT_WORK:
        raise ValueError(
            f"These rows exceed the shared-column work limit of {MAX_MULTIPLE_ALIGNMENT_WORK:,} "
            "summed pairwise matrix cells. Choose shorter regions or fewer queries."
        )


@dataclass(frozen=True, slots=True)
class MultipleAlignmentResult:
    """The global alignment matrix and its mappings to the selected source revisions."""

    inputs: tuple[AlignmentInput, ...]
    alignedRows: tuple[str, ...]
    engineVersion: str = MULTIPLE_ALIGNMENT_ENGINE
    _positions: tuple[tuple[int | None, ...], ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.inputs or len(self.alignedRows) != len(self.inputs):
            raise ValueError("The shared-column matrix has the wrong number of rows.")
        width = self.columnCount
        if not 0 < width <= sum(map(len, self.inputs)) or any(len(row) != width for row in self.alignedRows):
            raise ValueError("The shared-column matrix has invalid row widths.")
        positions = []
        for source, aligned in zip(self.inputs, self.alignedRows, strict=True):
            if aligned.replace("-", "") != source.sequence:
                raise ValueError("A shared-column row does not recover its original ungapped symbols.")
            offsets = iter(alignmentSourcePositions(source))
            positions.append(tuple(None if symbol == "-" else next(offsets) for symbol in aligned))
        if any(all(symbol == "-" for symbol in col) for col in zip(*self.alignedRows, strict=True)):
            raise ValueError("The shared-column matrix contains an all-gap column.")
        object.__setattr__(self, "_positions", tuple(positions))

    @property
    def columnCount(self) -> int:
        """Number of shared columns, including columns that contain gaps in some rows."""
        return len(self.alignedRows[0])

    def sourcePosition(self, row: int, column: int) -> int | None:
        """Return the zero-based original source position, or None at a gap."""
        if not 0 <= row < len(self.inputs) or not 0 <= column < self.columnCount:
            raise IndexError("Choose a cell in the shared-column alignment.")
        return self._positions[row][column]

    def rowId(self, row: int) -> str:
        """Stable identifier used by the table and the corresponding aligned FASTA record."""
        if not 0 <= row < len(self.inputs):
            raise IndexError("Choose a row in the shared-column alignment.")
        return _rowId(row)

    def fastaText(self) -> str:
        """Return wrapped aligned FASTA with row IDs and source revision and feature metadata."""
        records = []
        for index, (source, aligned) in enumerate(zip(self.inputs, self.alignedRows, strict=True)):
            name = re.sub(r"[^\w.-]+", "_", source.name).strip("_") or "sequence"
            revision = re.sub(r"[^\w.-]+", "_", source.revisionId)
            description = f"{name} revision={revision}"
            if source.selection.featureId is not None:
                feature = re.sub(r"[^\w.-]+", "_", source.featureLabel or "").strip("_") or "feature"
                featureId = quote(source.selection.featureId, safe="")
                description += f" feature={feature} feature_id={featureId} strand={source.strand:+d}"
            records.append(SeqRecord(
                Seq(aligned), id=self.rowId(index), description=description,
            ).format("fasta"))
        return "".join(records)


def _writeMatrix() -> None:
    """Run the native extension only in its worker process and return the engine's named rows."""
    try:
        import kalign
    except ImportError as error:
        raise SystemExit(
            f"Install kalign-python=={KALIGN_VERSION} to enable shared-column alignment."
        ) from error
    installed = version("kalign-python")
    if installed != KALIGN_VERSION:
        raise SystemExit(
            f"Shared-column alignment requires kalign-python {KALIGN_VERSION}; found {installed}."
        )
    filename, sequenceType = json.load(sys.stdin)
    matrix = kalign.align_from_file(filename, seq_type=sequenceType, mode="fast", n_threads=1)
    json.dump(list(zip(matrix.names, matrix.sequences, strict=True)), sys.stdout)


def alignMultiple(
    inputs: tuple[AlignmentInput, ...], isCanceled: Callable[[], bool] | None = None,
) -> MultipleAlignmentResult:
    """Align 3–9 oriented DNA, RNA or protein rows and map gaps and residues to original positions.

    Kalign uses its fast preset for the explicit alphabet and one thread. Each
    input and the sum of pairwise matrix sizes are bounded before launching.
    Cancellation raises AlignmentCanceled. An invalid selection raises
    ValueError. A failed engine or unverifiable matrix raises RuntimeError.
    """
    inputs = tuple(inputs)
    if isCanceled is not None and isCanceled():
        raise AlignmentCanceled()
    _validateInputs(inputs)
    # The file API returns native row names. They distinguish even identical input
    # sequences. The parent owns the temporary input and removes it after a child is killed.
    with TemporaryDirectory(prefix="strandfolio-kalign-") as directory:
        fastaPath = Path(directory) / "inputs.fa"
        fastaPath.write_text("".join(
            f">{_rowId(row)}\n{source.sequence}\n" for row, source in enumerate(inputs)
        ), encoding="ascii")
        output = runAlignmentWorker(
            "shared-columns",
            (str(fastaPath), inputs[0].moleculeType.lower()), isCanceled,
            engineName="Kalign", timeoutSeconds=MULTIPLE_ALIGNMENT_TIMEOUT_SECONDS,
        )
    ids = tuple(_rowId(i) for i in range(len(inputs)))
    if not isinstance(output, list) or any(
        not isinstance(pair, list) or len(pair) != 2 or not all(isinstance(s, str) for s in pair)
        for pair in output
    ):
        raise RuntimeError("Kalign returned invalid row data.")
    byId = dict(output)
    if len(output) != len(inputs) or set(byId) != set(ids):
        raise RuntimeError("Kalign returned missing, duplicate or unknown row identifiers.")
    try:
        result = MultipleAlignmentResult(inputs, tuple(byId[key] for key in ids))
    except ValueError as error:
        raise RuntimeError(f"Kalign returned an invalid alignment. {error}") from error
    if isCanceled is not None and isCanceled():
        raise AlignmentCanceled()
    return result
