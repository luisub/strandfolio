# strandfolio project structure

## Current checkout

The GitHub root has three directories. [strandfolio/](../strandfolio/) contains the Python application and its packaged examples and icons. [databases/](../databases/README.md) holds the reference collections and their provenance. [docs/](./) contains the guides and [executable notebooks](notebooks/README.md). The [README](../README.md), [license](../LICENSE) and [ignore rules](../.gitignore) have repository-wide roles.

### Layout changes and their consumers

The [Python build configuration](../pyproject.toml) specifies the desktop entry point, runtime files and independent source distribution. The wheel places the released catalogues and the start-screen GFP example under `strandfolio/data/databases/`. The checkout keeps these inputs under `databases/`. The source archive also carries the build configuration, desktop requirements and a [build hook](installation/sdist_hook.py) that excludes repository ignore files. Scientific attribution travels with both archives. [Installation and publishing](installation/README.md) · [Desktop dependencies](installation/runtime-requirements.txt) · [Data notices](installation/DATA-NOTICES.md)

| Current location | Previous location | What uses it |
| --- | --- | --- |
| [Installation guide](installation/README.md), [requirements](installation/requirements.txt) and [Conda recipe](installation/environment.yml) | Root `environment.yml` with the pip dependencies embedded in its YAML | The README, launcher diagnostics and notebook setup point here. Pip reads the requirements file directly and Conda uses the same list. |
| [Enzyme catalogue builder](../databases/enzymes/build_catalogue.py) | `dev/build_restriction_database.py` | The data guide, tests and notebook source references use the new module. Anyone with a clone can build the optional database. |
| [SnapGene example](../strandfolio/data/examples/synthetic_circular.dna) and [format notes](../strandfolio/data/examples/README.md) | `tests/fixtures/standard_software/` | The import guide, notebooks, launcher checks and local tests share the same example bytes. |
| [Notebook register](notebooks/README.md) and five notebooks | Former top-level notebook directory | The installation guide and verifier run the notebooks in their new location. Local run evidence lives alongside them in `docs/notebooks/_outputs/`. |

### Local maintainer files

The ignored `docs/audits/` folder holds local code and planning audit reports, with its own `README.md` guide. The ignored `docs/promotional/` folder holds promotional media. Its `strandfolio-gui-tour/index.html` opens the local gallery of GitHub, LinkedIn, widescreen and SquareOne assets.

The ignore rules cover `planning/`, `dev/`, `tests/`, `.agent/` (including its workflows), `.agents/`, `AGENTS.md`, `__trash/` and `_trash/`. The existing planning files, maintenance tools, tests and recovery material stay in the maintainer checkout. The Git index excludes these paths from future commits. A fresh clone contains the public guides, examples and notebooks. Back up local maintainer files separately. The ignored `software_distribution/` directory holds local desktop build recipes, locked inputs, release candidates and their qualification records. Its platform guide describes the separate backup and rebuild procedure. If you maintain another clone, save its local-only material before updating to the reorganized revision. Files committed before this change remain in earlier Git history.

### Data and documentation

| Existing path | Responsibility and current contents |
| --- | --- |
| [databases/](../databases/README.md) | Active reference data, scientific originals and archival provenance |
| [databases/enzymes/](../databases/enzymes/README.md) | Public catalogue builder and guide. The maintainer checkout also keeps the Biopython 1.88 / REBASE 404 database at `restriction_enzymes.sqlite3` and a local `provenance.zip` with source, validation and attribution. |
| [databases/features/](../databases/features/README.md) | Distributable features.sqlite3, its reviewed references.json input and the source and query guide |
| [databases/ladders/](../databases/ladders/README.md) | Offline ladder SQLite release, manifest, source terms and vendor comparison guide. |
| `databases/features/raw/` | Local study inputs in `uva-cbln2-v1/` and `goldenpics-2017/`. The feature guide lists the [Dataverse files](../databases/features/README.md#human-cbln2-source) and the [GoldenPiCS workbook](../databases/features/README.md#goldenpics-workbook-review). |
| [databases/plasmids/](../databases/plasmids/README.md) | Annotated plasmid references, with the four example GenBank files in `maps/`, the deposited [pUC19 original](../databases/plasmids/sources/M77789.2.gb) in `sources/`, their SB2CL notice alongside the maps, local comparison records in `local/` and one source manifest |
| `databases/sequences/` | Ignored in full. Unpublished research files stay local. |
| [databases/references/](../databases/README.md#linear-reference-examples) | Offline GenBank records for the complete λ reference, its gallery edition and the coordinate-only HXB2 example. |
| [docs/images/](images/README.md) | The [15-second desktop tour](images/strandfolio-tour.gif) is embedded in the root README. The [gallery generator](images/render_gallery.py) captures the [Sequence workspace](images/sequence-workspace.png), [digest](images/digest.png), [PCR](images/pcr.png), [PCR product](images/pcr-product.png), [pairwise](images/alignment.png) and [shared-column alignment](images/shared-alignment.png), [Assembly](images/assembly.png), [Gel](images/gel.png) and [History](images/history.png). Notebook 02 supplies the [pUC19 map](images/pUC19.svg) and the [larger GFP plasmid map](images/pBH4-His-SUMO-GFP-LPETGG.svg). The [reference collection](../databases/README.md#linear-reference-examples) supplies the complete λ record and a coordinate-only [HIV-1 HXB2 map](images/hxb2-gene-map.svg). The [λ gallery edition](../databases/references/lambda-landmarks.gb) illustrates the [expanded linear map](images/lambda-linear.png) and [split sequence view](images/lambda-split.png). |
| [docs/](./) | This map, the [import guide](importing_sequences.md), [workspace guide](sequence_workspace.md) and [scientific validation register](validation.md) |
| [Apple Silicon installer](releases/Strandfolio-0.1.7-macOS-arm64.dmg) | Current Mac disk image linked from the main README. Build inputs and qualification evidence live in the local distribution directory. |
| [Mac installation screenshot](installation/mac_install_image.tiff) and [README image](images/mac-install.png) | The README uses the PNG to show where to approve Strandfolio on first launch. |
| [docs/notebooks/](notebooks/README.md) | Five executable investigations cover [intake](notebooks/01_import_export.ipynb), [sequence maps](notebooks/02_sequence_visualization.ipynb), the [enzyme catalogue](notebooks/03_enzyme_catalogue.ipynb), [ladders and gels](notebooks/08_ladders_and_gels.ipynb), and the [interactive workspace](notebooks/09_interactive_workspace.ipynb). |
| `docs/notebooks/_outputs/` | Generated exchange files, figures and projects from the [notebooks](notebooks/README.md), plus local verification evidence |

## Documentation ownership

Use these guides for installation, implemented interfaces and data provenance.

| Existing path | Owns |
| --- | --- |
| [README.md](../README.md) | User introduction and quick launch |
| [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md) | This inventory, startup graph and new-file destinations |
| [installation/README.md](installation/README.md) | Python and Conda installation, desktop launch and notebook kernel setup |
| [importing_sequences.md](importing_sequences.md) | Current intake API and format/fidelity limits |
| [sequence_workspace.md](sequence_workspace.md) | Current controls, copy/conversion behavior and native acceptance steps |
| [docs/validation.md](validation.md) | Cross-software evidence, exact inputs and outputs, comparison thresholds, reproducible commands and open claims for restriction digestion, PCR, alignment and Assembly |
| [docs/notebooks/README.md](notebooks/README.md) | Python library examples, their inputs and exports, and instructions for running them |
| [databases/README.md](../databases/README.md) | Scientific data index, catalogue and collection guides, intake requirements, updates and backup |
| [Enzyme](../databases/enzymes/README.md), [feature](../databases/features/README.md) and [ladder](../databases/ladders/README.md) guides | Implemented releases, query and rebuild entry points, provenance and source terms |
| [Plasmid guide](../databases/plasmids/README.md) | Public examples, source terms, annotation coverage and checksum verification |

The [planned collections](../databases/README.md#planned-collections) section covers future supporting material, primer and project collections. Unpublished sequence files already have a local home under `databases/sequences/`.

The resource packages [data](../strandfolio/data/__init__.py) and [icons](../strandfolio/data/icons/__init__.py) expose bundled assets. The Qt workspace uses the [panels](../strandfolio/gui/panels/__init__.py), [views](../strandfolio/gui/views/__init__.py) and [rendering](../strandfolio/rendering/__init__.py) packages.

## Existing Python entry points

`python -m strandfolio` enters `__main__.py`, delegates to `gui/main.py`, then `gui/app.py:main()`, which creates `gui/window.py:MainWindow`. `python -m strandfolio.gui.main` reaches the same launcher. From a wheel installation, the `strandfolio` command launches the installed application from any directory. The launcher accepts a sequence or saved digestion file. Use `--example` to open the bundled design reporter. `--molecule-type` chooses the alphabet for headerless text or overrides FASTA inference. On macOS the launcher checks that the process can reach a Quartz session before starting Cocoa. Missing PySide6 produces setup guidance and exit status 1.

| Existing path | Current responsibility |
| --- | --- |
| [strandfolio/__init__.py](../strandfolio/__init__.py) | Package description; no window initialization |
| [strandfolio/__main__.py](../strandfolio/__main__.py) | Module entry point |
| [strandfolio/gui/main.py](../strandfolio/gui/main.py) | Delegates lazily to application startup |
| [strandfolio/gui/app.py](../strandfolio/gui/app.py) | CLI arguments, package-relative icon resolution (`getIconDirectory`, `getAppIconPath`, `getHeaderIconPath`, `buildAppIcon`, `buildHeaderIconPixmap`), QApplication setup and event loop |
| [strandfolio/gui/window.py](../strandfolio/gui/window.py) | `MainWindow` composes the record header, six workflow tabs, contextual tools, Sequence canvas and right inspector. The empty workspace can open the illustrated reporter or the distributed GFP plasmid through normal intake. It connects file and project actions and offers map and sequence-detail scopes for figure export. |
| [strandfolio/gui/__init__.py](../strandfolio/gui/__init__.py) | GUI package marker |
| [strandfolio/data/examples/pReporter_demo.gb](../strandfolio/data/examples/pReporter_demo.gb) | Bundled artificial Sequence example. The design verifier checks its letters, topology, name and ordered annotations against the approved prototype fixture. |
| [PCR example template](../strandfolio/data/examples/Insert_demo.gb) | Linear, 1,800 bp teaching template for the three fixed example pairs and their 912 bp product. |
| [Illustrated primer temperatures](../strandfolio/data/examples/insert_demo_tm.json) | Six fixed nearest-neighbor Tm values with primer identities, model and chemistry for offline PCR validation |
| [strandfolio/data/icons/](../strandfolio/data/icons/) | [Checkbox tick](../strandfolio/data/icons/control-check.svg), [down](../strandfolio/data/icons/control-chevron.svg) and [up](../strandfolio/data/icons/control-chevron-up.svg) control arrows, score [increment](../strandfolio/data/icons/control-plus.svg) and [decrement](../strandfolio/data/icons/control-minus.svg) symbols with [upper](../strandfolio/data/icons/control-plus-muted.svg) and [lower](../strandfolio/data/icons/control-minus-muted.svg) limit states, and bundled plasmid application and header icons (`icon_strandfolio_app.{svg,icns,ico}`, `icon_strandfolio_gui.{svg,png}`, and per-scale application PNGs under `scales/`). The [1024 px PNG](../strandfolio/data/icons/scales/icon_strandfolio_app_1024x1024.png) is the raster fallback. |
| [strandfolio/domain/__init__.py](../strandfolio/domain/__init__.py) | Lightweight domain package marker |
| [strandfolio/domain/sequence.py](../strandfolio/domain/sequence.py) | `SequenceRevision`: isolated supported annotated payload, immutable projections, typed references, identity-preserving hydration and naming, and geometry-aware fingerprints for duplex products |
| [strandfolio/domain/primers.py](../strandfolio/domain/primers.py) | Immutable primer revisions, named pair identities, declared tails and oriented exact binding footprints |
| [Primer structure analysis](../strandfolio/operations/primer_structures.py) and [native structure report](../strandfolio/gui/views/primer_structure_review.py) | Live perfect-complement annealing Tm estimates and five Primer3 model checks on complete input oligos, their settings, binding-site remarks in PCR results, and explicit unassessed states |
| [strandfolio/domain/fragment_annotations.py](../strandfolio/domain/fragment_annotations.py) | Map retained annotation intervals into digest candidates and identify clipped features |
| [strandfolio/domain/fragments.py](../strandfolio/domain/fragments.py) | Ordered digest products and their physical strand spans, overhangs and terminal chemistry on aligned reference sequences |
| [strandfolio/domain/features.py](../strandfolio/domain/features.py) | `SequenceFeature`, `FeaturePart`: immutable display projections, preserved part/qualifier semantics and canonical annotation/location values |
| [strandfolio/domain/provenance.py](../strandfolio/domain/provenance.py) | `SourceArtifact` byte descriptor, stable operation/output references and file-supplied illustration credits |
| [strandfolio/io/__init__.py](../strandfolio/io/__init__.py) | Lightweight format package marker |
| [strandfolio/io/formats.py](../strandfolio/io/formats.py) | Bounded GenBank, FASTA, text and SnapGene intake with source-byte evidence. Validated and atomically published GenBank, FASTA and text exports through `writeSequence` |
| [Primer-pair FASTA](../strandfolio/io/primer_pairs.py) and [load/save dialog](../strandfolio/gui/primer_files.py) | Two-record exchange, explicit forward/reverse assignment, tail-length review and atomic file publication. |
| [strandfolio/io/digestion.py](../strandfolio/io/digestion.py) | Self-contained, versioned recorded digestions, offline validation and atomic file replacement |
| [strandfolio/domain/coordinates.py](../strandfolio/domain/coordinates.py) | `SequenceSelection`: revision-bound ordered interbase segments, circular wrap-around and whole-feature linkage |
| [strandfolio/application/session.py](../strandfolio/application/session.py) | `DocumentSession`: immutable current revision, revision-bound selection and copy, annotation undo/redo, named snapshot adoption and per-document view values |
| [strandfolio/application/workspace.py](../strandfolio/application/workspace.py) | Ordered open sessions, root draft or saved state, exact-revision activation, batch append, close targets and record-scoped display names |
| [strandfolio/application/tasks.py](../strandfolio/application/tasks.py) | Qt-free task identities and relevance checks for owner, input revisions, activation, settings and versions |
| [strandfolio/operations/__init__.py](../strandfolio/operations/__init__.py) | Lightweight operations package marker |
| [strandfolio/operations/editing.py](../strandfolio/operations/editing.py) | Immutable annotation changes, exact suggestion classification, adoption evidence and dismissal decisions |
| [strandfolio/operations/pcr.py](../strandfolio/operations/pcr.py) | Bounded exact single-template search, keyed mature amplicons, ordered template and primer contributions, and projected annotations |
| [strandfolio/operations/alignment.py](../strandfolio/operations/alignment.py) | Pairwise DNA, RNA and protein comparison, molecule-specific scoring, source-coordinate mapping, differences and nucleotide translation tracks |
| [Alignment worker](../strandfolio/operations/alignment_worker.py) | Pipe-based console entry point for the desktop's pairwise and shared-column engines. The installed app resolves its bundled helper beside its GUI executable. |
| [strandfolio/operations/multiple_alignment.py](../strandfolio/operations/multiple_alignment.py) | Bounded Kalign execution for DNA, RNA or protein comparisons, source coordinates and aligned FASTA |
| [strandfolio/operations/restriction.py](../strandfolio/operations/restriction.py) | Qt-free motif recognition on both strands, occurrence filters, attributed cut pairs and ideal complete digest runs |
| [strandfolio/operations/gel.py](../strandfolio/operations/gel.py) | Pinned samples, marker and selection settings, exact contributors and the illustrative size scale |
| [strandfolio/operations/conversion.py](../strandfolio/operations/conversion.py) | Ordered nucleotide and protein-copy previews, offline genetic-code resolution, and source codon mapping for annotated CDSs and whole-record frames |
| [strandfolio/rendering/layout.py](../strandfolio/rendering/layout.py) | `SequenceLayout`, `OverviewLayout`, `SequenceRowIndex`, `LinearFeatureSpan` / `buildLinearFeatureSpans`, `LayerOptions` (primers hidden by default), visible-row frame codons, `featureColor`: layered sequence/track geometry independent of Qt |
| [strandfolio/rendering/circular.py](../strandfolio/rendering/circular.py) | `CircularMapLayout`, `buildCircularMapLayout`, `annularArrowPolygon`: concentric plasmid arcs, radial lanes, repeat-array grouping (`×N`), priority cap (`maxLabels=22`), zoom (`1.0×`–`8.0×`) expansion, strand-aware multipart connector geometry and hit-testing |
| [strandfolio/rendering/maps.py](../strandfolio/rendering/maps.py) | Shared vector map scenes for Sequence, the embedded Digest source map and notebook exports. Includes scale, annotation layout, selection overlays, fitted titles and native hit regions. |
| [strandfolio/rendering/gels.py](../strandfolio/rendering/gels.py) | Label and band geometry and attributed gel exports |
| [strandfolio/storage/__init__.py](../strandfolio/storage/__init__.py) | Lightweight storage package marker; importing does not open a database |
| [strandfolio/storage/projects.py](../strandfolio/storage/projects.py) | Qt-free `ProjectRepository` entry point, SQLite project schema, intake registration and lookup, named and exact revisions, preferred names and record-scoped genetic-code settings, managed capture and transactional publication, including restriction and PCR runs |
| [strandfolio/storage/revision_codec.py](../strandfolio/storage/revision_codec.py) | Versioned native snapshot encoding and decoding, shared strict JSON serialization, and snapshot comparisons for exact revisions, renames and independent product copies |
| [strandfolio/storage/project_exports.py](../strandfolio/storage/project_exports.py) | Project-aware export intent, delivery verification, provenance and interrupted-registration recovery using the shared format writer |
| [strandfolio/storage/project_primer_pairs.py](../strandfolio/storage/project_primer_pairs.py) | Schema 9 storage for named pairs, concurrency checks and retention of primer revisions used by saved pairs and runs |
| [strandfolio/storage/project_pcr.py](../strandfolio/storage/project_pcr.py) and [pcr_codec.py](../strandfolio/storage/pcr_codec.py) | PCR inputs, runs, keyed output revisions, preview settings, transactional registration and offline hydration |
| [strandfolio/storage/project_runs.py](../strandfolio/storage/project_runs.py) | Transactional run registration, keyed product lookup, naming and source resolution |
| [strandfolio/storage/restriction_codec.py](../strandfolio/storage/restriction_codec.py) | Native restriction-run encoding, pinned definition and geometry validation, and offline replay against exact revisions |
| [strandfolio/storage/project_backup.py](../strandfolio/storage/project_backup.py) | Whole-project closure checks and verified SQLite snapshot backups with their referenced artifacts |
| [strandfolio/storage/build_restriction_catalogue.py](../strandfolio/storage/build_restriction_catalogue.py) | Builds and validates the SQLite enzyme catalogue from the installed Biopython data. The desktop cache and checkout snapshot tool use the same builder. |
| [strandfolio/storage/catalogues.py](../strandfolio/storage/catalogues.py) | `RestrictionCatalogue`, `EnzymeRecord`: explicit read-only SQLite catalogue queries with immutable definitions and a checksum identifying the read transaction’s snapshot |
| [strandfolio/storage/feature_catalogue.py](../strandfolio/storage/feature_catalogue.py) | Read-only source records, whole-reference identification and exact regional scans on linear or circular DNA |
| [strandfolio/gui/theme.py](../strandfolio/gui/theme.py) | Light Qt controls, focus states, document tabs and workflow inspectors |
| [strandfolio/rendering/colors.py](../strandfolio/rendering/colors.py) | Shared presentation colors for sequence tracks, map geometry and contrast-aware labels |
| [strandfolio/gui/controller.py](../strandfolio/gui/controller.py) | `WorkspaceController`: workspace activation, project and import actions, revision-bound selection, derived display state, Qt notifications and clipboard copy |
| [strandfolio/gui/pcr_controller.py](../strandfolio/gui/pcr_controller.py) and [pcr_task.py](../strandfolio/gui/pcr_task.py) | Revision-bound PCR drafts, pinned targets, previous results, History reads and keyed product actions. The workspace shares the project task queue for asynchronous reads and ordered analysis writes. |
| [strandfolio/gui/digest_controller.py](../strandfolio/gui/digest_controller.py) | Coordinates asynchronous scans, portable intake and reaction requests, checks the requesting revision, and connects retained runs to the GUI |
| [strandfolio/application/pcr_session.py](../strandfolio/application/pcr_session.py) | User-assigned primer locations, retained PCR runs, exact source lookup and the selected Assembly preparation payload |
| [strandfolio/application/alignment_session.py](../strandfolio/application/alignment_session.py) | Workspace choices and independent live pairs, retained input revisions and close-review effects |
| [strandfolio/application/digest_session.py](../strandfolio/application/digest_session.py) | Retain compatible run, source snapshot and ancestry identities for the current workspace |
| [strandfolio/storage/project_digestions.py](../strandfolio/storage/project_digestions.py) | Portable source evidence, stored gel settings, ancestry recovery and atomic import of recorded runs |
| [strandfolio/gui/import_task.py](../strandfolio/gui/import_task.py) | `SequenceImportTask`: bounded background reader using `io/formats.py`, with request-tagged results delivered to the GUI thread |
| [strandfolio/gui/views/overview.py](../strandfolio/gui/views/overview.py) | `SequenceOverview`: compact shared multi-segment selection, visible-region indicator and navigation |
| [strandfolio/gui/sequence_export.py](../strandfolio/gui/sequence_export.py) | File-menu recovery of the complete captured original, and saved-sequence exchange with format-specific fidelity review |
| [strandfolio/gui/figure_dialog.py](../strandfolio/gui/figure_dialog.py) | Preview bounds, scope and format selection, guarded PNG rendering and atomic figure publication for maps, sequence tracks and gels |
| [strandfolio/gui/sequence_figure.py](../strandfolio/gui/sequence_figure.py) | A captured sequence-detail scope, source intervals, output bounds and SVG serialization of the native tracks |
| [strandfolio/gui/views/sequence_tracks.py](../strandfolio/gui/views/sequence_tracks.py) | Shared row painting for sequence viewports and exported figures, including directional features, codons, recognition spans and cut boundaries |
| [Map feature types](../strandfolio/application/map_features.py) and [Layers menu](../strandfolio/gui/panels/map_features.py) | Classify annotation types for whole-molecule views, expose per-document presets and choose custom types |
| [strandfolio/gui/views/plasmid_map.py](../strandfolio/gui/views/plasmid_map.py) | `PlasmidMapView`: synchronized circular and linear maps with fitted editable display titles, directional annotations, callouts, per-document zoom/pan/rotation and map selection |
| [strandfolio/gui/views/sequence_view.py](../strandfolio/gui/views/sequence_view.py) | `SequenceView`: visible layered-row painting (feature tracks, complement, CDS codons), variable row index, pixel scrolling through tall annotation stacks, and mouse/keyboard selection |
| [strandfolio/gui/panels/document_strip.py](../strandfolio/gui/panels/document_strip.py) | Scrollable record tabs, activation and per-tab close, fitted labels, full-name reveal and editor gestures |
| [strandfolio/gui/panels/dialog_layout.py](../strandfolio/gui/panels/dialog_layout.py) | Shared native dialog headings, footer actions and cancel-safe confirmations |
| [strandfolio/gui/panels/saved_primer_pairs.py](../strandfolio/gui/panels/saved_primer_pairs.py) | Named pair editor, session and project lists, and application of a saved or recorded pair to the current draft |
| [strandfolio/gui/panels/pending_saves.py](../strandfolio/gui/panels/pending_saves.py) | Source, revision and run choices for retrying failed saves in Digest, PCR, Gel and History |
| [strandfolio/gui/panels/document_dialogs.py](../strandfolio/gui/panels/document_dialogs.py) | Ordered batch review, explicit clipboard capture and normalization, preferred-name editor, exact revision picker and Qt project dialogs |
| [strandfolio/gui/annotation_controller.py](../strandfolio/gui/annotation_controller.py) and [calculation_task.py](../strandfolio/gui/calculation_task.py) | Revision-bound detection, cancellation and annotation commands. Digest, PCR and other background calculations share the worker. |
| [strandfolio/gui/panels/annotation_editor.py](../strandfolio/gui/panels/annotation_editor.py) and [label_review.py](../strandfolio/gui/panels/label_review.py) | Feature metadata and ordered-location editor, exact-match review, and explicit decisions |
| [strandfolio/storage/project_parents.py](../strandfolio/storage/project_parents.py) | Ordered, user-reported relationships between saved revisions, transactional edits and project integrity checks |
| [strandfolio/storage/project_edits.py](../strandfolio/storage/project_edits.py) | Persist and resolve applied annotation snapshots and their predecessor revisions |
| [strandfolio/gui/panels/features.py](../strandfolio/gui/panels/features.py) | `FeaturePanel`: per-document feature filters, accessible rows, selection and annotation actions |
| [strandfolio/gui/panels/choice_rows.py](../strandfolio/gui/panels/choice_rows.py) | Paged native buttons for selectable fragments, samples, enzymes and history rows, with stable identity and keyboard access |
| [strandfolio/gui/panels/fitted_label.py](../strandfolio/gui/panels/fitted_label.py) | Fits long record and file names to source cards and review dialogs, retaining both ends and the full accessible name |
| [strandfolio/gui/workspace_icons.py](../strandfolio/gui/workspace_icons.py) | Outline icons for workflow tabs, document types and toolbar actions at desktop and Retina resolutions |
| [strandfolio/gui/panels/digest.py](../strandfolio/gui/panels/digest.py) | Per-record enzyme recipe and display controls, product inspection and actions for recorded results |
| [strandfolio/gui/views/pcr_sequence.py](../strandfolio/gui/views/pcr_sequence.py) | PCR sequence canvas and overview with picked-site bands and locators, exact-site arrows, target selection and recorded product overlays |
| [Primer site comparison](../strandfolio/gui/views/primer_site_comparison.py) | Primer and template strand display at the user-selected site, including unpaired 5′ ends and visible substitutions |
| [strandfolio/gui/views/pcr.py](../strandfolio/gui/views/pcr.py) and [pcr_result.py](../strandfolio/gui/views/pcr_result.py) | Live primer selection length, adjacent binding and 5′ end fields, recorded product coverage and gel preview |
| [strandfolio/gui/views/alignment.py](../strandfolio/gui/views/alignment.py) and [alignment_tracks.py](../strandfolio/gui/views/alignment_tracks.py) | Reference and query selectors, pair-specific results, residue and nucleotide tracks, translations, and difference navigation |
| [Shared-column result window](../strandfolio/gui/views/multiple_alignment.py) and [matrix view](../strandfolio/gui/views/alignment_matrix.py) | Calculation lifecycle and aligned FASTA export, virtualized symbols, source labels, column overview and source-coordinate inspection |
| [strandfolio/gui/alignment_controller.py](../strandfolio/gui/alignment_controller.py) | Queued pair requests, cancellation and exact input relevance, plus access to retained session comparisons |
| [strandfolio/gui/views/gel.py](../strandfolio/gui/views/gel.py) and [gel_preview.py](../strandfolio/gui/views/gel_preview.py) | Comparison lanes, marker selection and the shared interactive gel drawing |
| [strandfolio/gui/views/digest_source_map.py](../strandfolio/gui/views/digest_source_map.py) | `DigestSourceMap` renders the recorded source and selected fragment intervals in the Digest popup |
| [strandfolio/application/assembly_session.py](../strandfolio/application/assembly_session.py), [strandfolio/gui/views/assembly.py](../strandfolio/gui/views/assembly.py) | Ordered source occurrences, verification of recorded output identity, exact source navigation and selection from saved revisions, files and pasted text |
| [Planned junction inspector](../strandfolio/gui/views/assembly_junctions.py) | Circular neighbor selection, plan orientation, recorded input ends and strand spans, and pinned revision identities |
| [Assembly example view](../strandfolio/gui/views/assembly_example.py) and [example actions](../strandfolio/gui/assembly_example.py) | Annotated product preview, named copies of the packaged record, and exact navigation to Sequence and file-supplied History |
| [strandfolio/gui/views/reported_parents.py](../strandfolio/gui/views/reported_parents.py), [reported_parent_map.py](../strandfolio/gui/views/reported_parent_map.py) | Review and edit reported parent links, map supplied intervals onto the selected child and reopen exact parent snapshots |
| [strandfolio/io/history_report.py](../strandfolio/io/history_report.py) | Export a versioned JSON report with recorded ancestry, evidence gaps and separately attributed parent reports |
| [strandfolio/rendering/history.py](../strandfolio/rendering/history.py) | Read-only projection of immutable ancestors, ordered source intervals and evidence gaps |
| [strandfolio/gui/views/history_diagram.py](../strandfolio/gui/views/history_diagram.py) | Selectable visual ancestry from resolved revision identities and their recorded input links |
| [strandfolio/gui/views/history.py](../strandfolio/gui/views/history.py) and [history_ancestry.py](../strandfolio/gui/views/history_ancestry.py) | Recorded analyses, file-supplied source diagrams and asynchronous ancestry inspection with navigation to exact revisions and keyed outputs |
| [strandfolio/gui/views/restriction_sites.py](../strandfolio/gui/views/restriction_sites.py) | Shared recognition-site markers, picking and keyboard interaction for sequence, overview and map |
| [Sequence text-size control](../strandfolio/gui/panels/sequence_text_size.py) | Shared editable font size with decrease and increase buttons for the Sequence and PCR toolbars |
| [strandfolio/gui/panels/sequence_details.py](../strandfolio/gui/panels/sequence_details.py) | Import Details with source identity and reader notices, collapsible lower selection strip, quick-copy actions, shared protein interpretation and expandable 3-tab drawer (`Selection / CDS`, `Feature Qualifiers`, `Source / Limits`) |
| [strandfolio/gui/panels/sequence_conversion.py](../strandfolio/gui/panels/sequence_conversion.py) | Bottom-of-Sequence genetic-code and frame controls, effective-code labels, selectable Copy preview and clipboard feedback |
| `strandfolio/application/__init__.py`, `data/__init__.py`, `data/icons/__init__.py`, `rendering/__init__.py`, `gui/views/__init__.py`, `gui/panels/__init__.py` | Package markers only; paths after the first are relative to `strandfolio/` |

Use the existing launcher. Keep `window.py` focused on composition and `app.py` on startup. Empty `main_functions.py`, `utils/` and resource-package scaffolding have been retired; add a focused operation or real resource only when implemented. Do not add a parallel `src/` package or a runtime dependency on MicroLive.

## Planned module destinations

**These paths do not exist yet.** Create only the module needed by an authorized feature, and move its entry into the existing inventory when created. Existing `coordinates.py`, `session.py`, `maps.py`, `storage/projects.py` and `storage/catalogues.py` above implement bounded subsets; extend those owners for later supported behavior rather than creating parallel versions. One module owns each implementation; functions are preferred for calculations, with classes for meaningful records and state. The local architecture plan owns detailed biological contracts when available.

| Planned path | Responsibility |
| --- | --- |
| `strandfolio/operations/assembly.py` | Joining plans, junction validation and construct products |
| `strandfolio/gui/views/<view>.py`, `gui/panels/<task>.py` | Focused widgets and controls; choose the exact descriptive filename before creating it |

The catalogue builder captures a named installed source and creates a new snapshot.
`RestrictionCatalogue` opens an explicitly supplied SQLite path read-only. Notebook 03
uses it to query the local catalogue or one it builds from the installed Biopython dictionary.
Notebook 08 uses the released DNA ladder profiles to render an illustrative gel. The native Digest and Gel views retain the selected profile with the recorded result.

The inspector’s views call `WorkspaceController`, which binds them to the active Python `DocumentSession` in the open workspace. `SequenceImportTask` uses the shared reader in the background. The controller checks request relevance before applying an outcome, and accepted batches append in one workspace change. Notebook SVG and Qt overview share `OverviewLayout`. Qt and notebooks use the same operations and domain values. IO and storage own parsing and persistence, and rendering consumes the shared values.

## Other file destinations

Angle-bracket names below are placement templates, not folders to generate in advance. Choose the concrete path in the affected feature brief before writing. For a small fix, a short change note is enough.

| New material | Destination and rule |
| --- | --- |
| Behavioral checks | Reuse the relevant `tests/test_<module_or_feature>.py`; create one if no suitable owner exists. Small licensed/synthetic inputs go in `tests/fixtures/<feature>/` |
| User/API guidance | `docs/<topic>.md`; describe implemented behavior and link it from the relevant entry point |
| Feature requirements | Record named acceptance cases and expected results in the owning feature brief under `planning/features/`. |
| New investigation cases | Extend an existing investigation when it fits. Add a notebook when a distinct question has executable cases. Register it in [the notebook index](notebooks/README.md). |
| Notebook figures/results | `docs/notebooks/_outputs/<case>/<run_id>/`; retain versions and meaningful evidence, not notebook copies |
| Design images | Supplied references go in `planning/references/Fxx_<topic>/` with interpretation in the feature brief. Maintain the approved prototype and specification in `planning/gui_design/` and its illustrations in `plates/`. |
| Reusable development tool | `dev/<descriptive_tool>.py`, only when repeat use is justified |
| Temporary probe/preview | `dev/_scratch/<task>/`; this ignored path is reserved, not currently a maintained directory. Promote or retire task-created files before completion |
| Active enzyme catalogue | `databases/enzymes/restriction_enzymes.sqlite3` |
| Automatic annotation references | Keep the released SQLite file, source manifest and guide in [databases/features/](../databases/features/README.md) |
| Enzyme sources and retained versions | `databases/enzymes/provenance.zip` with members under `raw/<source>/<release>/` and `snapshots/<snapshot_id>/` |
| Annotated plasmid maps | Example maps and notices in `databases/plasmids/maps/`, the original pUC19 deposit in `databases/plasmids/sources/`, local comparisons in `databases/plasmids/local/` and source details in `databases/plasmids/references.json` |
| Ladder data | [databases/ladders/](../databases/ladders/README.md) holds the active catalogue and its provenance |
| Unpublished sequences and their derivatives | Keep under the ignored `databases/sequences/` directory. |
| Primers/projects | Future `databases/primers/records/` and `databases/projects/records/<project_id>/` |
| Scientific source material | Future `databases/materials/originals/<artifact_id>/`. GUI design references live under `planning/` |
| Bundled icons/resources | Implemented in [strandfolio/data/icons/](../strandfolio/data/icons/) (matching MicroLive's `data/icons/` layout); resolve package-relatively via `strandfolio.gui.app` |
| Superseded code/docs | `__trash/YYYY-MM-DD_<purpose>/<original_relative_path>` plus the single archive index and verified hashes |

The [scientific data index](../databases/README.md) owns the holdings register and preservation guidance. Known local research files are not disposable tests. No personal sequence names/content belong in this map. Source originals and unique scientific evidence never become cleanup trash.

## Keep this map current

For every structural change, in the same task:

1. Inspect the checkout; select the existing owner or justify a concrete new path.
2. Update this map for additions, moves, renames, retirements, changed responsibilities or startup connections. Move implemented paths out of the proposed table; do not leave them listed as absent.
3. Fix affected imports, tests, notebook/data indexes and documentation links. Update `.gitignore` only when a new category of private/generated output needs it.
4. Confirm documented existing paths resolve and proposed paths are still labeled accurately. Record structural changes and validation in the existing local progress board when present; report any unavailable checks.

Keep the root README and AGENTS as short links to this map. Do not generate a second tree, copy the inventory into each plan, or create a separate structure report per session. Directory placeholders, a written API proposal and executed functionality are different states; document them honestly.
