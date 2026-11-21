"""Store DNA samples, markers and settings for an illustrative gel display."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import isfinite, log10
from typing import Literal
from uuid import uuid4

from strandfolio.domain.fragments import DNAFragment, FragmentSpan
from strandfolio.domain.provenance import OutputReference
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.operations.pcr import PcrProduct, PcrRun
from strandfolio.operations.restriction import RestrictionDigest
from strandfolio.storage.catalogues import DEFAULT_LADDER_ID, LadderProfile, LadderRelease


GEL_MODEL_VERSION = "linear-dsdna-log10-v1"
GEL_LEGEND = (
    "Illustrative linear dsDNA, logarithmic bp scale, fixed brightness. "
    "Digest sizes use forward-strand length. Circular and unknown-topology inputs stay off the plot. "
    "Migration, resolution and yield are uncalibrated."
)


def _validText(value: object) -> bool:
    return type(value) is str and bool(value.strip()) and "\x00" not in value


@dataclass(frozen=True, slots=True)
class GelSettings:
    """Set positive inclusive bp limits for one shared marker/sample scale."""

    minimumBp: int = 100
    maximumBp: int = 10_000

    def __post_init__(self) -> None:
        if (type(self.minimumBp) is not int or type(self.maximumBp) is not int
                or not 0 < self.minimumBp < self.maximumBp):
            raise ValueError("Choose increasing positive integer bp limits for the gel display.")

    def position(self, sizeBp: int) -> float | None:
        """Return a position from wells (0) to the bottom (1), or None outside the display."""
        if type(sizeBp) is not int or sizeBp <= 0:
            raise ValueError("A gel size must be a positive integer in bp.")
        if not self.minimumBp <= sizeBp <= self.maximumBp:
            return None
        low, high = log10(self.minimumBp), log10(self.maximumBp)
        if low == high:
            # Adjacent very large integers may have identical floating-point logarithms.
            return (self.maximumBp - sizeBp) / (self.maximumBp - self.minimumBp)
        return (high - log10(sizeBp)) / (high - low)


@dataclass(frozen=True, slots=True)
class GelMarker:
    """A DNA ladder ID with an optional catalogue release and marker profile."""

    ladderId: str = DEFAULT_LADDER_ID
    profile: LadderProfile | None = None
    release: LadderRelease | None = None

    def __post_init__(self) -> None:
        if not _validText(self.ladderId):
            raise ValueError("A gel marker requires a ladder ID.")
        if self.release is not None and not isinstance(self.release, LadderRelease):
            raise ValueError("A gel marker requires a typed ladder release.")
        if self.profile is not None:
            if (not isinstance(self.profile, LadderProfile) or self.profile.unit != "bp"
                    or self.profile.ladderId != self.ladderId):
                raise ValueError("A gel marker requires the matching DNA profile in bp.")
            if self.release is not None and self.profile.release != self.release:
                raise ValueError("The marker profile belongs to a different ladder release.")
            object.__setattr__(self, "release", self.profile.release)

    @classmethod
    def fromProfile(cls, profile: LadderProfile) -> "GelMarker":
        """Capture a complete DNA profile without retaining a database handle."""
        return cls(profile.ladderId, profile)

    @property
    def rangeBp(self) -> tuple[int, int] | None:
        """Return actual marker limits independent of the current display limits."""
        if self.profile is None:
            return None
        low, high = self.profile.range
        return int(low), int(high)


@dataclass(frozen=True, slots=True)
class GelViewSettings:
    """The marker, display settings and selected product key for one gel preview."""

    settings: GelSettings = GelSettings()
    marker: GelMarker = GelMarker()
    selectedOutputKey: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.settings, GelSettings) or not isinstance(self.marker, GelMarker):
            raise ValueError("A saved gel view requires typed display settings and marker.")
        if self.selectedOutputKey is not None and not _validText(self.selectedOutputKey):
            raise ValueError("A saved gel selection requires a nonblank output key.")


@dataclass(frozen=True, slots=True)
class GelProduct:
    """One size and loading amount with its recorded digest or PCR product.

    Sizes use the forward-strand length in bp under the illustrative model. Amounts
    are in ng per loaded component. Unknown amounts remain None.
    """

    lengthBp: int
    topology: Literal["linear", "circular"] | None
    sourceSpans: tuple[FragmentSpan, ...]
    fragment: DNAFragment | None = None
    amountNg: float | None = None
    pcrProduct: PcrProduct | None = None

    def __post_init__(self) -> None:
        if type(self.lengthBp) is not int or self.lengthBp <= 0:
            raise ValueError("A gel product requires a positive integer bp size.")
        if self.topology not in (None, "linear", "circular"):
            raise ValueError("A gel product has an unsupported topology.")
        if (type(self.sourceSpans) is not tuple or not self.sourceSpans
                or any(not isinstance(span, FragmentSpan) for span in self.sourceSpans)):
            raise ValueError("A gel product requires immutable source spans.")
        if self.fragment is not None and (
            not isinstance(self.fragment, DNAFragment) or (
                self.lengthBp, self.topology, self.sourceSpans,
            ) != (self.fragment.forwardLength, self.fragment.topology, self.fragment.sourceSpans)
        ):
            raise ValueError("A gel product must preserve the recorded fragment geometry.")
        pcr = self.pcrProduct
        if pcr is not None and (
            self.fragment is not None or not isinstance(pcr, PcrProduct) or (
                self.lengthBp, self.topology, self.sourceSpans,
            ) != (pcr.sizeBp, pcr.revision.topology, _pcrSourceSpans(pcr))
        ):
            raise ValueError("A gel product must preserve the recorded PCR size and source spans.")
        if self.amountNg is not None and (
            type(self.amountNg) not in (int, float) or self.amountNg < 0
            or (type(self.amountNg) is float and not isfinite(self.amountNg))
        ):
            raise ValueError("Loaded amount must be finite nonnegative ng or unknown.")

    @property
    def outputReference(self) -> OutputReference | None:
        """Return the run and output key that produced the fragment or PCR product, if available."""
        product = self.fragment if self.fragment is not None else self.pcrProduct
        return product.revision.outputReference if product is not None else None


def _pcrSourceSpans(product: PcrProduct) -> tuple[FragmentSpan, ...]:
    return tuple(FragmentSpan(start, end) for start, end in product.sourceSegments)


def _untreatedProduct(source: SequenceRevision, amountNg: float | None) -> GelProduct:
    geometry = source.duplexGeometry
    forwardLength = len(geometry.forwardSpan) if geometry is not None else len(source)
    return GelProduct(
        forwardLength, source.topology, (FragmentSpan(0, len(source)),), amountNg=amountNg,
    )


@dataclass(frozen=True, slots=True)
class GelLane:
    """A gel lane with its source revision and either untreated DNA or products from a recorded run."""

    laneId: str
    label: str
    source: SequenceRevision
    run: RestrictionDigest | PcrRun | None
    products: tuple[GelProduct, ...]

    def __post_init__(self) -> None:
        if not _validText(self.laneId) or not _validText(self.label):
            raise ValueError("A gel lane requires a valid ID and label.")
        if not isinstance(self.source, SequenceRevision) or self.source.moleculeType != "DNA":
            raise ValueError("The illustrative gel accepts DNA sources in bp.")
        if (type(self.products) is not tuple
                or any(not isinstance(product, GelProduct) for product in self.products)):
            raise ValueError("A gel lane requires immutable products.")
        if self.run is None:
            if (len(self.products) != 1
                    or self.products[0] != _untreatedProduct(self.source, self.products[0].amountNg)):
                raise ValueError("An untreated lane requires its exact source size and topology.")
        else:
            self._validateRecordedRun()

    def _validateRecordedRun(self) -> None:
        run = self.run
        if not isinstance(run, (RestrictionDigest, PcrRun)) or run.status != "completed":
            raise ValueError("A treated gel lane requires a completed run.")
        template = run.template
        if (self.source.recordId, self.source.revisionId, self.source.contentDigest) != (
            template.recordId, template.revisionId, template.contentDigest,
        ):
            raise ValueError("A gel lane must pin the exact recorded source revision.")
        if isinstance(run, RestrictionDigest):
            if not self.products or tuple(product.fragment for product in self.products) != run.fragments:
                raise ValueError("A digest lane requires every recorded product in order.")
        else:
            products = tuple(product.pcrProduct for product in self.products)
            if products != run.products and (len(products) != 1 or products[0] not in run.products):
                raise ValueError("A PCR lane requires one keyed output or all recorded products in order.")

    @property
    def treatment(self) -> str:
        """Return the treatment label for the lane."""
        if self.run is None:
            return "Untreated"
        if isinstance(self.run, PcrRun):
            return "PCR"
        enzymes = " + ".join(enzyme.name for enzyme in self.run.enzymes)
        return enzymes if self.run.cuts else f"{enzymes} · uncut"


def untreatedLane(
    source: SequenceRevision, *, laneId: str | None = None, label: str | None = None,
    amountNg: float | None = None,
) -> GelLane:
    """Pin the untreated DNA revision; circles retain their size without a band."""
    return GelLane(
        str(uuid4()) if laneId is None else laneId,
        source.name if label is None else label,
        source, None, (_untreatedProduct(source, amountNg),),
    )


def digestLane(
    source: SequenceRevision, run: RestrictionDigest, *, laneId: str | None = None,
    label: str | None = None, amountsNg: Mapping[str, float] | None = None,
) -> GelLane:
    """Pin a complete digest and optional per-output ng without changing the run."""
    amounts = {} if amountsNg is None else amountsNg
    if not set(amounts).issubset(fragment.outputKey for fragment in run.fragments):
        raise ValueError("A digest loading amount must refer to a recorded output key.")
    products = tuple(GelProduct(
        fragment.forwardLength, fragment.topology, fragment.sourceSpans,
        fragment, amounts.get(fragment.outputKey),
    ) for fragment in run.fragments)
    return GelLane(
        str(uuid4()) if laneId is None else laneId,
        source.name if label is None else label,
        source, run, products,
    )


def pcrLane(
    run: PcrRun, *, outputKey: str | None = None, laneId: str | None = None,
    label: str | None = None,
) -> GelLane:
    """Preview all completed PCR outputs or pin exactly one recorded output key."""
    if not isinstance(run, PcrRun) or run.status != "completed":
        raise ValueError("A PCR gel lane requires a completed run.")
    products = run.products
    if outputKey is not None:
        products = tuple(product for product in products if product.outputKey == outputKey)
        if len(products) != 1:
            raise ValueError("Choose a recorded PCR output key that resolves to one product.")
    return GelLane(
        str(uuid4()) if laneId is None else laneId,
        run.template.name if label is None else label,
        run.template, run, tuple(GelProduct(
            product.sizeBp, product.revision.topology, _pcrSourceSpans(product), pcrProduct=product,
        ) for product in products),
    )


@dataclass(frozen=True, slots=True)
class GelSelection:
    """Select a lane or one recorded contributor within that lane."""

    laneId: str
    outputReference: OutputReference | None = None

    def __post_init__(self) -> None:
        if not _validText(self.laneId) or (
            self.outputReference is not None and not isinstance(self.outputReference, OutputReference)
        ):
            raise ValueError("A gel selection requires a lane ID and an optional output reference.")


@dataclass(frozen=True, slots=True)
class GelState:
    """Immutable sample lanes, marker, display range and selection for one gel view."""

    marker: GelMarker = GelMarker()
    settings: GelSettings = GelSettings()
    lanes: tuple[GelLane, ...] = ()
    selection: GelSelection | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.marker, GelMarker) or not isinstance(self.settings, GelSettings):
            raise ValueError("A gel requires typed marker and display settings.")
        if type(self.lanes) is not tuple or any(not isinstance(lane, GelLane) for lane in self.lanes):
            raise ValueError("A gel requires immutable sample lanes.")
        if len({lane.laneId for lane in self.lanes}) != len(self.lanes):
            raise ValueError("Gel lane identities must be unique.")
        if self.selection is not None:
            self._validateSelection()

    def _validateSelection(self) -> None:
        if not isinstance(self.selection, GelSelection):
            raise ValueError("Use a typed gel selection.")
        try:
            lane = self.lanes[self._laneIndex(self.selection.laneId)]
        except KeyError as error:
            raise ValueError("The selected lane is absent from this gel.") from error
        reference = self.selection.outputReference
        if reference is not None and not any(p.outputReference == reference for p in lane.products):
            raise ValueError("The selected contributor is absent from its gel lane.")

    def _laneIndex(self, laneId: str) -> int:
        for index, lane in enumerate(self.lanes):
            if lane.laneId == laneId:
                return index
        raise KeyError(laneId)

    def withMarker(self, marker: GelMarker) -> "GelState":
        """Change only the marker used by this view."""
        return replace(self, marker=marker)

    def withSelection(self, selection: GelSelection | None) -> "GelState":
        """Select a lane or one contributor, or clear the selection."""
        return replace(self, selection=selection)

    def appendLane(self, lane: GelLane) -> "GelState":
        """Add a pinned sample at the next display position."""
        return self.insertLane(len(self.lanes), lane)

    def insertLane(self, index: int, lane: GelLane) -> "GelState":
        """Insert a sample at a zero-based display position, including to undo removal."""
        if type(index) is not int or not 0 <= index <= len(self.lanes):
            raise IndexError("The requested gel lane position is outside this view.")
        return replace(self, lanes=(*self.lanes[:index], lane, *self.lanes[index:]))

    def replaceLane(self, laneId: str, lane: GelLane) -> "GelState":
        """Replace a known lane in place, keeping its stable ID and any surviving selection."""
        index = self._laneIndex(laneId)
        selection = self.selection
        if (selection is not None and selection.laneId == laneId
                and not any(p.outputReference == selection.outputReference for p in lane.products)):
            selection = GelSelection(laneId)
        return replace(
            self, lanes=(*self.lanes[:index], replace(lane, laneId=laneId), *self.lanes[index + 1:]),
            selection=selection,
        )

    def removeLane(self, laneId: str) -> "GelState":
        """Remove a known sample. The marker remains available in an empty view."""
        index = self._laneIndex(laneId)
        selection = self.selection if self.selection and self.selection.laneId != laneId else None
        return replace(
            self, lanes=(*self.lanes[:index], *self.lanes[index + 1:]), selection=selection,
        )
