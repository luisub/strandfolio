# Electrophoresis ladder catalogue

[ladders.sqlite3](ladders.sqlite3) contains **12 DNA profiles and one six-protein reference** calculated with Biopython 1.88. Release `2026-11-17.1` contains 152 component occurrences calculated from six DNA and six protein source records. Five conventional DNA digests reproduce every published fragment size and multiplicity for a commercial marker. The remaining records include published alternatives, comparisons with known discrepancies, and theoretical protein masses. The database works offline. [Scientific data index](../README.md)

## Files

| File | Purpose | Availability |
| --- | --- | --- |
| [ladders.sqlite3](ladders.sqlite3) | Profile names, component sizes, source records and product comparisons | Distributed with strandfolio |
| [references.json](references.json) | Versioned inputs, sequence checksums, digest definitions and comparison evidence for the builder | Distributed with strandfolio |
| `raw/2026-11-17/references.fasta` | Normalized DNA and protein records for an offline rebuild | Local cache, excluded from Git |

## Supported size previews

Keep the released SQLite and JSON files together in this directory in a local
source checkout. The native Digest, PCR and Gel previews read the 12 DNA profiles
in base pairs. Notebook 08 loads the released DNA profiles through the same Python API. The local FASTA cache is an
input to the offline rebuild.

| Operation | Evidence and limits for release `2026-11-17.1` |
| --- | --- |
| Read catalogue sizes | L01–L02 check component identity, exact bp, nominal labels where recorded, duplicate size counts and source comparisons. The six-protein profile records theoretical masses for catalogue queries. [DNA ladder and gel example](../../docs/notebooks/08_ladders_and_gels.ipynb) |
| Display a DNA gel or reaction preview | L03–L06 cover a shared logarithmic size scale for linear DNA, 100–10,000 bp by default. Equal sizes share a band with traceable contributors. Out-of-scale components and circular products remain in the inventory. The default marker is Penn State 1 kb · extended (`psu-1kb-extended`). [Native gel workflow](../../docs/sequence_workspace.md#digest-a-sequence-and-reopen-its-gel) |

Calibrated migration, resolution, band intensity, experimental yields and
protein or RNA gel previews are outside the desktop size model.

## Query the database

Run the read-only query from the repository root. This query returns the 14 distinct sizes in the Penn State 100 bp lane and the number of components at each size.

```sh
sqlite3 -readonly -header -column databases/ladders/ladders.sqlite3 \
  "SELECT size, unit, component_count FROM size_classes WHERE ladder_id='psu-100bp' ORDER BY size DESC"
```

List names and units in `ladders`. Join `components` to `preparations` on `(ladder_id, prep)` and to `sources` on `source_id = sources.id` to trace each size to its inputs. `nominal_bp` holds a documented label override. SQL NULL means no override was recorded. `theoretical_kda` applies only to proteins. `comparisons` gives the quantity reviewed for each referenced product or paper. Migration distance, intensity, loading mass and gel-specific visibility require experimental measurements.

## Sources and distribution

| Source | What enters the release | Attribution and terms |
| --- | --- | --- |
| INSDC via DDBJ | Derived lengths, accession.version, normalized sequence checksum and topology. | Depositor records for [J01749.1](https://getentry.ddbj.nig.ac.jp/getentry/na/J01749), [M77789.2](https://getentry.ddbj.nig.ac.jp/getentry/na/M77789?filetype=html), [J02482.1](https://getentry.ddbj.nig.ac.jp/getentry/na/J02482) and [J02459.1](https://getentry.ddbj.nig.ac.jp/getentry/na/J02459). INSDC provides open access. GenBank describes the limits of its ability to clear third-party claims. [INSDC policy](https://www.insdc.org/policy/) · [GenBank notice](https://www.ncbi.nlm.nih.gov/genbank/about/) |
| Penn State DNA ladder | Digest sizes computed from Song Tan's DNASU deposits. Variant definitions and two nominal label overrides are adapted from the paper. | Ryan C. Henrici, Turner J. Pecen, James L. Johnston and Song Tan (2017), *The pPSU Plasmids for Generating DNA Molecular Weight Markers*. The paper is CC BY 4.0. Its statement about distribution to nonprofits concerns physical plasmids. This release contains derived data and citations. [Paper and notice](https://doi.org/10.1038/s41598-017-02693-1) · [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| UniProtKB and wwPDB | Accessions, mature-chain intervals and masses calculated from the amino acids. | UniProt Consortium, CC BY 4.0. wwPDB, entry 1AVU, CC0 1.0. [UniProt license](https://rest.uniprot.org/help/license) · [wwPDB policy](https://www.wwpdb.org/about/usage-policies) |
| Manufacturer documents | Product identifiers, source links, comparison outcomes and one-way checksums used to verify published size lists. | Vendor content serves as an external check. Vendor sequences, tables, photographs, artwork, colors and loading profiles are excluded from the distributed files. [NEB terms](https://www.neb.com/en-us/terms-of-use) · [Bio-Rad product documentation](https://www.bio-rad.com/sites/default/files/webroot/web/pdf/lsr/literature/4110025.pdf) |

Original strandfolio code uses [BSD-3-Clause](../../LICENSE). Preserve the third-party attributions above with the database. A comparison establishes agreement with a particular source on the stated quantity. Certification and traceability require evidence for a specific physical material, property and method. For an example, NIST's archived SRM 2390 certificate defines its scope for forensic RFLP measurements. [NIST SRM 2390 certificate](https://tsapps.nist.gov/srmext/certificates/archives/2390.pdf)

### DNA marker comparisons

Each row represents a complete digest of one reference molecule. The component count includes repeated lengths. The distinct-size count groups only mathematically equal lengths. Both can differ from the number of visible bands.

| Reference and digest | Components / distinct sizes | Calculated range (bp) | Comparison against product documentation, reviewed 2026-11-17 |
| --- | ---: | ---: | --- |
| pBR322 J01749.1 / BstNI | 6 / 6 | 13–1,857 | All six sizes agree, including the 13 bp fragment omitted from the representative gel illustration. [NEB N3031](https://www.neb.com/en-us/products/n3031-pbr322-dna-bstni-digest) |
| pBR322 J01749.1 / MspI | 26 / 21 | 9–622 | All sizes and five pairs of equal lengths agree after expanding paired entries in the product table. [NEB N3032](https://www.neb.com/en-us/products/n3032-pbr322-dna-mspi-digest) |
| ΦX174 J02482.1, double-stranded replicative form / HaeIII | 11 / 11 | 72–1,353 | All component sizes agree. NEB lists 281 and 271 bp together in one row. The two components retain their identities. [NEB N3026](https://www.neb.com/en-us/products/n3026-x174-dna-haeiii-digest) |
| pUC19 M77789.2 / MspI | 13 / 12 | 26–501 | All sizes agree, including two 34 bp fragments. The manufacturer lists MspI as the enzyme used. [Thermo Scientific SM0221, MAN0012992 rev. C.00](https://documents.thermofisher.com/TFS-Assets/LSG/manuals/MAN0012992_pUC19_DNA_MspI_HpaII_Marker_50ug_UG.pdf) |
| λ J02459.1 / EcoRI | 6 / 6 | 3,530–21,226 | All six sizes agree. The terminal fragments can associate through the cohesive λ ends, which can add a larger apparent band. [Thermo Scientific SM0281, MAN0013003 rev. C.00](https://assets.thermofisher.com/TFS-Assets/LSG/manuals/MAN0013003_Lambda_DNA_EcoRI_Marker1_UG.pdf) |
| ΦX174 replicative form / HinfI | 21 / 20 | 24–726 | All 20 distinct sizes agree. The reference produces two 66 bp fragments, and the product sheet lists 66 bp once. It also reports limited separation of several unequal fragments on 2% agarose. [Invitrogen 15616-014, p. 2](https://documents.thermofisher.com/TFS-Assets/LSG/manuals/15616014.pdf) |
| λ J02459.1 / HindIII | 7 / 7 | 564–23,130 | Wild-type digest differs from N3012. Its 6,682 bp fragment corresponds to 6,557 + 125 bp in NEB’s eight-fragment preparation. NEB identifies an additional HindIII site in its strain. [NEB N3012](https://www.neb.com/products/n3012-dna-hindiii-digest) · [strain note](https://www.neb.com/en-us/faqs/what-are-the-mutations-in-your-lambda-dna) |
| λ J02459.1 / BstEII | 14 / 14 | 117–8,453 | Twelve lengths agree. The two remaining lengths are 8,453 and 5,687 bp in the calculation and 8,454 and 5,686 bp on the sheet. The cause is unresolved. NEB discontinued this product in 2025. [NEB N3014](https://www.neb.com/en/products/n3014-dna-bsteii-digest) |

The calculation treats pBR322, pUC19 and ΦX174 RF as circular double-stranded substrates and λ as isolated linear double-stranded DNA. Lengths are spans on the reference strand under complete cleavage. The builder uses Biopython’s substrate topology and preserves each fragment before grouping. It verifies conservation separately for every digest. It assumes the selected sites can all be cut. Sample methylation, incomplete digestion, cohesive-end association and gel resolution can change an experimental lane. [Biopython restriction analysis](https://biopython.org/DIST/docs/cookbook/Restriction.html)

### Common 100 bp and 1 kb variants

Henrici and colleagues published two plasmids specifically for DNA size standards. The authors deposited complete 10,000 bp and 7,750 bp sequences at DNASU. The lengths below come from those records. In a profile containing several enzymes, each listed digest is a **separate preparation** whose fragments are pooled. A simultaneous double digest would give different fragments. [pPSU1](https://dnasu.org/DNASU/GetVectorDetail.do?vectorid=744) · [pPSU2](https://dnasu.org/DNASU/GetVectorDetail.do?vectorid=746) · [Henrici et al., Table 1 and Figs. 2, 4–6](https://doi.org/10.1038/s41598-017-02693-1)

| Profile | Inputs | Components / distinct sizes | Result |
| --- | --- | ---: | --- |
| Penn State 100 bp | Both plasmids, PstI | 16 / 14 | 50–4,100 bp. Both 500 and 4,100 bp occur twice. The complete fragment list agrees with the paper. |
| Penn State 1 kb, core | Both plasmids, EcoRV | 8 / 8 | 500–5,000 bp, including 750 and 1,500 bp. |
| Penn State 1 kb, extended | Core plus each plasmid separately linearized with NcoI | 10 / 10 | Adds 7,750 and 10,000 bp. Both lengths agree with the published map. |
| Penn State 1 kb, pPSU1 alternative | Separate EcoRV, EcoRI and NcoI digests of pPSU1 | 8 / 8 | Reproduces the labels across 500–10,000 bp. The two EcoRI products are exactly 2,999 and 7,001 bp. Their published labels are 3,000 and 7,000 bp. |

NEB N3231 and the Penn State 100 bp profile share all ten steps from 100 through 1,000 bp. N3231 reports an additional 517 bp component beside the 500 bp landmark. Penn State supplies a second 500 bp fragment. For the 1 kb family, the Penn State extended profile and NEB N3232 both display an upper landmark around 10 kb. The respective sequences yield 10,000 bp for Penn State and the vendor reports 10,002 bp for N3232. These are separately identified profiles. [NEB 100 bp N3231](https://www.neb.com/products/n3231-100-bp-dna-ladder) · [NEB 1 kb N3232](https://www.neb.com/en-us/products/n3232-1-kb-dna-ladder)

NEB also describes proprietary plasmid mixtures for its 50 bp and 1 kb Plus products. No independently documented complete substrate set was verified here for either product. A 50 bp band in the Penn State 100 bp profile does not constitute a regular 50 bp ladder. The 1 kb Plus product distinguishes exact component sizes, nominal labels and deliberately enhanced bands. The manufacturer selector and specifications remain external comparison documents. [NEB selector](https://www.neb.com/en-us/tools-and-resources/selection-charts/dna-markers-and-ladders-selection-chart) · [50 bp N3236](https://www.neb.com/en-us/products/n3236-50-bp-dna-ladder) · [1 kb Plus N3200](https://www.neb.com/en-us/products/n3200-1-kb-plus-dna-ladder)

### Protein standards

The conventional low-range series uses six protein families identified in the electrophoresis literature. We selected publicly deposited representative chains. Biopython calculates the average isotopic mass of a reduced, unmodified polypeptide with free termini. Protein coordinates below are 1-based and inclusive in the deposited sequence. Precursor processing matters for albumin and lysozyme. Glycosylation, cofactors, dyes and migration conditions can shift a reference band's apparent mass. [Santilli et al., introduction and Fig. 4](https://doi.org/10.1038/s41598-021-96051-x) · [Bio-Rad low-range standard 161-0304, 4006033 rev. C](https://www.bio-rad.com/webroot/web/pdf/lsr/literature/4006033.pdf)

| Representative protein | Pinned record | Chain | Calculated kDa |
| --- | --- | ---: | ---: |
| Rabbit muscle glycogen phosphorylase | [P00489, SV 3](https://rest.uniprot.org/uniprotkb/P00489.txt) | 2–843 | 97.157 |
| Bovine serum albumin | [P02769, SV 4](https://rest.uniprot.org/uniprotkb/P02769.txt) | 25–607 | 66.432 |
| Chicken ovalbumin | [P01012, SV 2](https://rest.uniprot.org/uniprotkb/P01012.txt) | 2–386 | 42.750 |
| Bovine carbonic anhydrase II | [P00921, SV 3](https://rest.uniprot.org/uniprotkb/P00921.txt) | 2–260 | 28.982 |
| Soybean Kunitz trypsin inhibitor | [1AVU, entity 1, chain A](https://www.rcsb.org/structure/1AVU) | 1–181 | 20.094 |
| Chicken lysozyme C | [P00698, SV 1](https://rest.uniprot.org/uniprotkb/P00698.txt) | 19–147 | 14.313 |

The six protein families correspond to the Bio-Rad low-range standard. For example, the manufacturer's ovalbumin label is 45 kDa and the selected mature amino-acid chain gives 42.750 kDa. The catalogue records this comparison as **protein families only**. It makes no claim about the exact proteoforms in a Bio-Rad vial or their mobility. [Bio-Rad low-range table](https://www.bio-rad.com/webroot/web/pdf/lsr/literature/4006033.pdf)

Bio-Rad Precision Plus Dual Color 161-0374 is a separate recombinant, prestained system. Its manual shows a representative 4–20% Criterion TGX gel, gives ten assigned masses and describes confirmation by Laemmli SDS-PAGE. Dual Xtra has 12 bands and extends to 2 kDa. No open, product-specific combination of sequences, dye assignments and migration calibration was established for these formulations. As an independent experimental alternative, Santilli et al. report a nine-protein 10–100 kDa Penn State ladder and directly compare its migration with two Bio-Rad standards on 18% and 4–20% gels. That experiment is documented here for comparison. The current protein database entry is the six-chain calculation above. [Bio-Rad family chart](https://www.bio-rad.com/en-us/category/protein-ladders-standards-markers?ID=09507551-2848-4bd1-a3c1-650b4d41aa48) · [Dual Color manual 4110025 rev. H](https://www.bio-rad.com/sites/default/files/webroot/web/pdf/lsr/literature/4110025.pdf) · [Santilli et al., Fig. 4](https://doi.org/10.1038/s41598-021-96051-x)

## Rebuild and verify

The [manifest](references.json) pins the sequences, recipes and comparison evidence. The maintainer’s local builder computes the components first and then checks its outputs. Comparison digests hash the ascending decimal bp values, joined by commas as ASCII, preserving duplicates except in the explicitly distinct-size check. Only the nominal comparison applies the two published label overrides.

From a fresh clone, compare the SQLite checksum with the value in the release table below.

```sh
shasum -a 256 databases/ladders/ladders.sqlite3
```

Maintainers with the local `dev/` directory can rebuild from the repository root with Biopython 1.88 and the local FASTA. The builder requires a new SQLite path. The final command compares the rebuilt file with the release byte for byte.

```sh
ladderBuildDir="$(mktemp -d)"
.venv/bin/python -m dev.build_ladder_catalogue \
  --sequences databases/ladders/raw/2026-11-17/references.fasta \
  --output "$ladderBuildDir/ladders.sqlite3"
cmp databases/ladders/ladders.sqlite3 "$ladderBuildDir/ladders.sqlite3"
```

To repopulate a maintainer’s local sequence cache, add `--fetch` and set `--sequences` to a new FASTA path. The downloader verifies every normalized sequence against its pinned hash before writing the cache. A change in an upstream sequence requires review. Each record’s sequence hash is independent of FASTA wrapping and record order.

| Release artifact | SHA-256 of this file |
| --- | --- |
| `ladders.sqlite3` | `8147c254d071be532be19f4c0d035518a4b794d5ac4d0e196c38572635bdfaa6` |
| `raw/2026-11-17/references.fasta` | `5f09650f78b02619b3398f0f078b40d406a6c654d3dead7a58fb656cc1514517` |

The FASTA retains the public records’ uppercase sequence letters. The release contains their identifiers, checksums and calculated measurements.
