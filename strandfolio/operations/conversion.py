"""Convert sequence selections for copying and map translated codons to their source positions."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from itertools import islice
import re
from typing import Literal

from Bio.Data import CodonTable
from Bio.Seq import Seq

from strandfolio.domain.coordinates import SelectionSegment, SequenceSelection
from strandfolio.domain.features import SequenceFeature
from strandfolio.domain.sequence import UNVERIFIED_TRAVERSAL_WARNING, MoleculeType, SequenceRevision


_DNA_COMPLEMENT_MAP = str.maketrans(
    "ACGTRYSWKMBDHVNacgtryswkmbdhvn",
    "TGCAYRSWMKVHDBNtgcayrswmkvhdbn",
)
_RNA_COMPLEMENT_MAP = str.maketrans(
    "ACGURYSWKMBDHVNacguryswkmbdhvn",
    "UGCAYRSWMKVHDBNugcayrswmkvhdbn",
)
_DNA_ALLOWED = frozenset("ACGTRYSWKMBDHVN")
_RNA_ALLOWED = frozenset("ACGURYSWKMBDHVN")
_TRANSCRIBE = str.maketrans("Tt", "Uu")
_BACK_TRANSCRIBE = str.maketrans("Uu", "Tt")
_CODE_DISPLAY_NAMES = {
    4: "Mold/Protozoan/Coelenterate Mitochondrial and Mycoplasma/Spiroplasma",
    11: "Bacterial, Archaeal and Plant Plastid",
}
_CODE_SEARCH_ALIASES = {
    3: ("Fungal mitochondrial",),
    4: ("Fungal mitochondrial",),
    11: ("Archaea", "Chloroplast"),
    12: ("Fungal nuclear",),
}

CopyFormat = Literal["stored", "complement", "reverse", "rna", "dna", "protein"]
TableSource = Literal["annotation", "standard", "document", "override"]


@dataclass(frozen=True, slots=True)
class GeneticCodePreference:
    """Choose a document preview table and whether it applies to annotated CDSs.

    An omitted table defaults to standard code 1 where a CDS has no explicit
    table. Retain unknown positive IDs so the preview can report their status.
    """

    tableId: int | None = None
    overrideAnnotated: bool = False

    def __post_init__(self) -> None:
        if self.tableId is not None and (type(self.tableId) is not int or self.tableId < 1):
            raise ValueError("A genetic code ID must be a positive integer.")
        if type(self.overrideAnnotated) is not bool:
            raise ValueError("The annotated CDS preview setting must be a boolean.")
        if self.overrideAnnotated and self.tableId is None:
            raise ValueError("Choose a genetic code before overriding annotated CDSs.")


DEFAULT_GENETIC_CODE = GeneticCodePreference()


@dataclass(frozen=True, slots=True)
class CdsTranslationSettings:
    """Translation settings from CDS qualifiers and document preferences."""

    codonStart: int | None
    tableId: int | None
    annotatedTable: int | None
    tableSource: TableSource
    problem: str | None


@dataclass(frozen=True, slots=True)
class GeneticCodeOption:
    """Describe an offline NCBI table and any implementation limit."""

    tableId: int
    name: str
    aliases: tuple[str, ...]
    unavailableReason: str | None


@dataclass(frozen=True, slots=True)
class CodonPlacement:
    """Map one translated codon to its exact three stored sequence positions."""

    codonIndex: int
    aminoAcid: str
    positions: tuple[int, int, int]
    centerPosition: int
    strand: int


@dataclass(frozen=True, slots=True)
class CdsTranslation:
    """Codon translation tied to a specific snapshot and feature ID.

    isValid means supported inputs produced at least one mapped codon. Warnings
    report incomplete codons, internal stops, ambiguity and imported-protein
    differences; this flag does not establish that the CDS is complete.
    """

    featureId: str
    label: str
    revisionId: str
    codonStart: int | None
    translTable: int | None
    strand: int | None
    proteinSequence: str
    codons: tuple[CodonPlacement, ...]
    trailingBases: int
    importedTranslation: str | None
    matchesImported: bool | None
    isValid: bool
    warnings: tuple[str, ...]
    annotatedTable: int | None
    tableSource: TableSource


@dataclass(frozen=True, slots=True)
class _FrameCodons(Sequence[CodonPlacement]):
    letters: str = field(repr=False)
    start: int
    size: int
    strand: int
    tableId: int
    moleculeType: MoleculeType

    def __len__(self) -> int:
        return self.size

    def __getitem__(self, index: int | slice) -> CodonPlacement | tuple[CodonPlacement, ...]:
        if isinstance(index, slice):
            return tuple(self._codonAt(position) for position in range(self.size)[index])
        return self._codonAt(range(self.size)[index])

    def _codonAt(self, index: int) -> CodonPlacement:
        first = self.start + self.strand * 3 * index
        center = first + self.strand
        last = center + self.strand
        codon = self.letters[first] + self.letters[center] + self.letters[last]
        if self.strand == -1:
            codon = codon.translate(
                _DNA_COMPLEMENT_MAP if self.moleculeType == "DNA" else _RNA_COMPLEMENT_MAP,
            )
        table = CodonTable.unambiguous_dna_by_id[self.tableId]
        return CodonPlacement(
            index, _translateCodon(codon, table, self.tableId),
            (first, center, last), center, self.strand,
        )

    def indicesInRange(self, start: int, end: int, *, offset: int) -> range:
        origin = self.start + offset * self.strand
        if self.strand == 1:
            first, stop = start - origin, end - origin
        else:
            first, stop = origin - end + 1, origin - start + 1
        # Round each boundary up to the next codon index and clip to this frame.
        return range(max(0, (first + 2) // 3), min(self.size, (stop + 2) // 3))


@dataclass(frozen=True, slots=True)
class FrameTranslation:
    """Map an ordinary whole-record reading frame to stored sequence positions.

    Signed frames ±1 to ±3 start at the corresponding 5′ end with offsets 0–2.
    Circular records use the stored linearization for full-record frames.
    """

    revisionId: str
    frame: int
    strand: int
    translTable: int
    tableSource: TableSource
    trailingBases: int
    warnings: tuple[str, ...]
    _codons: _FrameCodons | None = field(default=None, repr=False)

    @property
    def codons(self) -> Sequence[CodonPlacement]:
        """Return a lazily indexed, immutable sequence of mapped codons."""
        return self._codons if self._codons is not None else ()

    @property
    def proteinSequence(self) -> str:
        """Materialize the entire frame; use codonsInRange for bounded display."""
        return "".join(codon.aminoAcid for codon in self.codons)

    @property
    def isValid(self) -> bool:
        """Report whether this frame can produce at least one mapped codon."""
        return bool(self.codons)

    def codonsInRange(self, start: int, end: int) -> tuple[CodonPlacement, ...]:
        """Return placements centered in the stored half-open interval."""
        codons = self._codons
        if codons is None:
            return ()
        return tuple(codons._codonAt(index) for index in codons.indicesInRange(start, end, offset=1))


@dataclass(frozen=True, slots=True)
class ConversionPreview:
    """Carry text, output orientation and any translation used for copy."""

    text: str
    moleculeType: MoleculeType
    orientation: str
    translation: CdsTranslation | FrameTranslation | None = None
    codons: tuple[CodonPlacement, ...] = ()
    excludedBases: int = 0
    warnings: tuple[str, ...] = ()

    @property
    def isAvailable(self) -> bool:
        """Report whether this preview has text that can be copied."""
        return bool(self.text)


def _geneticCodeProblem(tableId: int) -> str | None:
    table = CodonTable.unambiguous_dna_by_id.get(tableId)
    if table is None:
        return f"Unsupported genetic code {tableId}."
    if set(table.stop_codons).intersection(table.forward_table):
        return f"Genetic code {tableId} has context-dependent stop codons and is unsupported."
    return None


def geneticCodeOptions() -> tuple[GeneticCodeOption, ...]:
    """List NCBI tables by ID from the installed Biopython data."""
    options = []
    for tableId, table in sorted(CodonTable.unambiguous_dna_by_id.items()):
        names = tuple(name for name in table.names if name)
        displayName = _CODE_DISPLAY_NAMES.get(
            tableId, ", ".join(name for name in names if not name.startswith("SGC")),
        )
        options.append(GeneticCodeOption(
            tableId, displayName, names + _CODE_SEARCH_ALIASES.get(tableId, ()),
            _geneticCodeProblem(tableId),
        ))
    return tuple(options)


def complementBases(sequence: str, moleculeType: MoleculeType) -> str:
    """Return the base-for-base complement in the same orientation and case.

    Args:
        sequence: Nucleic-acid string in stored 5'->3' order.
        moleculeType: Must be 'DNA' or 'RNA'; protein has no complement.

    Raises:
        ValueError: Protein input or unexpected sequence characters.
    """
    if moleculeType == "protein":
        raise ValueError("Protein sequences have no complementary strand.")
    if moleculeType == "DNA":
        invalid = set(sequence.upper()) - _DNA_ALLOWED
        if invalid:
            raise ValueError(f"Unsupported DNA symbols for complement: {sorted(invalid)!r}.")
        return sequence.translate(_DNA_COMPLEMENT_MAP)
    if moleculeType == "RNA":
        invalid = set(sequence.upper()) - _RNA_ALLOWED
        if invalid:
            raise ValueError(f"Unsupported RNA symbols for complement: {sorted(invalid)!r}.")
        return sequence.translate(_RNA_COMPLEMENT_MAP)
    raise ValueError(f"Unsupported molecule type: {moleculeType!r}.")


def reverseComplementBases(sequence: str, moleculeType: MoleculeType) -> str:
    """Return the 5'->3' reverse complement, preserving original letter case."""
    return complementBases(sequence, moleculeType)[::-1]


def extractSegmentsSequence(
    sequence: str | SequenceRevision,
    segments: Sequence[tuple[int, int]],
    *,
    strand: int = 1,
    moleculeType: MoleculeType = "DNA",
) -> str:
    """Extract and join ordered half-open segments in biological 5'->3' order."""
    if strand not in (1, -1):
        raise ValueError("Strand must be 1 or -1 for biological extraction.")
    length = len(sequence)
    chunks: list[str] = []
    for start, end in segments:
        if not 0 <= start <= end <= length:
            raise ValueError("Segment interval lies outside the sequence.")
        if isinstance(sequence, SequenceRevision):
            chunk = sequence.sequenceSlice(start, end)
        else:
            chunk = sequence[start:end]
        if strand == -1:
            chunk = reverseComplementBases(chunk, moleculeType)
        chunks.append(chunk)
    return "".join(chunks)


def _qualifierInt(
    qualifiers: dict[str, tuple[str, ...]],
    key: str,
    default: int | None,
) -> tuple[int | None, str | None]:
    if key not in qualifiers:
        return default, None
    values = qualifiers[key]
    if not values:
        return None, f"Invalid {key} qualifier: no value was supplied."
    numbers: set[int] = set()
    for value in values:
        text = value.strip()
        try:
            if re.fullmatch(r"[+-]?[0-9]+", text) is None:
                raise ValueError
            numbers.add(int(text))
        except ValueError:
            return None, f"Invalid {key} qualifier value: {value!r}."
    if len(numbers) != 1:
        return None, f"Conflicting {key} qualifier values: {', '.join(map(repr, values))}."
    return numbers.pop(), None


def _documentCode(preference: GeneticCodePreference) -> tuple[int, TableSource]:
    if preference.tableId is None:
        return 1, "standard"
    return preference.tableId, "document"


def _cdsCode(
    qualifiers: dict[str, tuple[str, ...]], preference: GeneticCodePreference,
) -> tuple[int | None, int | None, TableSource, str | None]:
    annotated, error = _qualifierInt(qualifiers, "transl_table", None)
    if error is not None:
        return None, None, "annotation", error
    if annotated is None:
        tableId, source = _documentCode(preference)
        return tableId, None, source, _geneticCodeProblem(tableId)
    error = _geneticCodeProblem(annotated)
    if error is not None:
        return annotated, annotated, "annotation", error
    preview = preference.tableId
    if preference.overrideAnnotated and preview is not None and preview != annotated:
        return preview, annotated, "override", _geneticCodeProblem(preview)
    return annotated, annotated, "annotation", None


def cdsTranslationSettings(
    qualifiers: dict[str, tuple[str, ...]], preference: GeneticCodePreference = DEFAULT_GENETIC_CODE,
) -> CdsTranslationSettings:
    """Resolve the coding offset, genetic code and unsupported recoding or partial annotations."""
    codonStart, startProblem = _qualifierInt(qualifiers, "codon_start", 1)
    tableId, annotated, source, problem = _cdsCode(qualifiers, preference)
    if startProblem is not None or codonStart not in (1, 2, 3):
        problem = startProblem or f"Unsupported codon_start={codonStart!r}; expected 1, 2, or 3."
    elif problem is None:
        problem = _cdsQualifierProblem(qualifiers)
    return CdsTranslationSettings(codonStart, tableId, annotated, source, problem)


def _translateCodon(codon: str, table: CodonTable.CodonTable, translTable: int) -> str:
    dnaCodon = codon.upper().replace("U", "T")
    if dnaCodon in table.forward_table:
        return table.forward_table[dnaCodon]
    if dnaCodon in table.stop_codons:
        return "*"
    return str(Seq(dnaCodon).translate(table=translTable))


def _translatePositions(
    letters: str, positions: Sequence[int], strand: int, tableId: int,
) -> tuple[str, tuple[CodonPlacement, ...], int]:
    """Use already strand-oriented letters indexed by stored reference positions."""
    table = CodonTable.unambiguous_dna_by_id[tableId]
    codons: list[CodonPlacement] = []
    trailing = len(positions) % 3
    for index, offset in enumerate(range(0, len(positions) - trailing, 3)):
        first, center, last = positions[offset:offset + 3]
        codon = letters[first] + letters[center] + letters[last]
        codons.append(CodonPlacement(
            index, _translateCodon(codon, table, tableId), (first, center, last), center, strand,
        ))
    return "".join(codon.aminoAcid for codon in codons), tuple(codons), trailing


def _residueWarnings(protein: str) -> list[str]:
    warnings: list[str] = []
    if "*" in protein[:-1]:
        warnings.append("Translation contains an internal stop codon (*).")
    if "X" in protein:
        warnings.append("Translation contains ambiguous codons (X).")
    return warnings


def _trailingWarning(trailing: int) -> str:
    return f"Incomplete trailing codon ({trailing} base(s) excluded)."


def _codingPositions(
    feature: SequenceFeature,
) -> tuple[list[int], int | None, str | None]:
    if not feature.parts:
        return [], feature.strand, "Feature has no location parts."
    if feature.operator not in (None, "join"):
        return (
            [], feature.strand,
            f"CDS compound location operator {feature.operator!r} is unsupported; expected 'join'.",
        )
    positions: list[int] = []
    strandsSeen: set[int] = set()
    for part in feature.parts:
        if not part.isSelectable or part.start is None or part.end is None:
            return [], feature.strand, "CDS contains uncertain or remote location parts."
        partStrand = part.strand if part.strand in (1, -1) else feature.strand
        if partStrand not in (1, -1):
            return [], feature.strand, "CDS strand must be +1 or -1 to align codons."
        strandsSeen.add(partStrand)
        if len(strandsSeen) > 1:
            return [], None, "CDS mixed strands are unsupported; joined parts must share a strand."
        if partStrand == 1:
            positions.extend(range(part.start, part.end))
        else:
            positions.extend(range(part.end - 1, part.start - 1, -1))
    effectiveStrand = next(iter(strandsSeen))
    return positions, effectiveStrand, None


def translateCdsFeature(
    revision: SequenceRevision,
    feature: SequenceFeature,
    preference: GeneticCodePreference = DEFAULT_GENETIC_CODE,
) -> CdsTranslation:
    """Compute a codon-aligned translation for an annotated CDS feature.

    The feature must come from this revision's immutable feature projections.
    Imported /translation qualifiers are retained for comparison only and never
    substituted for the recomputed sequence translation. A terminal stop remains
    in the codon mapping but may be omitted from the imported protein qualifier.
    Exact local parts joined with 'join' are supported; other compound operators
    and transl_except recoding return an invalid result with an explicit warning.
    Joined parts must share one known strand and follow a verified order.
    CDS fragments marked partial by digestion or PCR cannot be translated.
    Tables with context-dependent dual-coded stop codons are also unsupported.
    Codons use the selected table's ordinary amino-acid assignments; initiator
    methionine is not inferred for alternative start codons.
    The preference normally applies only when /transl_table is absent. An
    explicit override applies to a supported annotated code without editing it.

    Raises:
        ValueError: The feature is not a CDS belonging to the supplied revision.
    """
    if feature not in revision.features or feature.type != "CDS":
        raise ValueError("Choose a CDS feature belonging to the supplied sequence revision.")
    return _translateCdsFeature(revision, feature, preference)


def _translateCdsFeature(
    revision: SequenceRevision,
    feature: SequenceFeature,
    preference: GeneticCodePreference,
    *,
    complement: str | None = None,
) -> CdsTranslation:
    """Translate an owned CDS; bulk callers may share its precomputed complement."""
    qualifiers = dict(feature.qualifiers)
    rawImported = qualifiers.get("translation", ())
    imported = (rawImported[0].strip() or None) if rawImported else None
    settings = cdsTranslationSettings(qualifiers, preference)
    codonStart, tableId, problem = settings.codonStart, settings.tableId, settings.problem
    strand = feature.strand
    if revision.moleculeType == "protein":
        problem = "Protein records cannot be translated as CDS features."
    positions: list[int] = []
    if problem is None:
        if revision.hasUnverifiedFeatureTraversal(feature):
            problem = UNVERIFIED_TRAVERSAL_WARNING
        else:
            positions, strand, problem = _codingPositions(feature)
    if problem is not None:
        return CdsTranslation(
            feature.featureId, feature.label, revision.revisionId, codonStart,
            tableId, strand, "", (), 0, imported, None, False, (problem,),
            settings.annotatedTable, settings.tableSource,
        )

    assert codonStart is not None and tableId is not None and strand is not None
    letters = revision.sequence
    if strand == -1:
        letters = (
            complement if complement is not None else complementBases(letters, revision.moleculeType)
        )
    protein, codons, trailing = _translatePositions(letters, positions[codonStart - 1:], strand, tableId)
    warnings: list[str] = []
    if trailing:
        warnings.append(_trailingWarning(trailing))
    if not codons:
        warnings.append("CDS has fewer than three coding bases after codon_start.")
    warnings.extend(_residueWarnings(protein))

    matchesImported: bool | None = None
    if imported is not None:
        matchesImported = protein == imported or protein.removesuffix("*") == imported
        if not matchesImported:
            warnings.append("Computed translation differs from imported /translation qualifier.")

    return CdsTranslation(
        featureId=feature.featureId,
        label=feature.label,
        revisionId=revision.revisionId,
        codonStart=codonStart,
        translTable=tableId,
        strand=strand,
        proteinSequence=protein,
        codons=codons,
        trailingBases=trailing,
        importedTranslation=imported,
        matchesImported=matchesImported,
        isValid=bool(codons),
        warnings=tuple(warnings),
        annotatedTable=settings.annotatedTable,
        tableSource=settings.tableSource,
    )


def _cdsQualifierProblem(qualifiers: dict[str, tuple[str, ...]]) -> str | None:
    if "transl_except" in qualifiers:
        return "CDS transl_except recoding is unsupported."
    partialOperations = qualifiers.get("vibegene_partial", ())
    if "digest" in partialOperations or "pcr" in partialOperations:
        operation = "PCR" if "pcr" in partialOperations else "Digestion"
        return f"{operation} retained only part of this CDS. Translation is unavailable."
    return None


def translateAllCds(
    revision: SequenceRevision, preference: GeneticCodePreference = DEFAULT_GENETIC_CODE,
) -> tuple[CdsTranslation, ...]:
    """Translate every CDS in the snapshot and return its result, including any warnings."""
    if revision.moleculeType == "protein":
        return ()
    cdsFeatures = [feature for feature in revision.features if feature.type == "CDS"]
    if not cdsFeatures:
        return ()
    complement = complementBases(revision.sequence, revision.moleculeType)
    return tuple(
        _translateCdsFeature(revision, feature, preference, complement=complement)
        for feature in cdsFeatures
    )


def translateFrame(
    revision: SequenceRevision, frame: int, preference: GeneticCodePreference = DEFAULT_GENETIC_CODE,
) -> FrameTranslation:
    """Resolve a whole-record frame and calculate residues as positions are requested.

    Raises:
        ValueError: The signed frame is outside ±1 to ±3.
    """
    if type(frame) is not int or frame not in (-3, -2, -1, 1, 2, 3):
        raise ValueError("Choose a reading frame from +1 to +3 or −1 to −3.")
    strand = 1 if frame > 0 else -1
    tableId, source = _documentCode(preference)
    if revision.moleculeType == "protein":
        return FrameTranslation(revision.revisionId, frame, strand, tableId, source, 0,
                                ("Protein records cannot be translated as reading frames.",))
    problem = _geneticCodeProblem(tableId)
    if problem is not None:
        return FrameTranslation(revision.revisionId, frame, strand, tableId, source, 0, (problem,))

    offset = abs(frame) - 1
    letters = revision.sequence
    start = offset if strand == 1 else len(letters) - offset - 1
    complete, trailing = divmod(max(0, len(letters) - offset), 3)
    warnings: list[str] = []
    if trailing:
        warnings.append(_trailingWarning(trailing))
    if not complete:
        warnings.append("The frame contains no complete codons.")
    codons = _FrameCodons(letters, start, complete, strand, tableId, revision.moleculeType)
    return FrameTranslation(revision.revisionId, frame, strand, tableId, source, trailing,
                            tuple(warnings), codons)


def _segmentsForStrand(selection: SequenceSelection, strand: int) -> tuple[SelectionSegment, ...]:
    # Reverse feature parts already follow biological order. Other reversed
    # selections must flip both the segment order and the letters in each part.
    if strand == -1 and not (selection.featureId is not None and selection.strand == -1):
        return selection.segments[::-1]
    return selection.segments


def _selectedFrameCodons(
    frame: FrameTranslation, selection: SequenceSelection,
) -> Iterator[CodonPlacement]:
    codons = frame._codons
    if codons is None:
        return
    if not selection.isCompound:
        # Both endpoints (and hence the middle) must fall inside the interval.
        start, end = selection.segments[0]
        start, end = (start, end - 2) if frame.strand == 1 else (start + 2, end)
        for index in codons.indicesInRange(start, end, offset=0):
            yield codons._codonAt(index)
        return
    seen: set[int] = set()
    for start, end in _segmentsForStrand(selection, frame.strand):
        for index in codons.indicesInRange(start, end, offset=0):
            if index in seen:
                continue
            codon = codons._codonAt(index)
            if all(selection.contains(position) for position in codon.positions):
                seen.add(index)
                yield codon


def selectedTranslationCodons(
    translation: CdsTranslation | FrameTranslation, selection: SequenceSelection,
    *, limit: int | None = None,
) -> tuple[CodonPlacement, ...]:
    """Return complete selected codons, optionally taking only the first `limit`."""
    if translation.revisionId != selection.revisionId:
        raise ValueError("The translation does not belong to the selected revision.")
    if isinstance(translation, FrameTranslation):
        matches = _selectedFrameCodons(translation, selection)
    else:
        matches = (
            codon for codon in translation.codons
            if all(selection.contains(position) for position in codon.positions)
        )
    return tuple(matches if limit is None else islice(matches, limit))


def _proteinPreview(
    revision: SequenceRevision, selection: SequenceSelection,
    translation: CdsTranslation | FrameTranslation | None,
) -> ConversionPreview:
    if revision.moleculeType == "protein":
        return ConversionPreview("", "protein", "N → C", warnings=("Copy protein as stored.",))
    if translation is None:
        feature = revision.getFeature(selection.featureId) if selection.featureId else None
        if feature is not None and feature.type == "CDS":
            translation = translateCdsFeature(revision, feature)
        else:
            translation = translateFrame(revision, 1)
    if translation.revisionId != revision.revisionId:
        raise ValueError("The translation does not belong to the selected revision.")
    if not translation.isValid:
        return ConversionPreview(
            "", "protein", "N → C", translation,
            excludedBases=len(selection), warnings=translation.warnings,
        )

    codons = selectedTranslationCodons(translation, selection)
    if isinstance(translation, FrameTranslation) and not selection.isCompound:
        covered = 3 * len(codons)
    else:
        coveredPositions = {position for codon in codons for position in codon.positions}
        covered = sum(
            start <= position < end
            for start, end in selection.segments for position in coveredPositions
        )
    excluded = len(selection) - covered
    protein = "".join(codon.aminoAcid for codon in codons)
    warnings = _residueWarnings(protein)
    if excluded:
        warnings.append(
            f"Excluded {excluded:,} selected nt in partial codons or outside the coding region."
        )
    if not codons:
        warnings.append("The selection contains no complete codons in this translation.")
    return ConversionPreview(
        protein, "protein", "N → C", translation, codons, excluded, tuple(warnings),
    )


def previewConversion(
    revision: SequenceRevision, selection: SequenceSelection | None, copyFormat: CopyFormat,
    *, translation: CdsTranslation | FrameTranslation | None = None,
) -> ConversionPreview:
    """Project selected stored intervals as copy text without changing the revision.

    DNA↔RNA copy swaps T/U in stored order, retaining case and ambiguity.
    Protein copy uses the supplied revision-bound trace, or the linked CDS
    or default +1 frame, retaining only complete selected codons and stops.
    Reverse-complement copy of a whole feature requires verified part order.

    Raises:
        ValueError: The requested format is unknown, or selection/translation
            coordinates do not belong to the supplied revision.
    """
    if copyFormat not in ("stored", "complement", "reverse", "rna", "dna", "protein"):
        raise ValueError(f"Unknown sequence copy format: {copyFormat!r}.")
    moleculeType = revision.moleculeType
    if copyFormat == "rna":
        moleculeType = "RNA"
    elif copyFormat == "dna":
        moleculeType = "DNA"
    elif copyFormat == "protein":
        moleculeType = "protein"
    orientation = "N → C" if moleculeType == "protein" else "5′ → 3′"
    if copyFormat == "complement" and moleculeType != "protein":
        orientation = "3′ → 5′"
    if selection is not None and (
        selection.revisionId != revision.revisionId
        or any(end > len(revision) for _, end in selection.segments)
    ):
        raise ValueError("The selection does not belong to the supplied sequence revision.")
    if selection is None or not len(selection):
        return ConversionPreview(
            "", moleculeType, orientation, warnings=("Select a region to preview it.",),
        )
    if copyFormat == "protein":
        return _proteinPreview(revision, selection, translation)
    if copyFormat != "stored" and revision.moleculeType == "protein":
        return ConversionPreview(
            "", moleculeType, orientation, warnings=("This format requires DNA or RNA.",),
        )
    if copyFormat == "reverse":
        if (
            selection.wholeFeature and selection.featureId is not None
            and revision.hasUnverifiedFeatureTraversal(revision.getFeature(selection.featureId))
        ):
            return ConversionPreview(
                "", moleculeType, orientation, warnings=(UNVERIFIED_TRAVERSAL_WARNING,),
            )
        text = extractSegmentsSequence(
            revision, _segmentsForStrand(selection, -1), strand=-1, moleculeType=revision.moleculeType,
        )
    else:
        text = extractSegmentsSequence(revision, selection.segments)
        if copyFormat == "complement":
            text = complementBases(text, revision.moleculeType)
        elif copyFormat == "rna":
            text = text.translate(_TRANSCRIBE)
        elif copyFormat == "dna":
            text = text.translate(_BACK_TRANSCRIBE)
    return ConversionPreview(text, moleculeType, orientation)
