# Restriction enzyme catalogue

The local catalogue contains 1,088 restriction enzymes from the Biopython 1.88
dictionary derived from REBASE EMBOSS 404 (2024). Use it to look up recognition
sites, strand-cut offsets and supplier listings from that release.
[Scientific data index](../README.md)

## Files

| File | Purpose | Availability |
| --- | --- | --- |
| `restriction_enzymes.sqlite3` | Active read-only lookup database | Local, excluded from Git |
| `provenance.zip` | Versioned manifests, validation, source files and historical SQLite database | Local, excluded from Git |

In a source checkout, the native **Enzymes** chooser and new digests load `restriction_enzymes.sqlite3`
from this directory. The installed desktop wheel builds the same catalogue on first use in the user's cache, under `strandfolio/enzymes-v1-biopython-<version>/`. Notebook 03 opens the checkout database if available. In a fresh
checkout it builds a local example from the installed Biopython dictionary.
[Catalogue notebook](../../docs/notebooks/03_enzyme_catalogue.ipynb)

## Query the database

Run from the repository root in the project Python environment.

```python
from strandfolio.storage.catalogues import RestrictionCatalogue

with RestrictionCatalogue("databases/enzymes/restriction_enzymes.sqlite3") as catalogue:
    enzyme = catalogue.get("EcoRI")
    print(enzyme.recognitionSite, enzyme.cleavagePairs)
    print(catalogue.summary())
```

`get(name)` looks up an exact name case-insensitively. `search(text)` matches a literal
name or motif substring. It accepts `commercialOnly`, `cleavageKnown`, `includeFlagged`,
`limit` (1–1,000) and `offset`. The reader validates schema, integrity and cut-pair
consistency when opening a database. A missing file raises `FileNotFoundError`.
[Read-only Python API](../../strandfolio/storage/catalogues.py)

### Read the records

| SQLite table | Contents |
| --- | --- |
| `enzymes` | Enzyme name, REBASE ID, forward recognition motif, span, known-cleavage state and reported overhang |
| `cleavage_pairs` | One or two pairs of strand-cut offsets for enzymes with known cleavage |
| `suppliers`, `enzyme_suppliers` | Supplier identities and enzyme availability at capture |
| `data_issues` | Recorded source discrepancies for TscAI and TspRI |
| `metadata` | Source version, attribution, license text, schema and coordinate conventions |

Cut offsets on both strands are zero-based boundaries measured from the left edge of
the forward 5′→3′ motif. EcoRI recognizes `GAATTC` and has top and bottom offsets of
1 and 5. Offsets can lie outside a motif. Zero cut pairs means the cleavage geometry
is unknown. Alternative motifs use `|`, and the source span is stored separately.
TscAI and TspRI retain conflicting reported overhang lengths and cut positions.
The catalogue covers recognition and cut geometry. Reaction conditions and
experimental digestion performance require separate evidence.

| Operation | Evidence and limits for Biopython 1.88 / REBASE EMBOSS 404 |
| --- | --- |
| Read-only lookup | The release checks cover all 1,088 records, supplier listings as captured, paired-strand geometry, retained source discrepancies, literal search and pagination. Try a [lookup and search in Python](../../docs/notebooks/03_enzyme_catalogue.ipynb). |
| Virtual digestion | D01–D08 cover the 13 reviewed enzyme definitions. Unknown cuts, flagged definitions, multiple cut pairs and alternative-motif cleavage are outside the digestion model. Reaction chemistry and supplier availability today are outside this scope. |

## Sources and distribution

Of 1,088 enzymes, 754 have known cleavage and 334 have unknown cleavage. There are
779 cut pairs, 623 enzymes with listed suppliers and two flagged enzymes. Coverage
applies to the captured Biopython dictionary. The database preserves REBASE
attribution, the Biopython source header and the installed Biopython license.
The comparison panel uses a separate local copy of the official REBASE 611 EMBOSS export. The installed catalogue remains the recorded 404 build. E02 still needs the integration, coverage and redistribution review for a new catalogue release. Keep the database, ZIP and
generated source captures local. [REBASE](https://rebase.neb.com/rebase/rebase.html)

### Inspect provenance

| Directory inside `provenance.zip` | Contents |
| --- | --- |
| `snapshots/biopython-1.88-rebase404-2a79099295db/` | Active build manifest and validation report |
| `raw/installed_biopython/1.88-rebase404-2a79099295db/` | Captured restriction dictionary, engine, Biopython license and source manifest |
| `snapshots/biopython-1.86-rebase404-2a79099295db/` | Historical SQLite database and original manifest |

The 1.86 and 1.88 builds have identical SQLite schemas and identical rows in all
five biological and supplier tables. Both use the same REBASE 404 dictionary. The
metadata differences are the recorded Biopython version, license text and creation
time. Both SQLite files match their respective build-manifest SHA-256 values. The
1.88 source manifest, dictionary, engine, license and validation report also match
their recorded hashes.

The following commands list and test the archive, then print the active build manifest.

```sh
unzip -l databases/enzymes/provenance.zip
unzip -t databases/enzymes/provenance.zip
unzip -p databases/enzymes/provenance.zip \
  snapshots/biopython-1.88-rebase404-2a79099295db/manifest.json
```

To inspect the historical database or captured sources on disk, extract the ZIP
into a temporary directory. It retains the member paths shown above.

```sh
enzymeProvenanceDir="$(mktemp -d)"
unzip databases/enzymes/provenance.zip -d "$enzymeProvenanceDir"
```

## Rebuild and verify

To restore the recorded release exactly, put both regular files below in this
directory and compare their SHA-256 values with the table.

| Local artifact | SHA-256 |
| --- | --- |
| `restriction_enzymes.sqlite3` | `7dc3bff8fc06903019f31408a96a96457bcf4c627f8513d08b89497d11abfa91` |
| `provenance.zip` | `284f7e36db4fb77240aca32df415e209139f39bb255b4fed74a8eb01bf745ca0` |

For lookup and simulation in a fresh checkout, run the offline builder from the
checkout root with Biopython 1.88. The pinned restriction dictionary has SHA-256
`2a79099295dbad6061ea67a11e053787c591fcb2eb10fc8c0f89ead908dfa02b`.
Each build records a new creation time and SQLite checksum.

```sh
.venv/bin/python -m databases.enzymes.build_catalogue \
  --output databases/enzymes/restriction_enzymes.sqlite3
```

For a versioned update, choose fresh source and snapshot directories. The
[builder](build_catalogue.py) checks the database against
the installed source and writes a manifest, validation report and captured source
files, including the package license. It rejects existing destinations.

```sh
catalogueBuildDir="$(mktemp -d)"
.venv/bin/python -m databases.enzymes.build_catalogue \
  --snapshot-dir "$catalogueBuildDir/snapshots/candidate-01" \
  --source-dir "$catalogueBuildDir/raw/installed_biopython/candidate-01"
```

Compare the release, records and issues before promotion. Keep the outgoing SQLite
file beside its versioned manifest in the provenance ZIP and verify its checksum
before replacing the active file. Record the release in this guide and the
[database index](../README.md).
