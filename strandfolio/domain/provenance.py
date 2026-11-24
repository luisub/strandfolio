"""Stable references to preserved source bytes and operation outputs."""

from dataclasses import dataclass
from hashlib import sha256
from uuid import uuid4

from strandfolio.domain.coordinates import SelectionSegment


@dataclass(frozen=True, slots=True)
class IllustrationAttribution:
    """Source names supplied by a design illustration in GenBank record metadata."""

    illustration: str
    recordName: str
    backboneSource: str
    insertSource: str
    insertLabel: str

    @classmethod
    def fromAnnotations(cls, annotations: dict[str, object]) -> "IllustrationAttribution | None":
        """Read a complete, text-valued illustration block without resolving revision identities."""
        comments = annotations.get("structured_comment")
        block = comments.get("vibeGene-Illustration") if isinstance(comments, dict) else None
        if not isinstance(block, dict):
            return None
        values = []
        for field in ("Illustration", "Record name", "Backbone source", "Insert source", "Insert label"):
            value = block.get(field)
            if not isinstance(value, str) or not value.strip():
                return None
            values.append(value.strip())
        return cls(*values)


@dataclass(frozen=True, slots=True)
class DeclaredParent:
    """One user-reported use of an existing revision, with optional half-open intervals.

    The source and product intervals describe the same number of positions. The
    orientation, when reported, is relative to the product's stored axis.
    An absent orientation means the direction is unknown.
    """

    revisionId: str
    sourceSpan: SelectionSegment | None = None
    productSpan: SelectionSegment | None = None
    orientation: int | None = None

    def __post_init__(self) -> None:
        if type(self.revisionId) is not str or not self.revisionId.strip() or "\x00" in self.revisionId:
            raise ValueError("A reported parent requires a revision ID.")
        if self.orientation is not None and (
            type(self.orientation) is not int or self.orientation not in (-1, 1)
        ):
            raise ValueError("Reported orientation must be unknown, forward (+1), or reverse (-1).")
        if self.sourceSpan is None and self.productSpan is None:
            return
        for span in (self.sourceSpan, self.productSpan):
            if (type(span) is not tuple or len(span) != 2 or any(type(n) is not int for n in span)
                    or not 0 <= span[0] < span[1]):
                raise ValueError("Supply both source and product intervals with valid boundaries.")
        if self.sourceSpan[1] - self.sourceSpan[0] != self.productSpan[1] - self.productSpan[0]:
            raise ValueError("Reported source and product intervals must have the same length.")


@dataclass(frozen=True, slots=True)
class OutputReference:
    """Identify an immutable product by the generating operation and its local key."""

    operationId: str
    outputKey: str

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value.strip() or "\x00" in value
               for value in (self.operationId, self.outputKey)):
            raise ValueError("A product reference requires a valid operation ID and output key.")


@dataclass(frozen=True, slots=True)
class SourceArtifact:
    """Describe the original bytes read during one intake.

    Attributes:
        artifactId: Opaque intake identity, independent of the byte checksum.
        filename: Original basename, without a machine-specific directory.
        sha256: SHA-256 of the exact bytes before decoding or parsing.
        byteCount: Number of original bytes read.
    """

    artifactId: str
    filename: str
    sha256: str
    byteCount: int

    @classmethod
    def fromCapture(cls, filename: str, capturedBytes: bytes) -> "SourceArtifact":
        """Give the import a new ID and record the size and SHA-256 digest of its source bytes."""
        return cls(str(uuid4()), filename, sha256(capturedBytes).hexdigest(), len(capturedBytes))

    def verifyCapture(self, capturedBytes: bytes) -> None:
        """Validate this occurrence and its exact bytes before storing or returning them."""
        if not isinstance(self.artifactId, str) or not self.artifactId.strip():
            raise ValueError("A source artifact needs a nonblank identity.")
        if not isinstance(self.filename, str) or not self.filename.strip():
            raise ValueError("A source artifact needs its original filename.")
        if (type(capturedBytes) is not bytes or type(self.byteCount) is not int
                or len(capturedBytes) != self.byteCount
                or sha256(capturedBytes).hexdigest() != self.sha256):
            raise ValueError(f"Captured bytes do not match artifact {self.artifactId} ({self.sha256}).")
