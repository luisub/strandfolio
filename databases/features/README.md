# Feature catalogue

The catalogue identifies eight exact DNA references. Six label regions within DNA
and two identify complete circular plasmids. Each record gives a stable ID, source,
length in base pairs, topology and SHA-256 fingerprint. [Scientific data index](../README.md)

## Files

| File | Purpose | Availability |
| --- | --- | --- |
| [features.sqlite3](features.sqlite3) | Read-only SQLite release for matching and lookup | Checkout, wheel and source distribution |
| [references.json](references.json) | Reviewed build input with four sources, eight reference fingerprints and their aliases | Checkout |
| `raw/uva-cbln2-v1/` | Five deposited files and their export inventory | Local download. [File inventory and public deposit](#human-cbln2-source) |
| `raw/goldenpics-2017/` | Publisher's supplementary workbook, reviewed for whole-plasmid coverage | Local download. [File details and public copy](#goldenpics-workbook-review) |

For a local source checkout, keep the SQLite file and manifest together at the
paths above. Sequence uses the released SQLite file for **Detect labels**. The
raw reference files are optional source material. The catalogue identifies
its schema 1 release by the SQLite SHA-256
`6d4ef97da059186977a23eba9c2a05fd53d6b884895ff41c6e059ab276a28067`.

## Query the database

| Catalogue label | Reference molecule | Length |
| --- | --- | ---: |
| pBH4-MBP-TeCH | Complete circular plasmid | 6,921 bp |
| pBH4-His-SUMO-GFP-LPETGG | Complete circular plasmid | 6,759 bp |
| Human CBLN2 putative promoter (huC2Pro) | Human GRCh38 reference fragment | 1,798 bp |
| Human EEF1A1 promoter region (CHM13 allele) | Human T2T-CHM13v2.0 reference fragment | 1,112 bp |
| pUC-ori | pIAKA1_11 | 725 bp |
| pSC101 origin | pIAKA1s271_11 | 951 bp |
| KanR (aph(3')-Ia) — pIAKA1_11 | pIAKA1_11 allele | 816 bp |
| KanR (aph(3')-Ia) — pIAKA1s271_11 | pIAKA1s271_11 allele | 816 bp |

The two KanR alleles have different nucleotide fingerprints. A match identifies
the recorded sequence. Phenotype requires separate experimental evidence.

Use the [catalogue reader](../../strandfolio/storage/feature_catalogue.py) from the
repository root to list the released records.

```python
from strandfolio.storage.feature_catalogue import FeatureCatalogue

with FeatureCatalogue("databases/features/features.sqlite3") as catalogue:
    for record in catalogue:
        print(record.label, record.lengthBp, record.sourceRecord)
```

For a single-record FASTA containing a circular plasmid, use `identify` for the
whole-molecule name and `scan` for feature positions. An empty result means the
catalogue has no exact match for that query.

```python
from Bio import SeqIO
from strandfolio.storage.feature_catalogue import FeatureCatalogue

plasmidDna = str(SeqIO.read("your_plasmid.fasta", "fasta").seq)
with FeatureCatalogue("databases/features/features.sqlite3") as catalogue:
    for match in catalogue.identify(plasmidDna, circular=True):
        print("Whole plasmid", match.record.label)
    for hit in catalogue.scan(plasmidDna, circular=True):
        print(hit.record.label, hit.segments, hit.strand)
```

Each hit reports zero-based, end-exclusive intervals in the input's forward
coordinate system. A feature that crosses the circular origin has two intervals.
Repeated copies have separate hits. Use `circular=False` for linear molecules.

Use `catalogue.identify(selectedDna)` to identify an entire selected DNA fragment
on either strand. Each match carries the reference, its original source and the
match strand. Ambiguity letters compare literally. In Sequence, **Detect labels** previews the catalogue matches. Accepted suggestions retain the catalogue snapshot and source reference in the named revision. [Review and save labels](../../docs/sequence_workspace.md#edit-annotations-and-review-detected-labels)

Notebook 09 shows how to review two catalogue matches on the distributed SB2CL
plasmid and select the complete feature that crosses its circular origin.
The catalogue supports exact matching. Approximate or partial matching, phenotype
inference and genome-wide feature prediction are outside its scope.
[Notebook 09](../../docs/notebooks/09_interactive_workspace.ipynb)

## Sources and distribution

Keep the JSON manifest and this guide with a standalone catalogue distribution.
The application wheel and source distribution include the SQLite catalogue with
its per-source attributions, the SB2CL MIT notice and the package data notices.
Original strandfolio curation, documentation and software use BSD-3-Clause.
[Package data notices](../../docs/installation/DATA-NOTICES.md) · [Strandfolio license](../../LICENSE)

### Replication origins and KanR alleles

The two origin and two KanR records
come from Alejandro Vignoni and Yadira Boada’s SB2CL dataset, *Combinatorial
Promoter–RBS Expression Library*, v1.0.2. The authors explicitly grant MIT terms for
the annotated dataset. The full SB2CL notice is included in the manifest and in the
SQLite `sources.attribution` column. Retain that notice when distributing these
files. [Dataset DOI](https://doi.org/10.5281/zenodo.18828308) ·
[Dataset license declaration](https://github.com/sb2cl/combinatorial-promoter-rbs-library/blob/848947015a2b52c17161906238a853d9ddf43bd3/zenodo_metadata.json) ·
[Original MIT notice](https://github.com/sb2cl/combinatorial-promoter-rbs-library/blob/848947015a2b52c17161906238a853d9ddf43bd3/LICENSE) ·
[Associated publication](https://doi.org/10.1038/s41467-026-76332-7).

| Pinned input | SHA-256 of original file bytes |
| --- | --- |
| [pIAKA1_11_L30_13.gb](https://github.com/sb2cl/combinatorial-promoter-rbs-library/blob/848947015a2b52c17161906238a853d9ddf43bd3/genbank/pIAKA1_11_L30_13.gb) | `17b0399d03e01d014fe37a5eac5a538ce962457b1b262ed6f3aa8112baec5180` |
| [pIAKA1s271_11_L30_01.gb](https://github.com/sb2cl/combinatorial-promoter-rbs-library/blob/848947015a2b52c17161906238a853d9ddf43bd3/genbank/pIAKA1s271_11_L30_01.gb) | `0356b818941208fce784c61809aad972898bc9b592358e32ce77037cc10e8f23` |

For each SB2CL record, `source_record` identifies the file, feature type and original
1-based inclusive GenBank location. To reproduce a fingerprint, extract that feature
in its annotated orientation, respecting `complement` and the ordering of `join`
parts across the circular origin. Hash the uppercase ASCII bases without a trailing
newline. Two independent extractions reproduced the four lengths and digests, and
the two translated CDSs agreed with the source annotations. Scanning both original
plasmids recovered all four feature intervals and strands, including the joined CDS.

### Complete circular plasmids from Dryad

Amy Weeks, Clara Frazier, Debashrito Deb, William Leiter and Umasankar Mondal
deposited these GenBank maps at Dryad on August 12, 2025. Dryad publishes its
datasets under CC0. Each display name follows the authors’ file inventory. The
manifest also retains its original LOCUS identifier, which can differ from the
filename. The reference covers the full circular map. [Dataset and original file
descriptions](https://doi.org/10.5061/dryad.c59zw3rkb) · [Dryad data terms](https://datadryad.org/help/guides/reuse).

| Deposited file | LOCUS in file | SHA-256 of original file bytes |
| --- | --- | --- |
| [pBH4-MBP-TeCH.gb](https://datadryad.org/downloads/file_stream/4228516) | `pBH4-His-Tev-MBP` | `2e8ab75fd0dfd3218b4d8a7b3bae837455b9d200d285120b413e8aaa601d11b4` |
| [pBH4-His-SUMO-GFP-LPETGG.gb](https://datadryad.org/downloads/file_stream/4228512) | `pBH4_His_SUMO_EGFP_LPET` | `3e5b54ec7c64cdf132c3fe6925ccc8b8f908118c1ba223ad98a0257fff356c91` |

To reproduce a circular fingerprint, read all bases from the GenBank `ORIGIN`,
check the LOCUS topology and length, and hash the lexicographically smallest
rotation across the forward and reverse-complement strands. Independent checks
agreed for both records. A different starting base preserves identity. A single
substitution or a truncated circle yields no whole-plasmid match.

### Human CBLN2 source

Arda Kipcak and Alev Erisir deposited *Sequence Homology and Transcription Factor
Binding Site Analysis* with the University of Virginia Dataverse in 2025. Version
1.0 lists five public data files under CC0 1.0. The catalogue uses the human FASTA
record in its deposited orientation. The authors report the human candidate at
GRCh38 chr18:72,542,815–72,544,612 (1-based, inclusive), a span of 1,798 bp. The
study provides comparative and genomic evidence for the human interval. Its
functional reporter experiments tested the tree-shrew sequence. The human label
therefore reads **putative promoter**. [Dataset and license](https://dataverse.lib.virginia.edu/dataset.xhtml?persistentId=doi%3A10.18130%2FV3%2FNEGJWP) ·
[Dataset DOI](https://doi.org/10.18130/V3/NEGJWP) · [Study](https://doi.org/10.1016/j.crmeth.2026.101309) ·
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).

The local export is in `databases/features/raw/uva-cbln2-v1/`.

| File in the original download | Bytes | Role |
| --- | ---: | --- |
| `huCBLN2Pro.fa` | 1,844 | Source for the human CBLN2 fingerprint |
| `huPromoter Panel.tab` | 137,136 | Human transcription-factor binding-site analysis |
| `msCBLN2Pro.fa` | 1,192 | Mouse comparison fragment |
| `tsCBLN2Pro.fa` | 1,862 | Tree-shrew comparison fragment |
| `SCRIPT_R_TFBS_Analysis_KipcakAndErisir.R` | 8,592 | Authors’ comparative analysis script |
| `MANIFEST.TXT` | 298 | Dataverse export inventory for the five deposited files |

The five deposited file sizes match `MANIFEST.TXT`. The human FASTA file’s SHA-256 is
`031e32a3474b502c7dbf8bdb6dad1c901870eb9c77b32ad4671410188d9bfc7e`
and its MD5 is `eeb1324469c782fa4b9443b92d3d3b95`. The sequence fingerprint in
`references.json` covers only the uppercase bases, excluding the header and line endings.

### Human EEF1A1 promoter region

This fingerprint identifies the **CHM13 allele** of a 1,112 bp human EF1α
promoter region. Its sequence comes from the T2T Consortium's CC0 assembly
T2T-CHM13v2.0. On chromosome 6, take the reverse complement of
`NC_060930.1:74697151..74698262` (1-based, inclusive) to recover the stored
orientation. Its uppercase sequence SHA-256 is
`5e6894d117d475c6dc743bd7c17ac86ad65290329305f4804f51218b2d66ae2d`.
[Assembly](https://www.ncbi.nlm.nih.gov/datasets/genome/GCA_009914755.4/) ·
[Chromosome interval](https://www.ncbi.nlm.nih.gov/nuccore/NC_060930.1?from=74697151&to=74698262&strand=2) ·
[Consortium data terms](https://github.com/marbl/CHM13#data-reuse-and-license).

Mikkelsen et al. identify the boundaries of a cloned human EF1α promoter in
Table 2 as `J04617.1:450..1561`. An ungapped comparison with that accession
matched 1,111 of 1,112 bases. Position 23 differs in promoter orientation.
The exact matcher distinguishes these alleles. A plasmid carrying the published
comparison allele will receive no EEF1A1 label from this row. [Table 2](https://doi.org/10.1016/S1525-0016(03)00216-8) ·
[Comparison accession](https://www.ncbi.nlm.nih.gov/nuccore/J04617.1).

### GoldenPiCS workbook review

Prielhofer et al. published `12918_2017_492_MOESM2_ESM.xlsx` as Additional file 2.
The local copy is in `databases/features/raw/goldenpics-2017/`. The 34,809-byte
copy supplied matches the publisher download byte-for-byte (SHA-256
`0cbb4d15ac533968cccdc191cf00eac52c656fed6fb1952aac4decab80de78d7`).
The `modules` sheet contains part sequences. The `plasmids` sheet lists construct
names, purposes and primer sequences. The entries for BB1_12, BB1_23 and BB1_34
contain no complete circular sequence. This workbook supplies **zero whole-plasmid
records** in this release. The publisher applies CC0 to article data unless an
exception is specified. [Publication and data terms](https://doi.org/10.1186/s12918-017-0492-3) ·
[Publisher workbook](https://static-content.springer.com/esm/art%3A10.1186%2Fs12918-017-0492-3/MediaObjects/12918_2017_492_MOESM2_ESM.xlsx).

### Release scope

The public catalogue accepts reviewed MIT or BSD-3-Clause data and works explicitly
dedicated under CC0-1.0. The four SB2CL features use MIT. Both human intervals
and both complete plasmid references come from CC0-1.0 data. Review the rights for the
actual data and any credited third-party parts before adding a row. A repository
license for software alone is insufficient. Use a named release or versioned record,
and keep the original coordinates, attribution and license evidence with its digest.

The build reads only the reviewed JSON. Privately supplied examples, commercial
feature lists and the local restriction-enzyme database are outside this release.
A comparison-only source, when its terms permit testing, may be used to check a result.
Do not copy its annotations, names unique to its catalogue, sequences or fingerprints
into release inputs.

| Requested coverage | Status |
| --- | --- |
| Human promoter fragments used in plasmids | The released EEF1A1 reference is the CHM13 allele. Exact references for the published cloned EF1α allele and human UBC are still open. Qin et al. provide comparison fragments under CC BY. [Promoter supplement](https://doi.org/10.1371/journal.pone.0010611.s001) · [Article and license](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0010611) |
| Endogenous human promoters | Exact matching covers the CBLN2 candidate and the CHM13 EEF1A1 interval. Genome-wide promoter prediction is outside the scope of this catalogue. |
| Whole plasmids | Two named pBH4 derivatives are released. Each requires a full-circle match. A shared backbone or origin alone supports a regional label. |

### strandfolio distribution notice

For a standalone copy of this catalogue, the project notice follows. The upstream
MIT notice travels with each copy of the database and its manifest.

```text
BSD 3-Clause License

Copyright (c) 2026 Luis Aguilera

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## Rebuild and verify

From a fresh clone, verify the SQLite file against the SHA-256 in [Files](#files).

```sh
shasum -a 256 databases/features/features.sqlite3
```

Maintainers with the local `dev/` and `tests/` directories can rebuild from the
released manifest using a fresh output path. The builder validates source terms,
links and fingerprints and refuses an existing destination.

```sh
featureBuildDir="$(mktemp -d)"
.venv/bin/python -m dev.build_feature_catalogue \
  --input databases/features/references.json --output "$featureBuildDir/features.sqlite3"
.venv/bin/python -m unittest discover -s tests -p 'test_feature_catalogue*.py'
```

The local build tests compare the tables in the release with a fresh build. The reader tests
exercise exact matching, circular coordinates and invalid inputs.
