# Import and export sequences

The shared importer reads GenBank, FASTA, explicitly typed text and binary SnapGene DNA files into isolated `SequenceRevision` objects. The shared writer exports a single revision to GenBank, FASTA or plain text. Python, [notebook 01](notebooks/01_import_export.ipynb) and the desktop workspace use the same adapter. The desktop opens files in independent record tabs and confirms a new multi-record batch before adding it. Native projects retain captured originals and exact named revisions. [Python project API](#save-and-reopen-a-project)

## Environment and setup

Install Python dependencies and launch the app from the checkout root using the
[installation guide](installation/README.md). It covers the project-local environment
and the Conda alternative. New digests also need the local enzyme catalogue.
[Data requirements](../databases/README.md#data-for-a-local-source-checkout)

### Notebook kernel and checks

Use the same interpreter for the app and for the notebooks.
[Register and check the notebook kernel](installation/README.md#notebook-kernel-and-checks)

## Run the example

For automatic dispatch between supported formats:

```python
from pathlib import Path

from strandfolio.io.formats import readSequence

result = readSequence(Path("databases/plasmids/maps/pUC19.gb"))
sequence = result.records[0]

print(result.formatName, sequence.name, len(sequence), sequence.topology)
print(len(sequence.features), result.warnings)
```

The default `moleculeType=None` interprets FASTA automatically for each record. In generic `.fasta`, `.fa` and `.fas` files, DNA-compatible letters default to DNA, U without T indicates RNA, and other supported amino-acid letters indicate protein. Nucleotide-compatible letters are also compatible with some proteins. `result.warnings` and **File → Import details…** flag that inference as ambiguous. A nucleotide-only record containing both T and U is rejected when automatic interpretation is ambiguous.

`.faa` supplies a protein hint and `.frn` an RNA hint; the selected alphabet is validated, with no fallback to another type. No type is guessed from header descriptions. For a known ambiguous FASTA, Python callers can explicitly set `moleculeType="DNA"`, `"RNA"` or `"protein"`; this override applies to every record and takes precedence over a filename hint. The optional CLI `--molecule-type` offers the same override. Ordinary desktop FASTA intake also interprets each record automatically. Headerless plain text prompts for its molecule type unless the launcher already received `--molecule-type`. **File → Paste sequence…** opens a separate capture for explicitly typed clipboard text. Any formatting changes are shown before intake.

SnapGene intake uses the file's declared DNA metadata and ignores a FASTA override. A SnapGene cookie selects binary intake. A GenBank LOCUS selects GenBank, including under a mismatched extension. Other `.dna` candidates undergo binary validation. A FASTA header under `.txt` selects FASTA. Explicit protein text intake accepts literal amino-acid groups such as `LOCUS ACDEFG`. A leading UTF-8 byte order mark in a plain-text file is preserved in the original capture and excluded from the sequence letters.

## Available interface

Python interfaces below use camelCase. Biopython source keys such as `annotations["molecule_type"]` retain their original spelling.

| Interface | Behavior |
| --- | --- |
| `readSequence(path, *, moleculeType=None, formatName=None, confirmNormalization=None, ...)` | Recognize GenBank, FASTA or binary SnapGene and validate the entire batch. For headerless text, pass `formatName="text"` and a molecule type. Use the confirmation callback when removing whitespace. |
| `readGenbank(path)` | Read one or more complete annotated DNA records in source order |
| `readText(path, *, moleculeType, confirmNormalization=...)`, `readPastedText(text, ...)` | Read one explicitly typed literal sequence. Review grouping and line breaks before accepting normalized input |
| `writeSequence(revision, path, *, formatName=None, header=None, confirm=None, protectedPaths=())` | Stage one export beside its destination, reopen it and present the fidelity report before replacement |
| `readFasta(path, *, moleculeType=None)` | Read FASTA with automatic interpretation, or one explicit type override for every record |
| `readSnapgene(path)` | Read one binary SnapGene DNA record |
| `SequenceImportResult` | `records`, `captureBytes`, `sourcePath`, `source` (original filename, byte count and SHA-256), `warnings`, `biopythonVersion` and `formatName` |
| `sequence.sequence`, `len(sequence)` | Stored letters (GenBank normalizes case) and order; nucleotide count or protein residue count |
| `sequence.name`, `sourceId`, `description` | Imported identity/header; SnapGene missing/unknown identifiers fall back to the filename stem |
| `recordId`, `revisionId`, `contentDigest` | Fresh intake identities and a versioned fingerprint; equal content may have different generated IDs |
| `moleculeType`, `topology`, `strandState` | Validated interpretation (inferred or explicitly supplied for FASTA), declared topology or `None`, duplex for `ds-DNA` and otherwise unknown for nucleic acids |
| `moleculeDeclaration`, `references`, `dbxrefs` | Deposited molecule declaration, immutable ordered citations with zero-based half-open ranges, and record-level database cross-references |
| `features`, `getFeature(featureId)` | Immutable feature projections with snapshot-local IDs, ordered parts, strands, labels and display qualifiers |
| `annotations`, `toSeqRecord()` | Independent mutable copies; changes cannot alter the snapshot |
| `SequenceRevision.fromSeqRecord(record, moleculeType=...)` | Capture a fully specified record with supported metadata/features; reject unsupported content explicitly |

`FeaturePart` reports exactness/selectability along with zero-based half-open bounds and the original location text. Only exact, nonempty local parts are selectable. Compound, fuzzy, remote and unknown locations remain represented; a multipart feature is never reduced to its bounding interval. Projection qualifier values are display strings; `toSeqRecord()` retains their supported original types.

A standalone read allocates fresh IDs. Accepting a known source in a project returns its existing intake IDs. A first named save preserves record and feature identities, including when a new name requires a distinct revision. The `vibegene.sequence.v3` fingerprint covers supported features, publication ranges, cross-references and strand evidence. Reimporting identical content from a new location preserves that digest. Fragment candidates with explicit duplex geometry use a v4 content fingerprint that includes strand spans and ends. Native saves preserve that geometry. Sequence exchange writes the aligned forward reference and reports the omitted physical information before publication.

The private `_record` is implementation state. Use `toSeqRecord()` for external work. This ownership boundary protects ordinary API use; it is not a security sandbox against deliberate private-attribute mutation.

## Supported inputs and fidelity

File intake accepts regular files, including hard links and symbolic links to regular files. A missing link target, directory, named pipe or device produces a read error. For a symbolic link, the capture keeps the selected filename and the resolved source path.

FASTA extensions `.fasta`, `.fa`, `.fas`, `.fna`, `.faa` and `.frn` work case-insensitively. Valid FASTA content under another extension, including `.txt`, is accepted with a warning. Headerless input requires explicit text mode and a molecule type. UTF-8, optional BOM, common newlines, wrapped lines, multiple records and IUPAC ambiguity letters are supported. Protein input accepts standard/ambiguous amino-acid letters, U/O and `*`.

Empty records, NUL bytes in headers, comments, gaps, digits, non-ASCII sequence symbols, whitespace inside sequence lines and malformed content are rejected. One invalid record rejects the whole intake. FASTA carries no structured annotations, topology, duplex ends or history.

SnapGene intake preserves the fields exposed by the installed Biopython reader: DNA, declared topology, supported features/ordered locations, qualifiers and notes. It is **not a lossless SnapGene importer**:

- Appearance, feature colors, source history, some notes/attributes and gap segments are not retained by the parser. Unknown packet types are reported when present.
- Import details report HTML-only feature qualifier values and nondirectional or bidirectional features. The original byte capture retains those fields. The parser displays these directions as forward and the export review reports changes when it unfolds multiline SnapGene comments into a GenBank definition.
- Imported `primer_bind` entries are binding annotations, not a primer library. The reader filters binding sites; full oligo sequences and thermal metadata are not retained.
- Imported translation qualifiers are source values, not recomputed translations. Strand/duplex state and terminal chemistry remain unknown.
- A reverse-strand feature crossing the circular origin can have uncertain part order. Stored copy joins its selected parts in imported order. For that whole feature, **Copy rev-comp** and CDS translation report the uncertainty and return no sequence. You can still select and copy an individual exact segment. Strandfolio carries this uncertainty with the annotation into new digest and PCR products and discloses it in the GenBank export review. Strandfolio recognizes the warning on reopening. If an older standalone output has already lost that evidence, reopen its original SnapGene source to recover the warning.

The original `.dna` remains authoritative. Review `result.warnings` or **File → Import details…** for the intake report. Non-DNA SnapGene cookie kinds, duplicate cookie/DNA packets, origin-wrapping segments on a linear record and corrupt or truncated files fail visibly without replacing the previous GUI document.

Per-letter annotations are outside the current snapshot boundary. New snapshots reject unsupported metadata types, local coordinate bounds outside the record and incompatible molecule metadata. Previously accepted native snapshots can still be reopened. Complement, reverse complement and validated CDS translation have their own controls and source-location limits. SnapGene binary writing remains unsupported.

Plain text and FASTA carry sequence letters without feature annotations. After opening an unannotated DNA record, the desktop searches for open reading frames. Choose the candidates to add to your draft, then save a named record in a project to retain the annotations.

The Python readers default to 64 MiB per capture and 200 records per batch. The desktop also limits rendered sequence letters.

## Annotated GenBank and sequence-only exports

Open a GenBank file through **Open…**, the launcher or `readSequence`. The shared reader checks record framing, declared length, DNA letters, topology, feature locations, qualifiers and citation structure before returning any revision. A batch fails if a record contains an embedded NUL, a malformed or repeated `VERSION` field, multiple `SOURCE` fields, or multiple `PUBMED` identifiers in one citation. If a later record is malformed, the error identifies its one-based record number and the entire import fails. Each revision has a one-based `sourceOrdinal`. The import result keeps the full capture in memory, including fields outside the parser's model. File intake records the supplied filename and the resolved file it read. Project registration uses those captured bytes even if the file or its link changes during review.

GenBank intake normalizes ORIGIN letters to uppercase. A `ds-DNA` declaration establishes duplex strand state. Bare `DNA` leaves that state unknown. Missing topology stays unknown. The snapshot preserves feature order, per-part strands and locations, repeated qualifiers, ordered citations with ranges and separate record-level cross-references. Fuzzy and remote locations can be inspected. A between-base site across a remote origin requires the other record’s length and topology, so intake reports that case as unsupported. Only resolved local intervals support exact sequence selection.

For a saved project record, use **File → Export sequence…** to review the exchange file in the desktop. **Export figure…** saves an SVG or PNG. The Python writer also accepts revisions directly. The following example lets the caller review a staged annotated copy before publication.

```python
from pathlib import Path
from strandfolio.io.formats import readSequence, writeSequence

source = Path("databases/plasmids/maps/pBH4-His-SUMO-GFP-LPETGG.gb")
revision = readSequence(source).records[0]

def approveExport(report):
    print(report.destinationPath, report.emittedName)
    for message in (*report.warnings, *report.serializationDetails):
        print(message)
    return input("Publish this export? [y/N] ").strip().lower() == "y"

result = writeSequence(revision, "GFP-copy.gb", confirm=approveExport)
```

GenBank reopens staged DNA and checks topology, feature locations and supported annotation fields. The report identifies the emitted LOCUS, changed letter case, placeholder fields and omitted multi-range citation coordinates. A remote between-base site such as `REF.1:10^11` keeps its accession and adjacent coordinates, including when the local circle is 10 bases long. A feature that would acquire an asserted strand on reopen blocks publication. FASTA preserves the exact letters. By default, an unchanged FASTA intake keeps its full header and a renamed or annotated record uses its revision name. Pass `header=` to choose another header. Text preserves the letters only. Review the report for inferred molecule type and omitted annotations, topology, primer records and history. A source or protected managed path cannot serve as the export destination. Cancellation or a failed validation leaves any earlier destination intact.

GenBank export generates an identifier for DNA imported from a plain-text file or paste. The fidelity review shows the emitted identifier. The project retains the original source identifier and captured bytes.

Local round trips use the installed Biopython release. Opening, saving and re-exporting the result in a named SnapGene desktop build requires an independent exchange check.

## Save and reopen a project

Use the desktop project controls to create or choose a project, save an imported record under a unique name, and open a named or exact source revision. Preferred display names are project metadata.

The Python repository stores an accepted import, its ordered records, and the exact input bytes inside a portable directory. Keep the original request ID when retrying an action. Generate a fresh request ID for each new action.

```python
from pathlib import Path
from uuid import uuid4

from strandfolio.io.formats import readSequence
from strandfolio.storage.projects import ProjectRepository

project = ProjectRepository.create(Path("MyProject"))
capture = readSequence("strandfolio/data/examples/synthetic_circular.dna")
intake = project.acceptImport(capture, requestId=str(uuid4()))
saved = project.saveRevision(intake.records[0], "Reference circle", requestId=str(uuid4()))

reopened = ProjectRepository.open("MyProject")
original = reopened.getRevision(intake.records[0].revisionId)
named = reopened.getNamedRevision("Reference circle")
assert original.recordId == named.recordId == saved.recordId
assert reopened.getArtifactBytes(intake.source.artifactId) == capture.captureBytes
backup = reopened.backup("MyProject-backup")
```

For a first save from a standalone read, pass `capture=capture` to `saveRevision` with the selected revision. It registers the batch and the first named root together. Names are trimmed and compared with Unicode case folding. An ordinary file reopen with the same origin, bytes and interpretation returns the intake revision, even after naming the root. A new origin, changed bytes, changed interpretation or deliberate independent intake has its own occurrence. Equal bytes share the managed file. Use `listSavedNames()` to discover saved constructs and `listIntakes()` followed by `getIntake(artifactId)` to retrieve an ordered batch without its external path.

Use `findMatchingIntake(capture)` to check whether the project knows a capture before publishing a new import. `listReopenableRevisions()` lists named and captured source snapshots for exact reopening. For a known record ID, `getSavedRevision(recordId)` resolves its named snapshot and `getOriginalName(recordId)` returns the name on its first stored revision. `getPreferredName(recordId)`, `setPreferredName(recordId, name)` and `clearPreferredName(recordId)` manage optional display metadata. `saveRevision(..., preferredName=...)` can carry a standalone preference into the first save transaction. Omitting the argument preserves an existing preference. For a standalone independent occurrence, pass `independent=True` along with the capture.

The project stores the Sequence preview code and the explicit annotated-CDS override by record ID. Read them with `getGeneticCodePreference(recordId)`. An older project or an unset preference uses the annotated codes. A first save can carry the standalone choice through `saveRevision(..., geneticCodePreference=...)`.

Choose **File → Export sequence…** in the desktop or use `project.exportSequence(named, path, requestId=..., confirm=...)` to retain an exchange file and its fidelity report. For an imported source, accept its capture first. A manually created root can be retained as the source of an export. Use **Retry registration** or `project.retryExport(requestId)` to verify delivery against the chosen file when the project recorded an export intent. If the initial transaction failed before recording the request, start a new export. Missing or different bytes leave the request incomplete. A completed request returns its stored receipt even if the external file has moved. A verified delivery records the exact source revision and an artifact ID. Creation attribution requires evidence that the request installed the published file.

Move the complete project directory when relocating it. For a consistent backup of an active project, call `backup` with a new directory path outside the project. Retain that directory independently of the live project. To restore, copy it to a separate location and open the restored directory with `ProjectRepository.open`. Compare the database identity, linked payload hashes and exact record, revision, operation and output identities with the original project. The external input paths can be unavailable during restore. Strandfolio 0.1.4 reads schemas 1–9. Its first successful write to a project created under schemas 1–8 upgrades that copy to schema 9 in the same transaction. Keep a backup in the original schema if you also need to open the project in Strandfolio 0.1.3. A portable digest can retain a source hash and selected snapshot when the original file bytes are unavailable. [Data preservation](../databases/README.md#sharing-and-backup)

## Retrieve a registered original

With a project open, use **File → Retrieve original…** on a record that has a registered capture. Pick a destination folder for the original filename. strandfolio verifies the stored bytes before publishing the full artifact. It preserves an existing file with the same name, and reports when a portable record has a source description but the original bytes are unavailable. Recovering a captured batch retains every record, formatting character and unsupported field present in the source. A clipboard capture has no disk filename and is recovered as `Pasted sequence.txt`. Use **Export sequence…** to serialize the selected revision in a chosen format.

## Exchange and project support

The following fields and limits apply to the macOS arm64 source checkout.

| Format or operation | Fields and limits | Examples and reference |
| --- | --- | --- |
| GenBank DNA intake and single-record export | Bases, declared topology and strand state, accession/version, feature order and parts, repeated qualifiers, cross-references and ordered citations. Export reports case normalization, generated or changed identifiers and omitted multi-range citation coordinates. Export rejects features whose unknown strand would acquire an asserted strand on reopen. Intake rejects undefined sequences. | [GenBank exchange](#annotated-genbank-and-sequence-only-exports) |
| FASTA intake and single-record export | Full header, letter order and case, record order and distinct identities for duplicate headers. DNA, RNA or protein interpretation is inferred per record or explicitly selected. Structured annotations, topology, duplex ends and history fall outside the format. | [Sequence intake notebook](notebooks/01_import_export.ipynb) |
| Explicit text intake and single-record export | Python and desktop text intake require a selected alphabet. The desktop accepts typed files and explicit clipboard pastes. Grouping characters and line breaks require a normalization preview. The project preserves the original bytes and the chosen molecule type. Export carries the letters only. | [Input formats](#supported-inputs-and-fidelity) |
| SnapGene `.dna` intake | One binary DNA record, declared topology, parsed features, ordered parts and supported qualifiers. Uncertain part order limits whole-feature extraction. The original file retains appearance, primer-library data and history. Binary writing is unavailable. Exchange with a named SnapGene desktop build remains unverified. | [Example and expected features](../strandfolio/data/examples/README.md) |
| Retrieve a registered original | Recovers the complete verified project artifact under its captured filename. Rejects missing or invalid captures, managed project destinations and existing files. | [Retrieve an original](#retrieve-a-registered-original) |
| Intake and publication failures | Import requires a regular file at the selected path or its link target. Python defaults to 64 MiB and up to 200 records in a capture. A malformed later record rejects its entire batch. A canceled or failed export preserves the prior destination. Source paths and managed project artifacts are protected. | [Reader and writer API](#available-interface) |
| Native project and registered sequence delivery | Schema 9 stores exact named and source revisions, original bytes, annotation features, display preferences, user-reported parent links and named primer pairs. A pair can be renamed or deleted while earlier PCR runs keep their original member revisions. Registered GenBank, FASTA and text deliveries keep the source revision, artifact ID and fidelity report. Replay uses the same request identity. | [Project API and example](#save-and-reopen-a-project) |
| Backup, restore and migration | Restore the complete SQLite store and linked artifacts with the originals unavailable. Verify hashes and exact IDs. Missing or damaged payloads, interrupted commits, conflicts and newer unsupported schemas produce diagnostics. Schemas 1–8 upgrade on the first successful write from the source checkout. Backups retain reported parent links and their order. | [Backup and restore](#save-and-reopen-a-project) |

## Verification and ownership

Notebook 01 opens the four distributed maps and compares the bases in the labeled
pUC19 map with the M77789.2 deposit. It reopens pUC19 as GenBank, FASTA and text
to show which fields survive, then reads the bundled SnapGene example.
[Sequence intake notebook](notebooks/01_import_export.ipynb) ·
[SnapGene example and expected features](../strandfolio/data/examples/README.md)

[formats.py](../strandfolio/io/formats.py) owns intake and exchange, [sequence.py](../strandfolio/domain/sequence.py) owns snapshots, [features.py](../strandfolio/domain/features.py) owns immutable feature projections/canonical annotation values, and [provenance.py](../strandfolio/domain/provenance.py) owns source descriptors. Keep original files and ignored evidence in the user's independent backup.
