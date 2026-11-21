# Annotated plasmid references

Seven complete circular GenBank maps for bacterial cloning, bacterial expression and mammalian fluorescence. [Scientific data index](../README.md)

## Files

| File | Purpose | Availability |
| --- | --- | --- |
| [maps/](maps/) | Four annotated GenBank examples and the [SB2CL license](maps/SB2CL-LICENSE.txt) | Distributed |
| [sources/M77789.2.gb](sources/M77789.2.gb) | Original pUC19 GenBank deposit used to make the labeled example | Distributed |
| `local/` | Three additional NCBI comparison maps named by accession and version | Local, excluded from Git |
| [references.json](references.json) | Source, version, path, feature count and checksums for every map | Distributed |

| Plasmid | Backbone or use | bp | Features | Map |
| --- | --- | ---: | ---: | --- |
| pBH4-His-SUMO-GFP-LPETGG | Bacterial protein expression, fluorescent fusion | 6,759 | 19 | [Dryad map](maps/pBH4-His-SUMO-GFP-LPETGG.gb) |
| pBH4-MBP-TeCH | Bacterial protein expression, maltose-binding protein fusion | 6,921 | 13 | [Dryad map](maps/pBH4-MBP-TeCH.gb) |
| pIAKA1s271_11_L30_01 | pSC101-based bacterial GFP reporter | 3,965 | 10 | [SB2CL map](maps/pIAKA1s271_11_L30_01.gb) |
| pUC19 | pUC high-copy cloning vector | 2,686 | 5 in the labeled example, 8 in the deposit | [Labeled example](maps/pUC19.gb) · [Original deposit](sources/M77789.2.gb) |
| pBR322 | pBR322 cloning vector | 4,361 | 50 | [J01749.1](https://www.ncbi.nlm.nih.gov/nuccore/J01749.1) |
| pACYC184 | p15A cloning vector | 4,245 | 8 | [X06403.1](https://www.ncbi.nlm.nih.gov/nuccore/X06403.1) |
| pEGFP-N1 | Mammalian fluorescent fusion vector | 4,733 | 4 | [U55762.1](https://www.ncbi.nlm.nih.gov/nuccore/U55762.1) |

Counts include a whole-record `source` when present. The labeled pUC19 example combines its source entry with the four landmarks below.

The checkout supplies the four files in `maps/` for direct GenBank
intake. Keep their [manifest](references.json), this guide and the SB2CL notice with
the maps. On the `2026-11-19.2` files, verification checks record ID, topology,
length, feature count, original file SHA-256, uppercase sequence SHA-256 and
canonical circular fingerprint. The three additional NCBI comparison maps are optional local
files. Two full-circle pBH4 identities and two regional SB2CL labels overlap the
feature catalogue. [Restore and verify](#restore-and-verify) ·
[Feature catalogue scope](../features/README.md#release-scope)

## pUC19 map labels

Open [pUC19.gb](maps/pUC19.gb) to see the four landmarks below. The Sequence workspace and [README map](../../docs/images/pUC19.svg) use this file. It contains every base of M77789.2 in the deposited order. The feature table uses four functional labels plus a whole-record source. The [original GenBank file](sources/M77789.2.gb) preserves Messing’s eight deposited features, notes and citations.

| Map label | Meaning | Coordinates in M77789.2 |
| --- | --- | --- |
| **lacZ alpha** | Coding sequence for the alpha fragment of β-galactosidase | 216–539 |
| **MCS** | Multiple cloning site, the polylinker within lacZ alpha | 233–289 |
| **bla (AmpR)** | β-lactamase coding sequence, the ampicillin resistance marker | 885–1,745 |
| **pMB1 ori** | pMB1-derived origin of replication, from the RNAII promoter’s −35 element to the RNA/DNA switch | 1,916–2,504 |

Coordinates are one-based and inclusive on the forward strand. The MCS uses the 57-base polylinker interval annotated in M77789.2. The other three intervals follow NEB’s pUC19 feature table. Its reference, L09137.2, matches M77789.2 across the entire circle after reverse-complementing and rotating the sequence. The landmarks were mapped into M77789.2 coordinates before rendering. Georgia Tech’s teaching diagram illustrates the same landmarks and links to an earlier sequence revision. [M77789.2](https://www.ncbi.nlm.nih.gov/nuccore/M77789.2) · [L09137.2](https://www.ncbi.nlm.nih.gov/nuccore/L09137.2) · [NEB feature coordinates](https://www.neb.com/en/-/media/nebus/page-images/tools-and-resources/interactive-tools/dna-sequences-and-maps/puc19_map.pdf) · [Georgia Tech teaching map](https://williams.chemistry.gatech.edu/course_Information/4581/labs/puc_19/puc_19.html)

## Read a map

From the repository root, use Biopython to open the Dryad GFP fusion and compare it with the [feature catalogue](../features/README.md). The desktop can also open these GenBank maps directly. [Import formats](../../docs/importing_sequences.md)

```python
from Bio import SeqIO
from strandfolio.storage.feature_catalogue import FeatureCatalogue

record = SeqIO.read("databases/plasmids/maps/pBH4-His-SUMO-GFP-LPETGG.gb", "genbank")
with FeatureCatalogue("databases/features/features.sqlite3") as catalogue:
    (match,) = catalogue.identify(str(record.seq), circular=True)
    print(match.record.label)

egfp = next(f for f in record.features if "EGFP" in f.qualifiers.get("label", []))
print(egfp.type, int(egfp.location.start) + 1, int(egfp.location.end), egfp.location.strand)
```

The example identifies `pBH4-His-SUMO-GFP-LPETGG` and locates EGFP at 1,207–1,923 on the forward strand (1-based, inclusive). The authors annotated it as `misc_feature`. In the SB2CL reporter the GFP annotation is a CDS at 2,490–3,206 on the forward strand.

## Sources and distribution

- **Dryad.** The two pBH4 maps use the authors’ deposited filenames. The manifest records attribution and original LOCUS names. Both file checksums match the earlier whole-plasmid review. [Dataset](https://doi.org/10.5061/dryad.c59zw3rkb) · [CC0 terms](https://datadryad.org/help/guides/reuse) · [Independent checks](../features/README.md#complete-circular-plasmids-from-dryad)
- **SB2CL.** Vignoni and Boada’s annotated reporter comes from release 1.0.2. Keep the MIT notice with the map. The manifest pins the source commit. [Dataset](https://doi.org/10.5281/zenodo.18828308) · [License declaration](https://github.com/sb2cl/combinatorial-promoter-rbs-library/blob/848947015a2b52c17161906238a853d9ddf43bd3/zenodo_metadata.json) · [Original notice](maps/SB2CL-LICENSE.txt)
- **GenBank.** The pUC19 file preserves J. Messing’s submission and cites Yanisch-Perron, Vieira and Messing (1985). Its feature table contains a whole-record source and seven `misc_feature` entries, with notes identifying the lac region, polylinker and backbone segments. NCBI places no restrictions on distributing GenBank data and notes that individual submitters may hold rights. The original citations remain in the file. [pUC19 publication](https://pubmed.ncbi.nlm.nih.gov/2985470/) · [GenBank data usage](https://www.ncbi.nlm.nih.gov/genbank/about/) · [INSDC policy](https://www.insdc.org/policy/)

## Restore and verify

Release `2026-11-19.2` keeps the original files byte for byte. The pUC19 deposit matches the versioned NCBI download (SHA-256 `14b0b5ba06c7e24e0e2c2205c63f58155c17ea74e3ec5806243aba87e8394c22`). The manifest also records the separate pUC19 display file checksum, the uppercase sequence hash and the whole-circle fingerprint. The fingerprint is independent of starting position and strand.

<details>
<summary>Verify the available maps with Biopython</summary>

Run from the repository root. Set `requireLocal = True` to require all seven source records.

```python
import json
from hashlib import sha256
from pathlib import Path

from Bio import SeqIO
from strandfolio.storage.feature_catalogue import sequenceFingerprint

folder = Path("databases/plasmids")
manifest = json.loads((folder / "references.json").read_text())
requireLocal = False
for entry in manifest["records"]:
    path = folder / entry["path"]
    if entry["availability"] == "local" and not requireLocal and not path.exists():
        print("Local file absent", entry["name"])
        continue
    assert sha256(path.read_bytes()).hexdigest() == entry["file_sha256"], path
    record = SeqIO.read(path, "genbank")
    bases = str(record.seq).upper()
    assert record.id == entry["record_id"] and len(record) == entry["length_bp"], path
    assert record.annotations["topology"] == entry["topology"] == "circular", path
    assert len(record.features) == entry["feature_count"], path
    assert sha256(bases.encode("ascii")).hexdigest() == entry["sequence_sha256"], path
    assert sequenceFingerprint(bases, circular=True) == entry["circular_sha256"], path
    if "display_map" in entry:
        display = entry["display_map"]
        example = folder / display["path"]
        assert sha256(example.read_bytes()).hexdigest() == display["file_sha256"], example
        labeled = SeqIO.read(example, "genbank")
        assert labeled.name == entry["name"] and labeled.seq == record.seq, example
        assert len(labeled.features) == display["feature_count"], example
    print("Verified", entry["name"])
```

</details>

<details>
<summary>Restore missing deposited maps by command line</summary>

Run from the repository root to restore deposited source files. Each download must match its pinned checksum before it is saved. The separately labeled [pUC19 example](maps/pUC19.gb) ships with the repository.

```sh
.venv/bin/python - <<'PY'
import json
import subprocess
from hashlib import sha256
from pathlib import Path

folder = Path("databases/plasmids")
for entry in json.loads((folder / "references.json").read_text())["records"]:
    path = folder / entry["path"]
    if path.exists():
        continue
    data = subprocess.check_output(["curl", "--fail", "--silent", "--show-error",
                                    "--location", "--max-time", "60", entry["download_url"]])
    if sha256(data).hexdigest() != entry["file_sha256"]:
        raise SystemExit(f"Upstream file differs from the reviewed copy: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(data)
    print("Restored", path)
PY
```

</details>
