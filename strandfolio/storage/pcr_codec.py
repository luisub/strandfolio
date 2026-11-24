"""Encode PCR evidence independently from the simulation engine and revision payloads."""

from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json

from strandfolio.domain.coordinates import SequenceSelection
from strandfolio.domain.fragment_annotations import FragmentFeatureMapping
from strandfolio.domain.primers import PrimerBinding, PrimerRevision
from strandfolio.domain.sequence import SequenceRevision
from strandfolio.operations.pcr import PcrProduct, PcrRun, PcrSettings, PcrSourceMapping, validatePcrEvidence
from strandfolio.storage.revision_codec import encodeCanonicalJson, encodeRevision


_RUN_SCHEMA = "vibegene.project.pcr.v1"
_PRIMER_SCHEMA = "vibegene.project.primer.v1"


def _texts(values: object) -> tuple[str, ...]:
    if type(values) is not list or any(type(value) is not str for value in values):
        raise ValueError("Expected ordered PCR text values.")
    return tuple(values)


def encodePrimerRevision(primer: PrimerRevision) -> str:
    """Serialize the immutable primer independently of its role in a run."""
    if type(primer) is not PrimerRevision:
        raise TypeError("Expected a primer revision.")
    return encodeCanonicalJson({
        "schema": _PRIMER_SCHEMA,
        "primer_id": primer.primerId,
        "revision_id": primer.revisionId,
        "name": primer.name,
        "sequence": primer.sequence,
        "tail_length": primer.tailLength,
        "modifications": primer.modifications,
    })


def decodePrimerRevision(payload: str) -> PrimerRevision:
    """Restore an exact primer revision and reject noncanonical or unknown fields."""
    value = json.loads(payload)
    if value["schema"] != _PRIMER_SCHEMA:
        raise ValueError("Unsupported primer revision schema.")
    primer = PrimerRevision(
        name=value["name"], sequence=value["sequence"], tailLength=value["tail_length"],
        modifications=_texts(value["modifications"]), primerId=value["primer_id"],
        revisionId=value["revision_id"],
    )
    if encodePrimerRevision(primer) != payload:
        raise ValueError("Invalid recorded primer revision.")
    return primer


def _primerReference(primer: PrimerRevision) -> list[str]:
    return [primer.revisionId, sha256(encodePrimerRevision(primer).encode("ascii")).hexdigest()]


def _revisionReference(revision: SequenceRevision) -> list[str]:
    return [revision.revisionId, sha256(encodeRevision(revision).encode("utf-8")).hexdigest()]


def _targetDocument(target: SequenceSelection | None) -> dict[str, object] | None:
    if target is None:
        return None
    document = {
        "revision_id": target.revisionId, "anchor": target.anchor, "caret": target.caret,
        "feature_id": target.featureId, "part_index": target.partIndex, "segments": target.segments,
        "whole_feature": target.wholeFeature, "strand": target.strand,
    }
    if target.retainsReverseTraversal:
        document["retains_reverse_traversal"] = True
    return document


def _readTarget(value: dict[str, object] | None) -> SequenceSelection | None:
    if value is None:
        return None
    if type(value["whole_feature"]) is not bool or (
        value["strand"] is not None and type(value["strand"]) is not int
    ):
        raise ValueError("Invalid saved PCR target flags.")
    return SequenceSelection(
        value["revision_id"], value["anchor"], value["caret"], value["feature_id"],
        value["part_index"], _segments(value["segments"]), value["whole_feature"], value["strand"],
        retainsReverseTraversal=value.get("retains_reverse_traversal", False),
    )


def _segments(value: list[list[int]]) -> tuple[tuple[int, int], ...]:
    return tuple((start, end) for start, end in value)


def _settingsDocument(settings: PcrSettings) -> dict[str, object]:
    return {
        "binding_mode": settings.bindingMode, "min_product_bp": settings.minProductBp,
        "max_product_bp": settings.maxProductBp, "max_template_bp": settings.maxTemplateBp,
        "max_bindings": settings.maxBindings, "max_pairings": settings.maxPairings,
        "max_products": settings.maxProducts,
    }


def _readSettings(value: dict[str, object]) -> PcrSettings:
    return PcrSettings(
        bindingMode=value["binding_mode"], minProductBp=value["min_product_bp"],
        maxProductBp=value["max_product_bp"], maxTemplateBp=value["max_template_bp"],
        maxBindings=value["max_bindings"], maxPairings=value["max_pairings"],
        maxProducts=value["max_products"],
    )


def _bindingDocument(binding: PrimerBinding) -> dict[str, object]:
    return {
        "template_revision_id": binding.templateRevisionId,
        "primer_revision_id": binding.primerRevisionId, "strand": binding.strand,
        "source_segments": binding.sourceSegments, "annealing_sequence": binding.annealingSequence,
        "tail_sequence": binding.tailSequence,
    }


def _readBinding(value: dict[str, object]) -> PrimerBinding:
    return PrimerBinding(
        value["template_revision_id"], value["primer_revision_id"], value["strand"],
        _segments(value["source_segments"]), value["annealing_sequence"], value["tail_sequence"],
    )


def _productDocument(product: PcrProduct, bindings: dict[PrimerBinding, int]) -> dict[str, object]:
    return {
        "operation_id": product.operationId, "output_key": product.outputKey,
        "revision": _revisionReference(product.revision),
        "orientation": product.orientation, "source_segments": product.sourceSegments,
        "forward_binding": bindings[product.forwardBinding],
        "reverse_binding": bindings[product.reverseBinding],
        "source_mappings": [
            [item.role, item.sourceRevisionId, item.sourceSpan, item.productSpan, item.orientation]
            for item in product.sourceMappings
        ],
        "feature_mappings": [
            [item.featureId, item.sourceFeatureId, item.partial] for item in product.featureMappings
        ],
        "warnings": product.warnings,
    }


def _readProduct(
    value: dict[str, object], key: str, revision: SequenceRevision, bindings: tuple[PrimerBinding, ...],
) -> PcrProduct:
    if value["output_key"] != key or value["revision"] != _revisionReference(revision):
        raise ValueError("A PCR output differs from its linked revision.")
    indexes = (value["forward_binding"], value["reverse_binding"])
    if any(type(index) is not int or not 0 <= index < len(bindings) for index in indexes):
        raise ValueError("A PCR output refers to an unrecorded binding.")
    forward, reverse = (bindings[index] for index in indexes)
    return PcrProduct(
        operationId=value["operation_id"], outputKey=key, revision=revision,
        orientation=value["orientation"], sourceSegments=_segments(value["source_segments"]),
        forwardBinding=forward, reverseBinding=reverse,
        sourceMappings=tuple(
            PcrSourceMapping(role, source, (start, end), (productStart, productEnd), orientation)
            for role, source, (start, end), (productStart, productEnd), orientation in value["source_mappings"]
        ),
        featureMappings=tuple(FragmentFeatureMapping(*item) for item in value["feature_mappings"]),
        warnings=_texts(value["warnings"]),
    )


def encodePcrRun(run: PcrRun) -> str:
    """Record exact inputs, settings, outputs and evidence without embedding revision bytes."""
    if type(run) is not PcrRun:
        raise TypeError("Expected a PCR run.")
    bindings = {binding: index for index, binding in enumerate(run.bindings)}
    return encodeCanonicalJson({
        "schema": _RUN_SCHEMA, "operation_id": run.operationId, "created_at": run.createdAt,
        "template": _revisionReference(run.template),
        "forward_primer": _primerReference(run.forwardPrimer),
        "reverse_primer": _primerReference(run.reversePrimer),
        "target": _targetDocument(run.target), "settings": _settingsDocument(run.settings),
        "status": run.status, "engine_version": run.engineVersion, "assumptions": run.assumptions,
        "bindings": [_bindingDocument(item) for item in run.bindings],
        "products": [_productDocument(item, bindings) for item in run.products],
        "warnings": run.warnings,
    })


def decodePcrRun(
    payload: str, template: SequenceRevision, forwardPrimer: PrimerRevision,
    reversePrimer: PrimerRevision, outputs: tuple[tuple[str, SequenceRevision], ...], *,
    checkCompleteness: bool = False,
) -> PcrRun:
    """Check the stored links and restore the recorded result without rerunning PCR."""
    value = json.loads(payload)
    if value["schema"] != _RUN_SCHEMA:
        raise ValueError("Unsupported PCR run schema.")
    if datetime.fromisoformat(value["created_at"]).utcoffset() != timedelta(0):
        raise ValueError("A PCR run requires a UTC creation time.")
    if (value["template"] != _revisionReference(template)
        or value["forward_primer"] != _primerReference(forwardPrimer)
        or value["reverse_primer"] != _primerReference(reversePrimer)):
        raise ValueError("The PCR inputs differ from their linked revisions.")
    if len(value["products"]) != len(outputs):
        raise ValueError("The PCR run's output count differs from its linked revisions.")
    bindings = tuple(_readBinding(item) for item in value["bindings"])
    run = PcrRun(
        operationId=value["operation_id"], createdAt=value["created_at"], template=template,
        forwardPrimer=forwardPrimer, reversePrimer=reversePrimer, target=_readTarget(value["target"]),
        settings=_readSettings(value["settings"]), status=value["status"], bindings=bindings,
        products=tuple(
            _readProduct(item, key, revision, bindings)
            for item, (key, revision) in zip(value["products"], outputs, strict=True)
        ),
        warnings=_texts(value["warnings"]), engineVersion=value["engine_version"],
        assumptions=_texts(value["assumptions"]),
    )
    validatePcrEvidence(run, checkCompleteness=checkCompleteness)
    if encodePcrRun(run) != payload:
        raise ValueError("Invalid recorded PCR run fields.")
    return run
