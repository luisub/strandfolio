# Scientific data index

Use this index to find the annotated plasmids, reference catalogues and their build inputs.

| Collection | Current holdings | Availability and entry point |
| --- | --- | --- |
| [Restriction enzymes](enzymes/README.md) | 1,088 enzymes from Biopython 1.88 / REBASE EMBOSS 404 | Local SQLite and provenance ZIP. The [enzyme guide](enzymes/README.md#rebuild-and-verify) covers lookup from a fresh checkout and restoration of the pinned build. |
| [DNA features](features/README.md) | Six regional labels and two complete circular plasmid identities | Released [SQLite database](features/features.sqlite3) and [reviewed manifest](features/references.json). The [source inventory](features/README.md#sources-and-distribution) identifies the two locally retained deposits. |
| [Electrophoresis ladders](ladders/README.md) | 12 DNA profiles and one six-protein size reference, release `2026-11-17.1` | Released [SQLite database](ladders/ladders.sqlite3) and [reviewed manifest](ladders/references.json). The [comparison tables](ladders/README.md#dna-marker-comparisons) identify the quantity verified for each product. |
| [Annotated plasmids](plasmids/README.md) | Seven complete circular reference maps spanning bacterial cloning, bacterial expression and mammalian fluorescence | Four distributed GenBank maps and three optional local comparisons. The [manifest](plasmids/references.json) indexes both. |

Each catalogue guide documents its files, query and build procedures, source terms and verification. The [project map](../docs/PROJECT_STRUCTURE.md) covers placement elsewhere in the repository.

## Data for a local source checkout

Set up the Python environment from the checkout root, then use the following data for each workflow. [Installation guide](../docs/installation/README.md)

| Workflow | Data to keep with the checkout | Scope |
| --- | --- | --- |
| Sequence → Digest | The desktop reads `databases/enzymes/restriction_enzymes.sqlite3`. Restore it from backup or rebuild it with Biopython 1.88. The companion `provenance.zip` preserves the pinned release’s sources and build records. | The app uses the REBASE 404 dictionary shipped with Biopython 1.88. The independent comparison used REBASE 611. Adopting a new catalogue release still requires integration, coverage checks and redistribution review. [Restore or rebuild](enzymes/README.md#rebuild-and-verify) · [Comparison scope](../docs/validation.md#restriction-digestion-and-physical-ends) |
| Sequence → Detect labels | Sequence reads `databases/features/features.sqlite3`. The adjacent `references.json` lists the sequence fingerprints and their sources. | F09 U05 covers exact label review. The released database contains six regional references and two full-circle fingerprints. [Matching scope](features/README.md#query-the-database) |
| Digest, PCR and Gel DNA markers | The previews read `databases/ladders/ladders.sqlite3`. The adjacent `references.json` describes the inputs and comparisons for release `2026-11-17.1`. | F08 L01–L02 cover catalogue contents. L03–L06 cover the illustrative DNA gel and the supported native previews. Source FASTA is required for a rebuild. [Ladder scope](ladders/README.md#supported-size-previews) |
| Annotated GenBank examples | Use the four `databases/plasmids/maps/` files with their manifest, guide and SB2CL notice. | Release `2026-11-19.2` includes two Dryad maps, the SB2CL reporter and a labeled pUC19 map. The pUC19 source deposit M77789.2 ships alongside the examples. [Verification](plasmids/README.md#restore-and-verify) |

## Linear reference examples

The files below are available offline. Open a GenBank file in Sequence to inspect its map. [λ workspace screenshots](../docs/images/README.md#explore-the-linear-λ-genome) · [HXB2 coordinate map](../docs/images/README.md#explore-the-hxb2-coordinate-map)

| Record | Contents and provenance | File SHA-256 |
| --- | --- | --- |
| [Complete λ reference](references/J02459.1.gb) | Original GenBank J02459.1 file retrieved on 19 November 2026. It contains 48,502 bases and 188 annotations. The ladder manifest records the sequence checksum. [GenBank deposit](https://www.ncbi.nlm.nih.gov/nuccore/J02459.1) · [Ladder references](ladders/references.json) | `c9cde7c47e892d2ef424f34fe7791cf6643693df77cf847cbe53d57bdc3d057e` |
| [λ gallery edition](references/lambda-landmarks.gb) | Derived from the complete file. It retains the original bases and the 69 original source, gene and CDS annotations with their coordinates and qualifiers. | `bbe9a202418b46f58ed97c3b77acaf4951fc0889d8e918d91283c8aef75bee91` |
| [HIV-1 HXB2 coordinate map](references/HXB2-coordinate-map.gb) | Nine gene spans on the 9,719-position proviral reference axis. All bases are `N` placeholders. GenBank gene locations represent outer spans. [Los Alamos coordinate table](https://www.hiv.lanl.gov/components/sequence/HIV/search/help.html) | `574839f0df85f8fb7ca775e757a66a2eb132fbcff7894d821f20e1c8df8ad0e8` |

## Artifact manifests

At intake, preserve the original bytes and filename before parsing. Record the source or versioned accession, acquisition time when known, format, applicable terms, relative path, byte count and SHA-256. Keep distinct experimental origins even when two files have identical bytes. Assemblies and contigs need specific identifiers.

When registering an artifact, assign a stable ID. Record the parser and version, record count, validation evidence and unsupported fields. Compare records, annotations, topology and names with the source. Derivatives should identify the source revision, operation, settings and result hash. The standalone reader captures originals in memory. Project intake registers the exact capture, parser information and ordered revisions through the [Python repository](../docs/importing_sequences.md#save-and-reopen-a-project).

## Reference updates

Record the catalogue version and SQLite hash with each reproducible analysis. Build a candidate at a fresh path, validate it against its named inputs and compare it with the active version before replacement. Preserve source files, notices and evidence together with the version they describe. The enzyme, feature and ladder guides document their respective rebuild and verification commands.

## Planned collections

| Collection | Storage and identifying information |
| --- | --- |
| Supporting materials | Preserve the original filename, source, terms, date, hash and linked project or sequence in `materials/originals/<artifact_id>/`. GUI design references belong in local planning. |
| Primers | Saved PCR runs already retain their exact primer revisions and binding alignments against the template. Standalone primer exports under `primers/records/` and storage for thermal estimates remain planned. [PCR workflow](../docs/sequence_workspace.md#simulate-pcr-with-existing-primers) |
| Projects | Strandfolio already saves revisions, original source files and recorded operations in the project directory you choose. `projects/records/<project_id>/` is a proposed location for a future collection. [Save and reopen a project](../docs/importing_sequences.md#save-and-reopen-a-project) |

## Sharing and backup

The entire `databases/sequences/` directory is reserved for unpublished work and ignored by Git. Public examples and README images use the distributed maps and bundled fixtures. Keep local data, checksums and the enzyme rebuild inputs in an independent backup. Verify restored files against their recorded hashes. For a portable sequence project, use `ProjectRepository.backup` to snapshot the project database and every referenced managed file into an independent directory. Reopen the restored copy to verify its closure and hashes.

The [plasmid guide](plasmids/README.md#sources-and-distribution) identifies the four distributed maps, their sources and terms, and the three local NCBI comparisons.

Distribute the feature and ladder databases with their reviewed manifests and catalogue guides. Those guides carry the source-specific terms and attribution. Check notebook outputs and release packages for private research data before sharing.
