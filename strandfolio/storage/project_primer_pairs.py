"""Store a project's named pairs while sharing immutable primer revisions with PCR runs."""

import sqlite3

from strandfolio.domain.primers import SavedPrimerPair


SAVED_PAIR_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS saved_primer_pairs (
        pair_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(pair_id)) > 0),
        name TEXT,
        forward_primer_revision_id TEXT REFERENCES pcr_primer_revisions(revision_id),
        reverse_primer_revision_id TEXT REFERENCES pcr_primer_revisions(revision_id),
        CHECK ((name IS NULL AND forward_primer_revision_id IS NULL AND reverse_primer_revision_id IS NULL)
            OR (name IS NOT NULL AND length(trim(name)) > 0
                AND forward_primer_revision_id IS NOT NULL AND reverse_primer_revision_id IS NOT NULL))
    )""",
    "CREATE INDEX IF NOT EXISTS saved_pairs_by_forward ON saved_primer_pairs (forward_primer_revision_id)",
    "CREATE INDEX IF NOT EXISTS saved_pairs_by_reverse ON saved_primer_pairs (reverse_primer_revision_id)",
)


def unownedPrimerCondition(*, includeSavedPairs: bool) -> str:
    """Select revisions with no remaining run or library ownership in this schema."""
    tables = ("pcr_runs", "saved_primer_pairs") if includeSavedPairs else ("pcr_runs",)
    return " AND ".join(
        f"NOT EXISTS (SELECT 1 FROM {table} WHERE {role}_primer_revision_id = pcr_primer_revisions.revision_id)"
        for table in tables for role in ("forward", "reverse")
    )


class ProjectPrimerPairMixin:
    """Persist ordered oligos and guard changes against edits in another process."""

    def _readPrimerPair(self, connection: sqlite3.Connection, row: sqlite3.Row) -> SavedPrimerPair:
        from strandfolio.storage.projects import ProjectError

        try:
            pair = SavedPrimerPair(
                row["name"], self._loadPrimerRevision(connection, row["forward_primer_revision_id"]),
                self._loadPrimerRevision(connection, row["reverse_primer_revision_id"]), row["pair_id"],
            )
            if pair.name != row["name"]:
                raise ValueError("Invalid stored pair name.")
            return pair
        except (ValueError, TypeError, KeyError) as error:
            raise ProjectError(f"Invalid saved primer pair {row['pair_id']!r}: {error}") from error

    def _listPrimerPairs(self, connection: sqlite3.Connection) -> tuple[SavedPrimerPair, ...]:
        rows = connection.execute(
            "SELECT * FROM saved_primer_pairs WHERE name IS NOT NULL ORDER BY name COLLATE NOCASE, pair_id"
        )
        return tuple(self._readPrimerPair(connection, row) for row in rows)

    def listPrimerPairs(self) -> tuple[SavedPrimerPair, ...]:
        """List active project pairs by label and identity, including duplicate labels."""
        from strandfolio.storage.projects import _projectHeader

        with self._read() as connection:
            return self._listPrimerPairs(connection) if _projectHeader(connection) >= 9 else ()

    def savePrimerPair(
        self, pair: SavedPrimerPair, *, expected: SavedPrimerPair | None = None, timeout: float = 30.0,
    ) -> SavedPrimerPair:
        """Create or replace one pair if its reviewed contents are still current."""
        from strandfolio.storage.projects import ProjectError

        if expected is not None and expected.pairId != pair.pairId:
            raise ProjectError("The reviewed primer pair has a different identity.")
        with self._write(timeout=timeout) as connection:
            row = connection.execute("SELECT * FROM saved_primer_pairs WHERE pair_id=?", (pair.pairId,)).fetchone()
            if row is not None and row["name"] is None:
                raise ProjectError("This primer pair was deleted. Reload the saved pairs.")
            stored = self._readPrimerPair(connection, row) if row is not None else None
            if stored == pair:
                return stored
            if stored != expected:
                raise ProjectError("This primer pair changed. Reload the saved pairs before editing.")
            self._storePrimerRevision(connection, pair.forward)
            self._storePrimerRevision(connection, pair.reverse)
            connection.execute(
                "INSERT INTO saved_primer_pairs "
                "(pair_id, name, forward_primer_revision_id, reverse_primer_revision_id) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(pair_id) DO UPDATE SET name=excluded.name, "
                "forward_primer_revision_id=excluded.forward_primer_revision_id, "
                "reverse_primer_revision_id=excluded.reverse_primer_revision_id",
                (pair.pairId, pair.name, pair.forward.revisionId, pair.reverse.revisionId),
            )
            if stored is not None:
                self._releasePairRevisions(connection, stored)
        return pair

    def deletePrimerPair(self, pair: SavedPrimerPair, *, timeout: float = 30.0) -> None:
        """Remove the reviewed pair, retaining any revisions used by other pairs or recorded runs."""
        from strandfolio.storage.projects import ProjectError

        with self._write(timeout=timeout) as connection:
            row = connection.execute("SELECT * FROM saved_primer_pairs WHERE pair_id=?", (pair.pairId,)).fetchone()
            if row is None or row["name"] is None:
                return
            if self._readPrimerPair(connection, row) != pair:
                raise ProjectError("This primer pair changed. Reload the saved pairs before deleting.")
            # Keep only the identity so a delayed retry cannot recreate the deleted pair.
            connection.execute(
                "UPDATE saved_primer_pairs SET name=NULL, forward_primer_revision_id=NULL, "
                "reverse_primer_revision_id=NULL WHERE pair_id=?", (pair.pairId,),
            )
            self._releasePairRevisions(connection, pair)

    @staticmethod
    def _releasePairRevisions(connection: sqlite3.Connection, old: SavedPrimerPair) -> None:
        connection.execute(
            "DELETE FROM pcr_primer_revisions WHERE revision_id IN (?, ?) AND "
            + unownedPrimerCondition(includeSavedPairs=True),
            (old.forward.revisionId, old.reverse.revisionId),
        )
