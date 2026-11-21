"""Edit annotations and review exact matches against the feature catalogue."""

from collections import Counter
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
import json
import re
from typing import Literal
from uuid import uuid4

from Bio.Seq import Seq
from Bio.SeqFeature import CompoundLocation, SeqFeature, SimpleLocation
from Bio.SeqRecord import SeqRecord

from strandfolio.domain.coordinates import SelectionSegment
from strandfolio.domain.features import (
    ANNOTATION_CURATION_QUALIFIER,
    ANNOTATION_DECISIONS_KEY,
    FEATURE_COLOR_QUALIFIER,
    FEATURE_LABEL_QUALIFIERS,
    LABEL_EVIDENCE_QUALIFIER,
    LabelEvidence,
    SequenceFeature,
    canonicalFeature,
    canonicalMetadata,
    isFeatureColor,
)
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.storage.feature_catalogue import FeatureCatalogue, FeatureHit, FeatureMatch


LabelStatus = Literal["new", "already_annotated", "overlap", "ambiguous", "dismissed"]
FeatureLocus = tuple[tuple[SelectionSegment, ...], int | None]
_KIND_TO_TYPE = {"replication_origin": "rep_origin", "selection_marker": "CDS"}
_ALIAS_QUALIFIER = "vibegene_alias"
_ANNOTATION_NAME = re.compile(r"(?=[^A-Za-z]*[A-Za-z])[A-Za-z0-9_'*-]+")
_TRANSLATION_SETTINGS = ("codon_start", "transl_table", "transl_except")


@dataclass(frozen=True, slots=True)
class AnnotationEdit:
    """A changed immutable draft or the original revision for a no-op."""

    revision: SequenceRevision
    featureId: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LabelSuggestion:
    """An exact catalogue match, its location in the sequence and its review status."""

    suggestionId: str
    label: str
    featureType: str
    aliases: tuple[str, ...]
    evidence: LabelEvidence
    status: LabelStatus = "new"
    matchedFeatureIds: tuple[str, ...] = ()
    overlappingFeatureIds: tuple[str, ...] = ()
    equivalentReferenceIds: tuple[str, ...] = ()
    _uniqueNames: frozenset[str] = frozenset()

    @property
    def parts(self) -> tuple[SelectionSegment, ...]:
        return self.evidence.parts

    @property
    def strand(self) -> int | None:
        return self.evidence.strand


@dataclass(frozen=True, slots=True)
class LabelReview:
    """Label suggestions for a specific sequence revision and catalogue release."""

    revisionId: str
    catalogueRelease: str
    suggestions: tuple[LabelSuggestion, ...]
    wholePlasmids: tuple[FeatureMatch, ...] = ()


def _label(value: str) -> str:
    if type(value) is not str or not value.strip() or any(char in value for char in "\r\n\0"):
        raise ValueError("Enter a nonblank label on one line.")
    return value.strip()


def _featureType(value: str) -> str:
    if type(value) is not str or not _ANNOTATION_NAME.fullmatch(value.strip()):
        raise ValueError("Enter a feature type such as misc_feature, CDS or promoter.")
    return value.strip()


def _location(
    revision: SequenceRevision, parts: tuple[SelectionSegment, ...], strand: int | None,
) -> SimpleLocation | CompoundLocation:
    if strand is not None and (type(strand) is not int or strand not in (1, -1, 0)):
        raise ValueError("Choose forward, reverse, unstranded or unspecified strand.")
    if revision.moleculeType == "protein" and strand in (1, -1):
        raise ValueError("Protein annotations have no forward or reverse strand.")
    if type(parts) is not tuple or not parts:
        raise ValueError("Enter one or more ordered annotation parts.")
    for part in parts:
        if (
            type(part) is not tuple or len(part) != 2
            or any(type(boundary) is not int for boundary in part)
            or not 0 <= part[0] < part[1] <= len(revision)
        ):
            raise ValueError("Each annotation part needs a nonempty range within the sequence.")
    ascending = tuple(sorted(parts))
    if any(previous[1] > current[0] for previous, current in zip(ascending, ascending[1:])):
        raise ValueError("Parts of one annotation cannot overlap.")
    ordered = ascending[::-1] if strand == -1 else ascending
    firstIndex = ordered.index(parts[0])
    if parts != ordered and (
        revision.topology != "circular"
        or parts != ordered[firstIndex:] + ordered[:firstIndex]
    ):
        raise ValueError("Enter annotation parts in strand order with at most one circular wrap.")
    locations = [SimpleLocation(start, end, strand=strand) for start, end in parts]
    return locations[0] if len(locations) == 1 else CompoundLocation(locations, operator="join")


def _featureLocus(feature: SequenceFeature) -> FeatureLocus | None:
    if feature.operator not in (None, "join") or not feature.parts:
        return None
    parts = []
    for part in feature.parts:
        if not part.isSelectable or part.strand != feature.strand:
            return None
        parts.append((part.start, part.end))
    return tuple(parts), feature.strand


def canEditAnnotationLocation(revision: SequenceRevision, featureId: str) -> bool:
    """Allow coordinate edits for validated exact local locations with verified traversal."""
    feature = revision.getFeature(featureId)
    locus = _featureLocus(feature)
    if locus is None or revision.hasUnverifiedFeatureTraversal(feature):
        return False
    try:
        _location(revision, *locus)
    except ValueError:
        return False
    return True


def _changeQualifiers(feature: SeqFeature, qualifiers: dict[str, object] | None) -> None:
    if qualifiers is None:
        return
    if type(qualifiers) is not dict:
        raise ValueError("Annotation qualifiers must be a dictionary.")
    for key, value in qualifiers.items():
        if (
            type(key) is not str or not _ANNOTATION_NAME.fullmatch(key)
            or key in FEATURE_LABEL_QUALIFIERS or key.startswith("vibegene_")
        ):
            raise ValueError(f"Qualifier {key!r} is reserved or invalid for annotation editing.")
        canonicalMetadata(value, native=True)
        feature.qualifiers[key] = deepcopy(value)


def _qualifierStrings(value: object) -> tuple[str, ...]:
    values = value if isinstance(value, (tuple, list)) else (value,)
    return tuple(item for item in values if isinstance(item, str) and item.strip())


def _changeColor(feature: SeqFeature, color: str | None) -> None:
    if color is None:
        return
    if color == "":
        feature.qualifiers.pop(FEATURE_COLOR_QUALIFIER, None)
    elif isFeatureColor(color):
        current = _qualifierStrings(feature.qualifiers.get(FEATURE_COLOR_QUALIFIER))
        if not current or current[0].casefold() != color.casefold():
            feature.qualifiers[FEATURE_COLOR_QUALIFIER] = [color.upper()]
    else:
        raise ValueError("Enter a six-digit color such as #2266AA.")


def _finishEdit(
    revision: SequenceRevision, payload: SeqRecord, featureIds: Sequence[str], *,
    recordId: str | None, featureId: str | None = None, warnings: tuple[str, ...] = (),
) -> AnnotationEdit:
    if revision.moleculeDeclaration is None:
        payload.annotations.pop("molecule_type", None)
    edited = SequenceRevision.fromStoredRecord(
        payload, moleculeType=revision.moleculeType,
        recordId=revision.recordId if recordId is None else recordId,
        revisionId=str(uuid4()), featureIds=tuple(featureIds),
        sourceArtifactId=revision.sourceArtifactId, sourceOrdinal=revision.sourceOrdinal,
        sourcePath=revision.sourcePath, sourceFormat=revision.sourceFormat,
        duplexGeometry=revision.duplexGeometry,
    )
    return AnnotationEdit(edited, featureId, warnings)


def _featureIds(revision: SequenceRevision) -> list[str]:
    return [feature.featureId for feature in revision.features]


def addAnnotation(
    revision: SequenceRevision, *, label: str, featureType: str,
    parts: tuple[SelectionSegment, ...], strand: int | None = None, color: str | None = None,
    qualifiers: dict[str, object] | None = None, recordId: str | None = None,
) -> AnnotationEdit:
    """Add a manual annotation on exact zero-based, half-open parts in strand order."""
    feature = SeqFeature(
        _location(revision, parts, strand), type=_featureType(featureType),
        qualifiers={"label": [_label(label)], ANNOTATION_CURATION_QUALIFIER: ["manual"]},
    )
    _changeQualifiers(feature, qualifiers)
    _changeColor(feature, color)
    payload = revision.toSeqRecord()
    payload.features.append(feature)
    featureId = str(uuid4())
    return _finishEdit(
        revision, payload, (*_featureIds(revision), featureId), recordId=recordId,
        featureId=featureId,
    )


def _renameFeature(feature: SeqFeature, projection: SequenceFeature, label: str) -> None:
    name = _label(label)
    if name == projection.label:
        return
    priorLabels = feature.qualifiers.get("label")
    aliases = _qualifierStrings(feature.qualifiers.get(_ALIAS_QUALIFIER))
    if projection.labelSource == "label" and projection.label not in aliases:
        feature.qualifiers[_ALIAS_QUALIFIER] = [*aliases, projection.label]
    if type(priorLabels) in (tuple, list):
        feature.qualifiers["label"] = type(priorLabels)((name, *priorLabels[1:]))
    else:
        feature.qualifiers["label"] = [name]


def _removeStaleTranslation(
    previous: SeqFeature, current: SeqFeature, sequence: Seq, label: str, *, isRna: bool,
) -> tuple[str, ...]:
    if previous.type != "CDS" or "translation" not in previous.qualifiers:
        return ()
    def codingBases(feature: SeqFeature) -> Seq:
        bases = feature.extract(sequence).upper()
        # SeqFeature.extract uses a DNA complement on negative-strand RNA parts.
        return bases.transcribe() if isRna else bases

    if (
        current.type == "CDS"
        and all(
            canonicalMetadata(previous.qualifiers.get(key))
            == canonicalMetadata(current.qualifiers.get(key))
            for key in _TRANSLATION_SETTINGS
        )
        and (previous.location == current.location
             or codingBases(previous) == codingBases(current))
    ):
        return ()
    current.qualifiers.pop("translation", None)
    return (f"Removed the stale translation of CDS {label!r}.",)


def editAnnotation(
    revision: SequenceRevision, featureId: str, *, label: str | None = None,
    featureType: str | None = None, parts: tuple[SelectionSegment, ...] | None = None,
    strand: int | None = None, color: str | None = None,
    qualifiers: dict[str, object] | None = None, recordId: str | None = None,
) -> AnnotationEdit:
    """Edit one annotation by ID, preserving untouched location and qualifier values.

    Supply explicit parts to change the location or strand. Omitted color preserves
    it and empty color clears it. Unsupported imported locations allow metadata edits.
    """
    projection = revision.getFeature(featureId)
    featureIds = _featureIds(revision)
    payload = revision.toSeqRecord()
    index = featureIds.index(featureId)
    original = payload.features[index]
    feature = deepcopy(original)
    if label is not None:
        _renameFeature(feature, projection, label)
    if featureType is not None:
        feature.type = _featureType(featureType)
    if parts is not None:
        if not canEditAnnotationLocation(revision, featureId):
            raise ValueError("This imported annotation supports metadata editing only.")
        feature.location = _location(revision, parts, strand)
    elif strand is not None:
        raise ValueError("Supply ordered parts to change an annotation strand.")
    _changeColor(feature, color)
    _changeQualifiers(feature, qualifiers)
    warnings = _removeStaleTranslation(
        original, feature, payload.seq, projection.label, isRna=revision.moleculeType == "RNA",
    )
    if canonicalFeature(original, len(revision)) == canonicalFeature(feature, len(revision)):
        return AnnotationEdit(revision, featureId)
    if original.location != feature.location or original.type != feature.type:
        feature.qualifiers.pop(LABEL_EVIDENCE_QUALIFIER, None)
    feature.qualifiers.setdefault(ANNOTATION_CURATION_QUALIFIER, ["edited"])
    payload.features[index] = feature
    return _finishEdit(
        revision, payload, featureIds, recordId=recordId, featureId=featureId, warnings=warnings,
    )


def _names(values: Sequence[str]) -> frozenset[str]:
    return frozenset(value.strip().casefold() for value in values if value.strip())


def _featureNames(feature: SequenceFeature) -> frozenset[str]:
    qualifiers = dict(feature.qualifiers)
    names = [feature.sourceId] if feature.sourceId not in ("", "<unknown id>") else []
    for key in (*FEATURE_LABEL_QUALIFIERS, "standard_name", _ALIAS_QUALIFIER):
        names.extend(qualifiers.get(key, ()))
    for value in qualifiers.get("gene_synonym", ()):
        names.extend(value.split(";"))
    return _names(names)


def _referenceDecision(evidence: LabelEvidence) -> tuple:
    return ("reference", evidence.referenceId, evidence.referenceSequenceSha256,
            evidence.parts, evidence.strand)


def _equivalentReference(
    suggestion: LabelSuggestion, referenceId: str, referenceHash: str,
) -> bool:
    return referenceHash == suggestion.evidence.referenceSequenceSha256 and (
        referenceId == suggestion.evidence.referenceId
        or referenceId in suggestion.equivalentReferenceIds
    )


def _decisions(payload: SeqRecord | SequenceRevision) -> tuple[tuple, ...]:
    entries = payload.annotations.get(ANNOTATION_DECISIONS_KEY, ())
    if not isinstance(entries, (tuple, list)):
        return ()
    return tuple(entry for entry in entries if type(entry) is tuple and len(entry) == 5)


def _isSuppressed(
    decision: tuple, suggestion: LabelSuggestion, suggestionNames: frozenset[str],
) -> bool:
    kind, identity, signature, parts, strand = decision
    if (parts, strand) != (suggestion.parts, suggestion.strand):
        return False
    if kind == "reference":
        return _equivalentReference(suggestion, identity, signature)
    return (
        kind == "annotation" and identity == suggestion.featureType.casefold()
        and type(signature) is tuple
        and any(type(name) is str and name in suggestionNames for name in signature)
    )


def _storeDecisions(payload: SeqRecord, decisions: Sequence[tuple]) -> None:
    if decisions:
        payload.annotations[ANNOTATION_DECISIONS_KEY] = tuple(decisions)
    else:
        payload.annotations.pop(ANNOTATION_DECISIONS_KEY, None)


def removeAnnotation(
    revision: SequenceRevision, featureId: str, *, recordId: str | None = None,
) -> AnnotationEdit:
    """Remove one annotation and retain its catalogue/removal decision at that locus."""
    feature = revision.getFeature(featureId)
    featureIds = _featureIds(revision)
    index = featureIds.index(featureId)
    payload = revision.toSeqRecord()
    del payload.features[index]
    del featureIds[index]
    locus = _featureLocus(feature)
    decisions = list(_decisions(payload))
    if feature.labelEvidence is not None:
        decisions.append(_referenceDecision(feature.labelEvidence))
    elif locus is not None:
        decisions.append(("annotation", feature.type.casefold(),
                          tuple(sorted(_featureNames(feature))), *locus))
    if decisions:
        _storeDecisions(payload, decisions)
    return _finishEdit(revision, payload, featureIds, recordId=recordId)


def _suggestion(hit: FeatureHit, release: str) -> LabelSuggestion:
    record = hit.record
    parts = hit.segments[::-1] if hit.strand == -1 else hit.segments
    evidence = LabelEvidence(
        release, record.id, record.source.id, record.sourceRecord, record.sequenceSha256,
        parts, hit.strand,
    )
    key = json.dumps(_referenceDecision(evidence), separators=(",", ":"))
    return LabelSuggestion(
        sha256(key.encode("utf-8")).hexdigest(), record.label,
        _KIND_TO_TYPE.get(record.kind, record.kind), record.aliases, evidence,
    )


def _groupSuggestions(
    hits: Sequence[FeatureHit], release: str, isCanceled: Callable[[], bool] | None,
) -> dict[tuple[FeatureLocus, str], list[LabelSuggestion]]:
    groups: dict[tuple[FeatureLocus, str], list[LabelSuggestion]] = {}
    for index, hit in enumerate(hits):
        if isCanceled is not None and index % 256 == 0 and isCanceled():
            raise InterruptedError("Label detection was canceled.")
        item = _suggestion(hit, release)
        group = groups.setdefault(((item.parts, item.strand), item.featureType.casefold()), [])
        equivalent = [
            previous for previous in group
            if previous.evidence.referenceSequenceSha256 == item.evidence.referenceSequenceSha256
            and (
                item.label.casefold() in _names((previous.label, *previous.aliases))
                or previous.label.casefold() in _names(item.aliases)
            )
        ]
        if not equivalent:
            group.append(item)
            continue
        # A reviewed synonym links a canonical name. Shared family aliases alone
        # cannot distinguish different references matching the same bases.
        primary, *others = [*equivalent, item]
        aliases = list(primary.aliases)
        referenceIds = list(primary.equivalentReferenceIds)
        for synonym in others:
            aliases.extend((synonym.label, *synonym.aliases))
            referenceIds.extend((synonym.evidence.referenceId, *synonym.equivalentReferenceIds))
        primaryIndex = group.index(primary)
        group[:] = [previous for previous in group if previous not in equivalent]
        group.insert(primaryIndex, replace(
            primary, aliases=tuple(dict.fromkeys(aliases)),
            equivalentReferenceIds=tuple(dict.fromkeys(referenceIds)),
        ))
    return groups


def _reviewSuggestion(
    suggestion: LabelSuggestion,
    features: Sequence[tuple[SequenceFeature, FeatureLocus | None, frozenset[str]]],
    decisions: Sequence[tuple], uniqueNames: frozenset[str], hasVariants: bool,
) -> LabelSuggestion:
    matched, overlapping = [], []
    for feature, locus, featureNames in features:
        exact = locus == (suggestion.parts, suggestion.strand)
        evidence = feature.labelEvidence
        if evidence is None:
            matches = (
                feature.type.casefold() == suggestion.featureType.casefold()
                and bool(uniqueNames & featureNames)
            )
        else:
            matches = _equivalentReference(
                suggestion, evidence.referenceId, evidence.referenceSequenceSha256,
            )
        if exact and matches:
            matched.append(feature.featureId)
        elif feature.type.casefold() != "source" and any(
            part.isSelectable and start < part.end and part.start < end
            for part in feature.parts for start, end in suggestion.parts
        ):
            overlapping.append(feature.featureId)

    status: LabelStatus = "new"
    if matched:
        status = "already_annotated"
    elif any(_isSuppressed(decision, suggestion, uniqueNames) for decision in decisions):
        status = "dismissed"
    elif hasVariants:
        status = "ambiguous"
    elif overlapping:
        status = "overlap"
    return replace(
        suggestion, status=status, matchedFeatureIds=tuple(matched),
        overlappingFeatureIds=tuple(overlapping), _uniqueNames=uniqueNames,
    )


def scanLabels(
    revision: SequenceRevision, catalogue: FeatureCatalogue, *,
    isCanceled: Callable[[], bool] | None = None,
) -> LabelReview:
    """Read the catalogue for exact matches, leaving sequence and curation unchanged."""
    if revision.moleculeType != "DNA":
        raise ValueError("Catalogue label detection requires a DNA record.")
    circular = revision.topology == "circular"
    release = catalogue.release
    hits = catalogue.scan(revision.sequence, circular=circular, isCanceled=isCanceled)
    suggestions = _groupSuggestions(hits, release, isCanceled)
    features = [
        (
            feature,
            None if revision.hasUnverifiedFeatureTraversal(feature) else _featureLocus(feature),
            _featureNames(feature),
        )
        for feature in revision.features
    ]
    decisions = _decisions(revision)
    reviewed = []
    for group in suggestions.values():
        namesBySuggestion = [_names((item.label, *item.aliases)) for item in group]
        nameCounts = Counter(name for names in namesBySuggestion for name in names)
        for item, names in zip(group, namesBySuggestion, strict=True):
            if isCanceled is not None and isCanceled():
                raise InterruptedError("Label detection was canceled.")
            uniqueNames = frozenset(name for name in names if nameCounts[name] == 1)
            reviewed.append(
                _reviewSuggestion(item, features, decisions, uniqueNames, len(group) > 1)
            )
    wholePlasmids = catalogue.identify(revision.sequence, circular=True) if circular else ()
    if isCanceled is not None and isCanceled():
        raise InterruptedError("Label detection was canceled.")
    return LabelReview(revision.revisionId, release, tuple(reviewed), wholePlasmids)


def _reviewSelection(
    revision: SequenceRevision, review: LabelReview, suggestionIds: tuple[str, ...] | None,
) -> tuple[LabelSuggestion, ...]:
    if review.revisionId != revision.revisionId:
        raise ValueError("Run label detection again for the current revision.")
    if suggestionIds is None:
        return tuple(item for item in review.suggestions if item.status == "new")
    available = {item.suggestionId: item for item in review.suggestions}
    if any(suggestionId not in available for suggestionId in suggestionIds):
        raise ValueError("Choose suggestions from this label review.")
    return tuple(available[suggestionId] for suggestionId in dict.fromkeys(suggestionIds))


def acceptLabels(
    revision: SequenceRevision, review: LabelReview, *,
    suggestionIds: tuple[str, ...] | None = None, recordId: str | None = None,
) -> AnnotationEdit:
    """Adopt explicit suggestions, or all new unambiguous matches when IDs are omitted."""
    selected = tuple(
        item for item in _reviewSelection(revision, review, suggestionIds)
        if item.status != "already_annotated"
    )
    if not selected:
        return AnnotationEdit(revision)
    payload = revision.toSeqRecord()
    featureIds = _featureIds(revision)
    decisions = _decisions(payload)
    for item in selected:
        feature = SeqFeature(
            _location(revision, item.parts, item.strand), type=_featureType(item.featureType),
            qualifiers={
                "label": [_label(item.label)], ANNOTATION_CURATION_QUALIFIER: ["catalogue"],
                LABEL_EVIDENCE_QUALIFIER: item.evidence.qualifierValues(),
            },
        )
        if item.aliases:
            feature.qualifiers[_ALIAS_QUALIFIER] = list(item.aliases)
        payload.features.append(feature)
        featureIds.append(str(uuid4()))
        decisions = tuple(
            decision for decision in decisions
            if not _isSuppressed(decision, item, item._uniqueNames)
        )
    _storeDecisions(payload, decisions)
    return _finishEdit(revision, payload, featureIds, recordId=recordId, featureId=featureIds[-1])


def dismissLabels(
    revision: SequenceRevision, review: LabelReview, *,
    suggestionIds: tuple[str, ...], recordId: str | None = None,
) -> AnnotationEdit:
    """Record dismissed suggestions in the sequence revision history alongside feature edits."""
    selected = _reviewSelection(revision, review, suggestionIds)
    added = tuple(
        _referenceDecision(item.evidence) for item in selected
        if item.status not in ("already_annotated", "dismissed")
    )
    if not added:
        return AnnotationEdit(revision)
    payload = revision.toSeqRecord()
    _storeDecisions(payload, (*_decisions(payload), *added))
    return _finishEdit(revision, payload, _featureIds(revision), recordId=recordId)
