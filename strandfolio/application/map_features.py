"""Feature visibility for whole-molecule maps. Source annotations remain intact."""

from strandfolio.domain.features import SequenceFeature
from strandfolio.domain.sequence import SequenceRevision


LANDMARK_TYPES = frozenset({"gene", "CDS", "promoter", "rep_origin", "terminator"})


def mapFeatureType(feature: SequenceFeature) -> str:
    """Classify a regulatory promoter or terminator under its specific map type."""
    if feature.type == "regulatory":
        for value in dict(feature.qualifiers).get("regulatory_class", ()):
            if value.casefold() in {"promoter", "terminator"}:
                return value.casefold()
    return feature.type


def isWholeRecordSource(feature: SequenceFeature, length: int) -> bool:
    if feature.type != "source" or len(feature.parts) != 1:
        return False
    part = feature.parts[0]
    return part.isSelectable and (part.start, part.end) == (0, length)


def mapFeatures(
    revision: SequenceRevision, types: frozenset[str] | None, selectedId: str | None,
) -> tuple[SequenceFeature, ...]:
    """Filter before lane packing. Primer visibility is controlled by its own layer."""
    if types is None or revision.moleculeType == "protein":
        return revision.features
    return tuple(
        feature for feature in revision.features
        if feature.featureId == selectedId or feature.type == "primer_bind"
        or mapFeatureType(feature) in types
        or (types == LANDMARK_TYPES and isWholeRecordSource(feature, len(revision)))
    )
