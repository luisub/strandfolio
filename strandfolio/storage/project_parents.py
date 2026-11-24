"""Store user-reported parentage separately from immutable snapshots and computed operations."""

from collections.abc import Iterable
from graphlib import CycleError, TopologicalSorter
import sqlite3

from strandfolio.domain.provenance import DeclaredParent


DECLARED_PARENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS declared_parents (
    child_revision_id TEXT NOT NULL REFERENCES named_records(revision_id),
    ordinal INTEGER NOT NULL CHECK (typeof(ordinal) = 'integer' AND ordinal >= 0),
    parent_revision_id TEXT NOT NULL REFERENCES named_records(revision_id),
    source_start INTEGER, source_end INTEGER, product_start INTEGER, product_end INTEGER,
    orientation INTEGER CHECK (orientation IS NULL OR orientation IN (-1, 1)),
    PRIMARY KEY (child_revision_id, ordinal),
    CHECK (child_revision_id <> parent_revision_id),
    CHECK ((source_start IS NULL AND source_end IS NULL AND product_start IS NULL AND product_end IS NULL)
        OR (typeof(source_start) = 'integer' AND typeof(source_end) = 'integer'
            AND typeof(product_start) = 'integer' AND typeof(product_end) = 'integer'
            AND source_start >= 0 AND product_start >= 0 AND source_start < source_end
            AND (source_end - source_start) = (product_end - product_start)))
)
"""


def _namedLength(connection: sqlite3.Connection, revisionId: str, lengths: dict[str, int]) -> int:
    """Read the coordinates of the named snapshot without loading its annotations."""
    from strandfolio.storage.projects import ProjectError

    if revisionId not in lengths:
        row = connection.execute(
            "SELECT json_extract(payload, '$.revision_id'), length(json_extract(payload, '$.sequence')) "
            "FROM named_records JOIN revisions USING (revision_id) WHERE revision_id = ?",
            (revisionId,),
        ).fetchone()
        if row is None:
            raise KeyError(f"No named project revision {revisionId!r}.")
        if row[0] != revisionId or type(row[1]) is not int:
            raise ProjectError(f"Inconsistent stored revision {revisionId!r}.")
        lengths[revisionId] = row[1]
    return lengths[revisionId]


def _validateBounds(
    connection: sqlite3.Connection, childId: str, parents: tuple[DeclaredParent, ...],
    lengths: dict[str, int],
) -> None:
    from strandfolio.storage.projects import ProjectError

    childSize = _namedLength(connection, childId, lengths)
    for parent in parents:
        if parent.revisionId == childId:
            raise ProjectError("A revision cannot be its own reported parent.")
        parentSize = _namedLength(connection, parent.revisionId, lengths)
        if parent.sourceSpan is not None and (
            parent.sourceSpan[1] > parentSize or parent.productSpan[1] > childSize
        ):
            raise ProjectError("A reported interval lies outside its saved revision.")


def _readParents(connection: sqlite3.Connection, childId: str) -> tuple[DeclaredParent, ...]:
    from strandfolio.storage.projects import ProjectError

    rows = connection.execute(
        "SELECT ordinal, parent_revision_id, source_start, source_end, product_start, "
        "product_end, orientation FROM declared_parents WHERE child_revision_id = ? ORDER BY ordinal",
        (childId,),
    )
    parents = []
    for index, (ordinal, parentId, start, end, targetStart, targetEnd, orientation) in enumerate(rows):
        if index != ordinal:
            raise ProjectError(f"Invalid reported parent ordering on {childId!r}.")
        source = None if start is None and end is None else (start, end)
        product = None if targetStart is None and targetEnd is None else (targetStart, targetEnd)
        parents.append(DeclaredParent(parentId, source, product, orientation))
    return tuple(parents)


def _readParentGraph(
    connection: sqlite3.Connection, revisionIds: Iterable[str],
) -> dict[str, tuple[DeclaredParent, ...]]:
    from strandfolio.storage.projects import ProjectError

    graph = {}
    pending = list(revisionIds)
    seen = set()
    lengths = {}
    while pending:
        childId = pending.pop()
        if childId in seen:
            continue
        seen.add(childId)
        parents = _readParents(connection, childId)
        if parents:
            _validateBounds(connection, childId, parents, lengths)
            graph[childId] = parents
            pending.extend(parent.revisionId for parent in parents)
    dependencies = {child: tuple(parent.revisionId for parent in parents)
                    for child, parents in graph.items()}
    try:
        TopologicalSorter(dependencies).prepare()
    except CycleError as error:
        raise ProjectError("Reported parent links would form a cycle.") from error
    return graph


def verifyDeclaredParents(connection: sqlite3.Connection) -> None:
    """Check all reported links during project open and independent backup verification."""
    children = connection.execute("SELECT DISTINCT child_revision_id FROM declared_parents")
    _readParentGraph(connection, (row[0] for row in children))


class ProjectParentMixin:
    """Access ordered parent occurrences declared by the user for saved records."""

    def declaredParents(self, childRevisionId: str) -> tuple[DeclaredParent, ...]:
        """Return direct reported parents in order. An unnamed child raises KeyError."""
        from strandfolio.storage.projects import _projectHeader

        with self._read() as connection:
            parents = _readParents(connection, childRevisionId) if _projectHeader(connection) >= 8 else ()
            _validateBounds(connection, childRevisionId, parents, {})
            return parents

    def declaredParentGraph(self, revisionIds: Iterable[str]) -> dict[str, tuple[DeclaredParent, ...]]:
        """Read reachable reported links in one snapshot, skipping roots without declarations."""
        from strandfolio.storage.projects import _projectHeader

        with self._read() as connection:
            return _readParentGraph(connection, revisionIds) if _projectHeader(connection) >= 8 else {}

    def setDeclaredParents(
        self, childRevisionId: str, parents: tuple[DeclaredParent, ...],
        *, expected: tuple[DeclaredParent, ...], timeout: float = 30.0,
    ) -> None:
        """Replace the reported parents if the stored version matches the caller's version.

        Repeating an already committed change succeeds. Missing named snapshots
        raise KeyError. Stale edits, invalid mappings and cycles abort the transaction.
        """
        from strandfolio.storage.projects import ProjectError

        for entries in (parents, expected):
            if type(entries) is not tuple or any(type(entry) is not DeclaredParent for entry in entries):
                raise ProjectError("Reported parents must be an ordered tuple of DeclaredParent entries.")
        with self._write(timeout=timeout) as connection:
            _validateBounds(connection, childRevisionId, parents, {})
            current = _readParents(connection, childRevisionId)
            if current == parents:
                # The write context may have staged a schema upgrade for this no-op.
                connection.rollback()
                return
            if current != expected:
                raise ProjectError("Reported parents have changed. Reopen them before saving.")
            connection.execute(
                "DELETE FROM declared_parents WHERE child_revision_id = ?", (childRevisionId,),
            )
            for index, parent in enumerate(parents):
                connection.execute(
                    "INSERT INTO declared_parents VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (childRevisionId, index, parent.revisionId, *(parent.sourceSpan or (None, None)),
                     *(parent.productSpan or (None, None)), parent.orientation),
                )
            _readParentGraph(connection, (childRevisionId,))
