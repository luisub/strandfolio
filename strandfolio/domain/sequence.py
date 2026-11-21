"""Isolated sequence snapshots with immutable views of imported annotations."""

from copy import copy, deepcopy
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal
from uuid import uuid4

from Bio.Seq import Seq, UndefinedSequenceError
from Bio.SeqRecord import SeqRecord

from strandfolio.domain.features import (
    ANNOTATION_CURATION_QUALIFIER,
    PublicationReference,
    SequenceFeature,
    canonicalFeature,
    canonicalMetadata,
    projectFeature,
    projectReference,
)
from strandfolio.domain.fragments import DuplexGeometry
from strandfolio.domain.provenance import IllustrationAttribution, OutputReference


MoleculeType = Literal["DNA", "RNA", "protein"]
StrandState = Literal["duplex", "unknown", "not_applicable"]

_CONTENT_SCHEMA = "vibegene.sequence.v3"
_TRAVERSAL_QUALIFIER = "vibegene_traversal"
_UNVERIFIED_ORIGIN = "unverified SnapGene reverse-origin part order"
UNVERIFIED_TRAVERSAL_WARNING = (
    "The source SnapGene annotation has unverified part order across the circular origin."
)

_ALPHABETS = {
    "DNA": frozenset("ACGTRYSWKMBDHVN"),
    "RNA": frozenset("ACGURYSWKMBDHVN"),
    "protein": frozenset("ACDEFGHIKLMNPQRSTVWYBXZJUO*"),
}


def _validateIdentities(
    recordId: str, revisionId: str, featureIds: tuple[str, ...], featureCount: int,
) -> None:
    if any(
        type(value) is not str or not value.strip() or "\x00" in value
        for value in (recordId, revisionId)
    ):
        raise ValueError("Stored record and revision identities must be nonblank strings.")
    if (
        type(featureIds) is not tuple
        or len(featureIds) != featureCount
        or any(type(value) is not str or not value.strip() or "\x00" in value for value in featureIds)
        or len(set(featureIds)) != len(featureIds)
    ):
        raise ValueError("Stored features must each have a unique nonblank identity.")


def _validateRecord(record: SeqRecord, moleculeType: MoleculeType) -> str:
    """Validate the sequence letters and record metadata supported by a snapshot."""
    if not isinstance(record, SeqRecord):
        raise TypeError("record must be a Biopython SeqRecord.")
    if not isinstance(moleculeType, str) or moleculeType not in _ALPHABETS:
        raise ValueError("Choose moleculeType='DNA', 'RNA', or 'protein' explicitly.")
    if record.letter_annotations:
        raise ValueError("Per-letter annotations are not supported yet.")
    if type(record.dbxrefs) not in (list, tuple) or any(
        type(value) is not str or not value.strip() for value in record.dbxrefs
    ):
        raise ValueError("Record cross-references must be an ordered collection of nonblank strings.")
    if not isinstance(record.annotations, dict):
        raise ValueError("Record annotations must be a dictionary.")
    declaration = record.annotations.get("molecule_type", moleculeType)
    if declaration != moleculeType and (moleculeType, declaration) != ("DNA", "ds-DNA"):
        raise ValueError("The supplied molecule type conflicts with the record metadata.")
    if record.annotations.get("topology") not in (None, "linear", "circular"):
        raise ValueError("Record topology must be 'linear', 'circular', or unspecified.")
    if not all(isinstance(value, str) for value in (record.id, record.name, record.description)):
        raise ValueError("Record ID, name and description must be strings.")
    if not record.id.strip() or not record.name.strip():
        raise ValueError("Record ID and name cannot be blank.")
    if record.seq is None:
        raise ValueError("A record must contain a defined, nonempty sequence.")
    try:
        sequence = str(record.seq)
    except UndefinedSequenceError as error:
        raise ValueError("Partially or wholly undefined sequence content is unsupported.") from error
    if not sequence:
        raise ValueError("A record must contain a defined, nonempty sequence.")
    invalid = set(sequence.upper()) - _ALPHABETS[moleculeType]
    if invalid:
        raise ValueError(f"Invalid {moleculeType} sequence symbols: {sorted(invalid)!r}.")
    return sequence


def _fingerprint(
    record: SeqRecord, moleculeType: MoleculeType, moleculeDeclaration: str | None,
    strandState: StrandState, references: tuple[PublicationReference, ...],
    duplexGeometry: DuplexGeometry | None,
) -> str:
    canonical = {
        "schema": _CONTENT_SCHEMA,
        "source_id": record.id,
        "name": record.name,
        "description": record.description,
        "sequence": str(record.seq),
        "molecule_type": moleculeType,
        "molecule_declaration": moleculeDeclaration,
        "strand_state": strandState,
        "annotations": canonicalMetadata({
            key: value for key, value in record.annotations.items() if key != "references"
        }),
        "references": [asdict(reference) for reference in references],
        "dbxrefs": list(record.dbxrefs),
        "features": [canonicalFeature(feature, len(record)) for feature in record.features],
    }
    if duplexGeometry is not None:
        canonical["schema"] = "vibegene.sequence.v4"
        canonical["duplex_geometry"] = asdict(duplexGeometry)
    encoded = json.dumps(canonical, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True, weakref_slot=True, init=False, repr=False, eq=False)
class SequenceRevision:
    """Own one sequence snapshot; mutable Biopython values stay private.

    Use fromSeqRecord() to capture a record and toSeqRecord() for an isolated
    external copy. Features expose immutable display projections; the complete
    supported source annotations and locations remain in the private payload.
    moleculeType is the alphabet interpretation; moleculeDeclaration retains
    the supplied declaration, when present. references and dbxrefs follow
    their separate source orders.
    Separate IDs identify the record and its exact revision. The content digest
    excludes internal IDs and provenance identifying where it was captured.
    """

    _record: SeqRecord
    _features: tuple[SequenceFeature, ...]
    recordId: str
    revisionId: str
    sourceArtifactId: str | None
    sourceOrdinal: int | None
    sourcePath: Path | None
    sourceFormat: str | None
    moleculeType: MoleculeType
    moleculeDeclaration: str | None
    strandState: StrandState
    duplexGeometry: DuplexGeometry | None
    outputReference: OutputReference | None
    references: tuple[PublicationReference, ...]
    dbxrefs: tuple[str, ...]
    contentDigest: str

    def __init__(
        self,
        record: SeqRecord,
        *,
        moleculeType: MoleculeType,
        sourceArtifactId: str | None = None,
        sourceOrdinal: int | None = None,
        sourcePath: str | Path | None = None,
        sourceFormat: str | None = None,
        duplexGeometry: DuplexGeometry | None = None,
        outputReference: OutputReference | None = None,
    ) -> None:
        """Capture a defensive copy after validating the supported record subset."""
        self._initialize(
            record, moleculeType=moleculeType, sourceArtifactId=sourceArtifactId,
            sourceOrdinal=sourceOrdinal, sourcePath=sourcePath, sourceFormat=sourceFormat,
            duplexGeometry=duplexGeometry, outputReference=outputReference,
        )

    def _initialize(
        self,
        record: SeqRecord,
        *,
        moleculeType: MoleculeType,
        sourceArtifactId: str | None,
        sourceOrdinal: int | None,
        sourcePath: str | Path | None,
        sourceFormat: str | None,
        duplexGeometry: DuplexGeometry | None,
        outputReference: OutputReference | None,
        identities: tuple[str, str, tuple[str, ...]] | None = None,
    ) -> None:
        if sourceOrdinal is not None and (type(sourceOrdinal) is not int or sourceOrdinal < 1):
            raise ValueError("A source record ordinal must be a positive one-based integer.")
        if outputReference is not None and type(outputReference) is not OutputReference:
            raise ValueError("A product revision needs a typed generating output reference.")
        sequence = _validateRecord(record, moleculeType)
        payload = deepcopy(record)
        payload.seq = Seq(sequence)
        moleculeDeclaration = payload.annotations.get("molecule_type")
        payload.annotations.setdefault("molecule_type", moleculeType)
        strandState: StrandState = "unknown"
        if moleculeType == "protein":
            strandState = "not_applicable"
        elif moleculeDeclaration == "ds-DNA":
            strandState = "duplex"
        if duplexGeometry is not None:
            if type(duplexGeometry) is not DuplexGeometry or moleculeType != "DNA":
                raise ValueError("Physical duplex geometry requires DNA and a DuplexGeometry value.")
            duplexGeometry.validateAgainst(sequence, payload.annotations.get("topology"))
            strandState = "duplex"
        sourceReferences = payload.annotations.get("references", [])
        if type(sourceReferences) not in (list, tuple):
            raise ValueError("Record references must be an ordered list or tuple.")
        references = tuple(projectReference(item, len(payload)) for item in sourceReferences)
        digest = _fingerprint(
            payload, moleculeType, moleculeDeclaration, strandState, references, duplexGeometry,
        )
        if identities is None:
            recordId = str(uuid4())
            revisionId = str(uuid4())
            featureIds = tuple(str(uuid4()) for _ in payload.features)
        else:
            recordId, revisionId, featureIds = identities
            _validateIdentities(recordId, revisionId, featureIds, len(payload.features))
        object.__setattr__(self, "_record", payload)
        object.__setattr__(self, "_features", tuple(
            projectFeature(item, featureId)
            for item, featureId in zip(payload.features, featureIds, strict=True)
        ))
        object.__setattr__(self, "recordId", recordId)
        object.__setattr__(self, "revisionId", revisionId)
        object.__setattr__(self, "sourceArtifactId", sourceArtifactId)
        object.__setattr__(self, "sourceOrdinal", sourceOrdinal)
        object.__setattr__(self, "sourcePath", Path(sourcePath) if sourcePath is not None else None)
        object.__setattr__(self, "sourceFormat", sourceFormat)
        object.__setattr__(self, "moleculeType", moleculeType)
        object.__setattr__(self, "moleculeDeclaration", moleculeDeclaration)
        object.__setattr__(self, "strandState", strandState)
        object.__setattr__(self, "duplexGeometry", duplexGeometry)
        object.__setattr__(self, "outputReference", outputReference)
        object.__setattr__(self, "references", references)
        object.__setattr__(self, "dbxrefs", tuple(payload.dbxrefs))
        object.__setattr__(self, "contentDigest", digest)

    @classmethod
    def fromSeqRecord(
        cls,
        record: SeqRecord,
        *,
        moleculeType: MoleculeType,
        sourceArtifactId: str | None = None,
        sourceOrdinal: int | None = None,
        sourcePath: str | Path | None = None,
        sourceFormat: str | None = None,
        duplexGeometry: DuplexGeometry | None = None,
        outputReference: OutputReference | None = None,
    ) -> "SequenceRevision":
        """Create a snapshot without modifying the supplied record.

        Args:
            record: Fully specified Biopython record with supported annotations.
            moleculeType: Explicit alphabet interpretation; never guessed.
            sourceArtifactId: Optional link to the captured original-byte descriptor.
            sourceOrdinal: Optional one-based record position in the source capture.
            sourcePath: Optional advisory path to the supplied file.
            sourceFormat: Optional format of the supplied record.
            duplexGeometry: Physical strand spans and ends on the aligned DNA reference, if known.
            outputReference: Generating operation and output key for a derived candidate.

        Returns:
            A new snapshot with independent record/revision identities.

        Raises:
            TypeError: The input is not a SeqRecord.
            ValueError: Content or metadata is outside the supported subset.
        """
        return cls(
            record, moleculeType=moleculeType, sourceArtifactId=sourceArtifactId,
            sourceOrdinal=sourceOrdinal, sourcePath=sourcePath, sourceFormat=sourceFormat,
            duplexGeometry=duplexGeometry, outputReference=outputReference,
        )

    @classmethod
    def fromStoredRecord(
        cls,
        record: SeqRecord,
        *,
        moleculeType: MoleculeType,
        recordId: str,
        revisionId: str,
        featureIds: tuple[str, ...],
        sourceArtifactId: str | None = None,
        sourceOrdinal: int | None = None,
        sourcePath: str | Path | None = None,
        sourceFormat: str | None = None,
        duplexGeometry: DuplexGeometry | None = None,
        outputReference: OutputReference | None = None,
    ) -> "SequenceRevision":
        """Restore a snapshot using its stored record, revision, and feature IDs.

        Supply the original molecule declaration, leaving it absent when undeclared.
        Feature identities follow the record's feature order and must be unique.
        """
        revision = cls.__new__(cls)
        revision._initialize(
            record, moleculeType=moleculeType, sourceArtifactId=sourceArtifactId,
            sourceOrdinal=sourceOrdinal, sourcePath=sourcePath, sourceFormat=sourceFormat,
            duplexGeometry=duplexGeometry, outputReference=outputReference,
            identities=(recordId, revisionId, featureIds),
        )
        return revision

    def withName(self, name: str) -> "SequenceRevision":
        """Return the named snapshot of this record, preserving its origin and feature IDs.

        Trim the chosen name and reject an empty name. Reuse this snapshot if the
        trimmed name is unchanged.
        """
        if type(name) is not str or not name.strip():
            raise ValueError("A saved record name must be a nonblank string.")
        name = name.strip()
        if name == self.name:
            return self
        named = copy(self)
        # Only the name changes. Both snapshots expose their shared payload through copies.
        payload = copy(self._record)
        payload.name = name
        digest = _fingerprint(
            payload, self.moleculeType, self.moleculeDeclaration, self.strandState, self.references,
            self.duplexGeometry,
        )
        object.__setattr__(named, "_record", payload)
        object.__setattr__(named, "revisionId", str(uuid4()))
        object.__setattr__(named, "contentDigest", digest)
        return named

    def toSeqRecord(self, *, preserveTraversalEvidence: bool = False) -> SeqRecord:
        """Return an independent copy with molecule metadata for Biopython.

        A missing source declaration uses the normalized molecule type in the copy.
        Set preserveTraversalEvidence when moving features outside the snapshot.
        It marks uncertain traversal on the copied feature, leaving the source intact.
        """
        record = deepcopy(self._record)
        if (preserveTraversalEvidence and self.sourceFormat == "snapgene"
                and self.topology == "circular"):
            # A projected or exported copy loses the source format and may change topology.
            # Keep the ordinary snapshot copy exact for revision hashing and historical replay.
            for feature, view in zip(record.features, self.features, strict=True):
                if not self.hasUnverifiedFeatureTraversal(view):
                    continue
                prior = feature.qualifiers.get(_TRAVERSAL_QUALIFIER, ())
                values = prior if isinstance(prior, (list, tuple)) else (prior,)
                if _UNVERIFIED_ORIGIN not in values:
                    feature.qualifiers[_TRAVERSAL_QUALIFIER] = [*values, _UNVERIFIED_ORIGIN]
        return record

    @property
    def features(self) -> tuple[SequenceFeature, ...]:
        """Return stable immutable feature projections in imported feature order."""
        return self._features

    def getFeature(self, featureId: str) -> SequenceFeature:
        """Look up an imported feature by its snapshot-local opaque identifier.

        Args:
            featureId: An identifier from this snapshot's features.

        Returns:
            The immutable feature projection.

        Raises:
            KeyError: The identifier does not belong to this snapshot.
        """
        for feature in self._features:
            if feature.featureId == featureId:
                return feature
        raise KeyError(featureId)

    def hasUnverifiedFeatureTraversal(self, feature: SequenceFeature) -> bool:
        """Identify unverified SnapGene part order on original or projected annotations."""
        qualifiers = dict(feature.qualifiers)
        if _UNVERIFIED_ORIGIN in qualifiers.get(_TRAVERSAL_QUALIFIER, ()):
            return True
        return (
            self.sourceFormat == "snapgene"
            and self.topology == "circular"
            and feature.strand == -1
            and len(feature.parts) > 1
            and qualifiers.get(ANNOTATION_CURATION_QUALIFIER) not in (
                ("manual",), ("catalogue",),
            )
            and any(part.start == 0 for part in feature.parts)
            and any(part.end == len(self) for part in feature.parts)
        )

    @property
    def annotations(self) -> dict[str, object]:
        """Return a defensive copy of record metadata, preserving its original types."""
        return deepcopy(self._record.annotations)

    @property
    def illustrationAttribution(self) -> IllustrationAttribution | None:
        """Read file-supplied illustration labels retained by this snapshot."""
        return IllustrationAttribution.fromAnnotations(self._record.annotations)

    @property
    def sequence(self) -> str:
        """Return letters in their supplied case and stored orientation."""
        return str(self._record.seq)

    def sequenceSlice(self, start: int, end: int) -> str:
        """Return a stored half-open slice without decoding the rest of the record."""
        return str(self._record.seq[start:end])

    @property
    def name(self) -> str:
        """Return the snapshot's imported or saved name, independent of internal identity."""
        return self._record.name

    @property
    def sourceId(self) -> str:
        """Return the original external identifier; duplicates are allowed."""
        return self._record.id

    @property
    def description(self) -> str:
        """Return the imported description, including the full header for FASTA."""
        return self._record.description

    @property
    def topology(self) -> Literal["linear", "circular"] | None:
        """Return declared topology; never infer it from unannotated sequence text."""
        return self._record.annotations.get("topology")

    def __len__(self) -> int:
        """Return nucleotide or amino-acid count, not inferred duplex base pairs."""
        return len(self._record)

    def __repr__(self) -> str:
        return (
            f"SequenceRevision(name={self.name!r}, moleculeType={self.moleculeType!r}, "
            f"length={len(self)})"
        )
