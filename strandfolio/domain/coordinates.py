"""Revision-bound selection in stored, zero-based, half-open coordinates."""

from dataclasses import dataclass


SelectionSegment = tuple[int, int]


@dataclass(frozen=True, slots=True)
class SequenceSelection:
    """Store ordered zero-based half-open intervals on one sequence revision.

    A single-segment selection stores ((min(anchor, caret), max(anchor, caret)),)
    when segments are omitted. Circular origin-wrapping and whole multipart feature
    selections store ordered segments without including gaps between parts.
    A detached reverse feature can retain its segment order for reverse copy.
    """

    revisionId: str
    anchor: int
    caret: int
    featureId: str | None = None
    partIndex: int | None = None
    segments: tuple[SelectionSegment, ...] = ()
    wholeFeature: bool = False
    strand: int | None = None
    retainsReverseTraversal: bool = False

    def __post_init__(self) -> None:
        if not self.revisionId:
            raise ValueError("A selection must identify its sequence revision.")
        if any(type(value) is not int or value < 0 for value in (self.anchor, self.caret)):
            raise ValueError("Selection boundaries must be nonnegative integers.")
        if not self.segments:
            object.__setattr__(
                self, "segments", ((min(self.anchor, self.caret), max(self.anchor, self.caret)),),
            )
        else:
            if not isinstance(self.segments, tuple):
                raise ValueError("Selection segments must be an immutable tuple of intervals.")
            for index, segment in enumerate(self.segments):
                if (
                    not isinstance(segment, tuple)
                    or len(segment) != 2
                    or type(segment[0]) is not int
                    or type(segment[1]) is not int
                    or segment[0] < 0
                    or segment[1] < segment[0]
                ):
                    raise ValueError(f"Invalid selection segment at index {index}.")
                if len(self.segments) > 1 and segment[0] == segment[1]:
                    raise ValueError("Compound selection segments must each be nonempty.")
        if self.featureId is None:
            if self.partIndex is not None or self.wholeFeature:
                raise ValueError("Feature selection requires a feature identity.")
        else:
            if not isinstance(self.featureId, str) or not self.featureId:
                raise ValueError("A feature identity must be a nonempty string.")
            if self.wholeFeature:
                if self.partIndex is not None:
                    raise ValueError("Whole-feature selection cannot also identify a single segment.")
            elif type(self.partIndex) is not int or self.partIndex < 0:
                raise ValueError("A feature segment index must be a nonnegative integer.")
        if self.strand not in (None, 1, -1, 0):
            raise ValueError("Selection strand must be 1, -1, 0, or None.")
        if type(self.retainsReverseTraversal) is not bool:
            raise ValueError("Retained reverse traversal must be a boolean.")

    @property
    def start(self) -> int:
        """Return the first segment's included lower boundary."""
        return self.segments[0][0]

    @property
    def end(self) -> int:
        """Return the final segment's excluded upper boundary."""
        return self.segments[-1][1]

    @property
    def isCompound(self) -> bool:
        """Return whether the selection spans multiple ordered segments."""
        return len(self.segments) > 1

    def displayRange(self, sequenceLength: int, *, circular: bool) -> tuple[int, int] | None:
        """Return inclusive forward coordinates if one range covers exactly these bases."""
        if not self.isCompound:
            return (self.start + 1, self.end) if self.start < self.end else None

        # Normalize for display only. Copy, feature identity and strand retain part order.
        spans: list[SelectionSegment] = []
        for start, end in sorted(self.segments):
            if spans and start < spans[-1][1]:
                return None  # A range cannot express repeated bases.
            if spans and start == spans[-1][1]:
                spans[-1] = (spans[-1][0], end)
            else:
                spans.append((start, end))
        if len(spans) == 1:
            if circular and spans[0] == (0, sequenceLength):
                # A complete turn may have been entered with adjacent reversed endpoints.
                first, last = self.segments[0], self.segments[-1]
                if first[0] == last[1]:
                    return first[0] + 1, last[1]
                if self.strand == -1 and last[0] == first[1]:
                    return last[0] + 1, first[1]
            return spans[0][0] + 1, spans[0][1]
        if circular and len(spans) == 2 and spans[0][0] == 0 and spans[1][1] == sequenceLength:
            return spans[1][0] + 1, spans[0][1]
        return None

    def wrapsOrigin(self, sequenceLength: int) -> bool:
        """Return whether the bases form one interval across a circular origin."""
        endpoints = self.displayRange(sequenceLength, circular=True)
        return endpoints is not None and endpoints[0] > endpoints[1]

    def contains(self, position: int) -> bool:
        """Return whether a zero-based sequence index lies inside any selected segment."""
        return any(start <= position < end for start, end in self.segments)

    def clippedTo(self, start: int, end: int) -> tuple[SelectionSegment, ...]:
        """Return nonempty intersections with [start, end), sorted by coordinate."""
        clipped: list[SelectionSegment] = []
        for segStart, segEnd in self.segments:
            overlapStart, overlapEnd = max(start, segStart), min(end, segEnd)
            if overlapStart < overlapEnd:
                clipped.append((overlapStart, overlapEnd))
        clipped.sort()
        return tuple(clipped)

    def __len__(self) -> int:
        return sum(end - start for start, end in self.segments)
