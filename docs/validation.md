# Scientific software validation

This register records which outputs strandfolio reproduces, which programs produced the reference results, and where the evidence stops. It covers restriction digestion, exact PCR, melting temperature, pairwise and multiple alignment, Assembly and the desktop paths that expose their results. The comparison programs, full test fixtures and run logs are retained in the maintainer checkout. The examples and catalogue guides linked below are available in a fresh clone.

| Workflow | What the comparisons support |
| --- | --- |
| Restriction | Recognition sites matched in 200/200 cases. The 13 supported native physical digests matched on both strands and every end. Physical output was unavailable for 187. |
| PCR | Exact product counts and sequences matched the reference on 16/16 distributed plasmid cases, all three illustrated pairs recovered their known positive product, and six calculated melting temperatures matched Primer3 to the displayed precision. |
| Alignment | Two reference programs reproduced all six pairwise scores. Biotite matched six tracebacks and EMBOSS matched four. Multiple aligners recovered every input sequence and differed in gap placement. |
| Assembly | Three GenBank readers verified the packaged reporter and its saved copy. J01 generated-product parity remains open. |

## How to read a result

An **exact match** requires equality of every listed discrete output on all named inputs. One incorrect end, strand, base, coordinate or product count fails that case. **Model agreement** requires the stated numerical tolerance under the same model and units. **Concordance** reports agreement between heuristics that can yield different results. **Open** means the claim has no qualifying result. A program used inside strandfolio documents implementation provenance. Running the same implementation again checks integration but does not count as an independent algorithm.

Compare immutable input bytes and topology. Normalize indices and strand orientation once before comparing. Preserve duplicate products, empty completed results and explicit failures. Report every attempted case, including disagreements, alongside versions, parameters and rerun instructions. These are computational checks. No measured reaction temperature, yield or observed gel intensity was used as a target.

## Input manifest

The PCR comparison uses the four distributed GenBank source records, including the original pUC19 deposit M77789.2. The labeled pUC19 gallery map has the same bases and a different file hash. The alignment comparison uses the three records with a deposited replication-origin feature. The two Dryad records annotate the same 683-base ColE1 sequence. The SB2CL map supplies a different origin on the reverse strand. Whole-record comparisons start at each file’s stored origin and orientation. Digestion and Assembly have separate fixtures. [Source files and SHA-256 manifest](../databases/plasmids/README.md)

| Record | Whole record (bp) | Selected replication origin | File SHA-256 prefix |
| --- | ---: | --- | --- |
| pBH4-His-SUMO-GFP-LPETGG | 6,759 | ColE1, 683 nt, forward | `3e5b54ec7c64` |
| pBH4-MBP-TeCH | 6,921 | ColE1, 683 nt, forward | `2e8ab75fd0df` |
| pIAKA1s271_11_L30_01 | 3,965 | pSC101, 951 nt, reverse | `0356b8189412` |
| pUC19 (M77789.2) | 2,686 | PCR only | `14b0b5ba06c7` |

## Restriction digestion and physical ends

The application used restriction engine v2 and its sealed Biopython 1.88 / REBASE 404 snapshot. EMBOSS 6.6.0.0 used the publisher's separate REBASE 611 format 37 definitions dated 29 October 2026, SHA-256 `1253823900d5d7f27383f9f53edaa4330a10a8df4c2bd484791b257cbb57d4a2`. This file has 1,070 definition rows for 1,068 names. The two catalogues share upstream REBASE ancestry. The local evidence includes the original 611 files and their checksums under `rebase611/`. [REBASE file description](https://rebase.neb.com/rebase/rebase.f37.html)

Compare exact recognition and cleavage events separately. Normalize strand cuts to boundaries between bases on the stored forward reference, starting at zero. Compare one physical 5′→3′ string for **each strand of each fragment**, its left and right end type, the exposed strand and the exact exposed bases. Blunt ends require zero exposed bases. Preserve physically distinct fragments with identical lengths.

For a complete digest of a fully paired source, reconstruct each original strand separately, in its direction, and verify that each source position occurs exactly once on that strand. Circular checks may rotate at a cut while retaining direction. The forward and reverse physical strand totals each equal the input length. A staggered fragment's aligned display span includes unpaired bases. Adding such display spans across fragments can double-count positions from opposite strands. A gel-band size alone cannot validate a sticky end.

**Hand-checked physical controls.** An additional eight short linear and circular duplex fixtures cover a unique EcoRI 5′ extension, a KpnI 3′ extension, a blunt EcoRV cut, and three-enzyme digests. These eight controls are outside the requested 200-case count. Across them, 12 breaks produced 16 fragments. All 32 physical strand sequences and all 32 termini, including the protruding strand, exact protruding bases and polarity, agreed. Both original strands reconstructed in every case. EMBOSS `restrict` directly reports strand cut coordinates and forward fragment lengths. Its strand sequences and ends in this comparison were reconstructed from those cuts. pydna directly exposes both physical strands and ends. Its restriction search uses Biopython enzyme data, which share catalogue ancestry with strandfolio. [EMBOSS restrict](https://emboss.sourceforge.net/apps/release/6.6/emboss/apps/restrict.html) · [pydna duplex API](https://pydna-group.github.io/pydna/modules/pydna_dseq.html)

### 200-enzyme panel

**Recognition sites matched in all 200 cases. Native physical digests matched in all 13 cases the application completed. The other 187 returned unsupported and produced no native fragments.** The two external references supplied expected cuts and physical products for every case. The native model therefore has 13/200 demonstrated end-to-end coverage on this sample.

**Inputs and sampling.** The frozen input file contains R001–R200, their exact base strings, topology, enzyme names and hashes. Short unchanged windows (144–352 bp) came from a reproducible 262,144-base unannotated corpus. The panel reserved the 13 qualifying native enzyme definitions, then sampled distinct recognition/cut geometries from 513 eligible names under the stated strata. The 200 primary names represent 184 different recognition sequences and 200 distinct recognition/cut geometries. By primary enzyme, 89 produce 5′ overhangs, 66 produce 3′ overhangs and 45 produce blunt ends. A recognition sequence shared by different enzymes was counted once in the 184. This stratified sample is enriched for comparable known definitions. It cannot estimate performance across the complete catalogue. The maintainer fixture `tests/fixtures/restriction/panel_inputs.json` records the exact inputs and eligibility rules.

| Input topology | One selected enzyme | Two selected enzymes | Cases |
| --- | ---: | ---: | ---: |
| Linear duplex | 75 | 25 | 100 |
| Circular duplex | 75 | 25 | 100 |
| **Total** | **150** | **50** | **200** |

Every selected enzyme cut in its assigned case, including both enzymes in all 50 combinations. There were 122 one-break cases and 78 multiple-break cases. Twenty-eight single-enzyme cases contained multiple breaks. Forty-six primary geometries cut outside an asymmetric recognition motif, and 44 observed motif occurrences were in reverse orientation. The sampled sites and breaks were interior to the chosen window. The separate hand-checked circular controls exercise wrapping across the stored origin.

| Output compared | Native vs reference | External cross-check |
| --- | --- | --- |
| Recognition site, identity, position and orientation | Exact in 200/200 vs EMBOSS, 329 occurrences | EMBOSS independently searched the input with REBASE 611. |
| Top and bottom boundaries and signed stagger | 19 breaks matched both programs across 13 completed digests. | EMBOSS and pydna agreed on all 329 breaks in 200/200 cases. |
| Complete physical products and sizes | 25/25 native fragments matched both references, preserving each strand’s length and full 5′→3′ sequence. | 429/429 external fragments agreed. EMBOSS forward lengths were read directly and its physical sequences were reconstructed from its cuts. |
| Left/right end type, protruding strand, exact exposed bases | All 50 native termini agreed. | All 858 reference termini agreed, comprising 342 blunt, 278 with a 5′ overhang and 238 with a 3′ overhang. |
| Both strands reconstructed and original coordinates conserved | All 50 physical strands agreed. Each native source base occurred exactly once on its strand in every completed digest. | All 858 reference strands agreed. Both original strands were recovered in 200/200 cases. |
| Physical digest unavailable | 187/200 native cases returned unsupported. No physical sequence or end was scored as a native match for these cases. | All 187 have independently computed reference outputs in the fixture. |

The 13 native definitions were ApoI, BamHI, BsaI, EcoRI, EcoRV, HindIII, KpnI, MfeI, MspI, SacI, SmaI, XhoI and XmaI. The full local benchmark recorded zero tool errors and zero mismatches among comparable outputs. A second execution produced the same report byte for byte (SHA-256 `f47756c398147458fa87b3ef4c99408887201aa4b3aa7d7ac7ebeebb5c1c158f`). The frozen expectation file stores the reference site and break coordinates, complete-strand lengths and hashes, and both end descriptions for every case, including the 187 beyond the native physical model. Physical comparisons during the external run used complete sequences. The maintainer fixture is `tests/fixtures/restriction/panel_expected.json`.

**Worked conservation check.** R001 is a 352 bp linear duplex with ApoI and MspI. Both programs reported the three forward/reverse cut boundaries (43, 47), (140, 144) and (252, 254). The four physical forward-strand lengths were 43, 97, 112 and 100. The reverse-strand lengths, listed by the same fragments, were 47, 97, 110 and 98. Each strand totals 352 bases. The first fragment has a blunt left end and a right 5′ overhang on the reverse strand. The recorded right overhang string, read 5′→3′, is AATT. An implementation can preserve both length totals and still place an overhang on the wrong strand, so the regression also checks bases and polarity.

**Regression rule.** Normal test discovery checks all 200 native scans against the saved EMBOSS observations. For each completed digest it checks native cuts, source-coordinate conservation and physical products against the frozen pydna results. A previously qualified case becoming unsupported fails. A newly supported case must satisfy the same reference outputs. Seven deliberate faults were rejected, including a one-base shift, altered sticky-end bases, wrong end polarity, switched protruding strand, altered physical strand sequence, loss of a supported digest and fragments attached to an unsupported result. Terminal phosphate/hydroxyl chemistry, ligation efficiency, methylation and incomplete digestion were outside the external measurements. The panel excludes overlapping breaks. It tests the base-level duplex geometry of an ideal complete digest.

## PCR and melting temperatures

**Product comparison.** strandfolio and pydna 5.5.15 evaluated the three illustrated Insert_demo pairs and a standard M13 sequencing pair on each map. Both searches required exact, full-length matches of the two distinct primers on one circular template. The comparison preserved product count, sequence in the entered forward orientation and the strand on which each primer was found.

| Primer pair | Cases | Simulated products | Agreement with pydna |
| --- | ---: | --- | --- |
| Three illustrated pairs on each map | 12 | Zero products in each case | 12/12 |
| M13 pair on each map | 4 | One product on pUC19, zero on the other three | 4/4 |

**Independent site comparison.** SeqKit 2.14.0 reproduced every footprint across the 16 cases, including both sites on pUC19, one on each strand. The positive product lies within the stored linear coordinates. SeqKit’s linear amplicon comparison therefore covered all 16 cases and agreed in each. [SeqKit command definitions](https://bioinf.shenwei.me/seqkit/usage/)

**In silico positive controls.** On the fixed linear 1,800 bp Insert_demo template, each of the three illustrated pairs gave exactly one forward 912 bp product at positions 295–1,206 inclusive. All nine program/pair results from strandfolio, pydna and SeqKit matched the separately stored Insert_PCR sequence in full. The common product SHA-256 is `8a22231d7aa6ca714b0f2cdd812b2aadca5db4f6f963a9631dd272d7943446b2`. These three cases are additional to the 16 plasmid cases.

**Thermal comparison.** For the six illustrated DNA primers, the offline fixture records Biopython 1.88 `Tm_NN` reference values with the DNA_NN3 table and salt correction 5. A second implementation, libprimer3 2.6.1 called through primer3-py 2.4.0, used the corresponding SantaLucia model and salt correction. Both represent perfect, non-self-complementary duplexes. The inputs were 25 nM of each strand (50 nM total), 50 mM sodium, and zero potassium, Tris, magnesium and dNTPs. The pass tolerance was 0.10 °C. The largest direct library difference was 1.71 × 10⁻¹³ °C. The maximum difference from the reference rounded to eight decimals was 4.17 × 10⁻⁹ °C. [Fixed inputs and parameters](../strandfolio/data/examples/insert_demo_tm.json) · [Biopython model](https://biopython.org/docs/1.88/api/Bio.SeqUtils.MeltingTemp.html) · [Primer3 parameters](https://primer3.org/manual.html)

| Pair | Forward Tm (°C) | Reverse Tm (°C) |
| --- | ---: | ---: |
| 1 | 52.8 | 50.1 |
| 2 | 53.9 | 51.5 |
| 3 | 51.5 | 47.3 |

These comparisons reproduce the fixed model temperatures and the exact predictions for the tested inputs. Measured duplex melting, the temperature programmed into a cycler and the outcome of a physical assay are separate observations. Candidate generation, site-specific mismatched duplex temperatures, mismatched products, organism-wide specificity and physical reaction yield remain outside these results. Retained zero products are correct outputs under the tested search rules. [Desktop scope](sequence_workspace.md#desktop-support) · [pydna amplification](https://pydna-group.github.io/pydna/modules/pydna_amplify.html)

### Primer structure checks

The editor’s selected-site diagram compares the entered primer with an explicitly picked interval. Match and mismatch marks compare corresponding bases in equal-length binding sections. The unpaired 5′ end appears ahead of those columns. When the lengths differ the editor reports both lengths. The diagram itself makes no prediction of binding or amplification. [Selected-site view](sequence_workspace.md#simulate-pcr-with-existing-primers)

The draft review estimates each supplied annealing segment’s Tm against its ideal DNA complement. A declared 5′ tail is excluded. For 2–60 annealing bases it uses Biopython 1.88 `Tm_NN`, DNA_NN3 and salt correction 5, with 50 mM sodium and zero other salts or additives. Concentrations are 25 nM of each distinct strand, or 50 nM total for a self-complementary strand. For 61–80 annealing bases, the labeled approximation uses the long-sequence GC, length and salt model from primer3-py 2.4.0 at 50 mM monovalent salt. Temperatures outside these ranges appear as unavailable. A pair needs two available estimates from the same model to show an absolute Tm difference. The estimates describe idealized oligos under fixed conditions. [Biopython model](https://biopython.org/docs/1.88/api/Bio.SeqUtils.MeltingTemp.html) · [Primer3-py model](https://libnano.github.io/primer3-py/api/bindings.html)

The native report uses primer3-py 2.4.0 for five checks on the supplied complete oligos. The forward and reverse inputs each get a hairpin check (one molecule folding) and a self-dimer check (two copies of the same oligo). The pair gets a heterodimer check. Both inputs are read in their own 5′→3′ direction, with each declared tail included once. The single-template binding search uses only each declared annealing segment. The result identifies the saved revisions and shows the sequences actually assessed. [Primer3-py analysis API](https://libnano.github.io/primer3-py/api/bindings.html) · [Desktop walkthrough](sequence_workspace.md#simulate-pcr-with-existing-primers)

The adapter reads the library’s structure-found flag and its calculated melting temperature. The fixed reference conditions appear in the report. Each input is limited to 60 nt for structure analysis. If only one oligo is eligible, its own checks still run. An unsupported model input, missing engine or failed calculation yields **Unassessed** for the affected checks. The report shows GC percentage and the longest single-base run as composition descriptors. It applies no failure threshold to those descriptors or to a structure prediction. [Primer3 release and bundled changes](https://libnano.github.io/primer3-py/included_primer3_modifications.html)

**Verification.** The existing linear PCR fixture supplied an untailed pair and the same pair with its declared tails. All ten structure temperatures matched the separately invoked, matched-build `ntthal` executable within 0.000001 °C. This comparison shares the thermodynamic model. A uniform-A control exercised negative structure results. Moving a declared boundary without changing either full oligo preserved all five outputs. Other checks cover unsupported lengths and modifications, unavailable engines, template-free entry, background completion after edits and report reopening from captured inputs. A stopped search can still report modeled primer structures while withholding site and product counts.

The BOC Sciences article describes sequence-dependent structures alongside degradation, synthesis impurities and reaction conditions. Those causes have different evidence requirements. A predicted hairpin or dimer identifies a possible competing interaction. It does not establish that the structure formed or caused an assay to fail. The report cannot measure purity, integrity, contamination, target abundance or yield. Measurements and controls are needed to attribute an observed failure. [BOC Sciences review](https://rna.bocsci.com/support/why-pcr-primers-fail-common-oligo-issues.html) · [Experimental primer-dimer study](https://pubmed.ncbi.nlm.nih.gov/9241236/)

### PCR libraries and commercial software

| Software | Capability and evidence in this audit |
| --- | --- |
| Primer3 and primer3-py | Primer3 can rank candidates and calculate thermodynamic properties. The Python wrapper includes a patched build of libprimer3. Strandfolio uses its five structure checks on supplied oligos. The six illustrated primer–template melting temperatures have a separate cross-check. Candidate generation remains planned. [Primer3 manual](https://primer3.org/manual.html) · [Wrapper changes](https://libnano.github.io/primer3-py/included_primer3_modifications.html) |
| Biopython 1.88 | The nearest-neighbor module calculates duplex melting temperatures for a supplied duplex under explicit assumptions. Strandfolio uses the nearest-neighbor model for its short, ideal-complement temperature estimates. Six packaged values serve as numerical controls. That API does not discover folding structures or binding sites. [MeltingTemp](https://biopython.org/docs/1.88/api/Bio.SeqUtils.MeltingTemp.html) |
| pydna 5.5.15 and SeqKit 2.14.0 | The recorded pydna comparison used predicted products and primer footprints. SeqKit independently compared local sites, and its linear amplicon extractor was applicable to the one positive on the four circular maps. The 16 cases contained two matching footprints and neither crossed the origin. Separate native fixtures cover circular boundaries and multi-product enumeration. [pydna source](https://github.com/pydna-group/pydna/blob/v5.5.15/src/pydna/amplify.py) · [SeqKit source](https://github.com/shenwei356/seqkit/blob/v2.14.0/seqkit/cmd/amplicon.go) |
| NCBI Primer-BLAST | Provides design and specificity searches against a selected reference database. Coverage depends on its inputs and settings. It was outside the local exact-PCR and temperature comparisons. [NCBI tool and scope](https://www.ncbi.nlm.nih.gov/tools/primer-blast/) |
| SnapGene | Documents primer orientation, annealed length, multiple binding sites and unbound extensions. Users can simulate a saved pair, obtain a distinct product record and inspect its History. [Primer tooltips](https://support.snapgene.com/hc/en-us/articles/41148400224788-View-Primer-Tooltips) · [PCR](https://support.snapgene.com/hc/en-us/articles/10384147948436-PCR) |
| Benchling | Documents reusable attached oligos, oriented sites and designated overhangs. A linked pair shows an expected size. A derived product can be saved separately. The Primer Wizard generates candidates. [Primer records](https://help.benchling.com/hc/en-us/articles/39072057302541-Use-primers) · [Simulated PCR](https://help.benchling.com/hc/en-us/articles/9684267245325-Run-PCR-in-silico) |
| Geneious Prime | Documents oligo records, site annotations and 5′ extensions. The binding interval and the extracted product have different extents when an extension is present. Testing saved primers is distinct from Primer3 candidate design. [Primer manual](https://manual.geneious.com/en/latest/Primers.html) |

The three commercial comparisons describe their vendors’ published workflows. No independent runs of those applications are included here.

### What the PCR result establishes

The desktop models a single pair of supplied DNA primers on one stored DNA template. A completed count of zero means that the search found zero products under its recorded rules and size range. A capped or stopped search has no final count. A database specificity search has a different search space, defined by the reference, version and detection rules. An observed band can provide evidence of approximate size. Confirming product identity requires additional evidence, such as sequencing. [Primer-BLAST methods and scope](https://doi.org/10.1186/1471-2105-13-134) · [MIQE précis on assay validation](https://doi.org/10.1186/1471-2199-11-74)

| Assay term | Evidence the term describes |
| --- | --- |
| Endpoint PCR | A physical assay examines products after amplification. Strandfolio predicts product sequences and sizes. The illustrative gel has no measured intensity, yield or initial quantity. [Endpoint plateau study](https://doi.org/10.1016/0167-4781(94)90076-0) |
| Reverse-transcription PCR | An RNA input is copied to cDNA before PCR. A DNA or cDNA sequence file alone supplies no evidence that reverse transcription took place. [Endpoint RT-PCR study](https://pubmed.ncbi.nlm.nih.gov/2541142/) |
| Real-time PCR and RT-qPCR | Fluorescence is measured across cycles. A Cq, a concentration estimate and a qualitative assay call have different evidence requirements. Strandfolio has no fluorescence measurements or calibration data. [MIQE 2.0](https://pubmed.ncbi.nlm.nih.gov/40272429/) |
| Multiplex PCR | Multiple targets are assayed concurrently in one reaction. Several possible products of one supplied pair are still results from a single-pair search. [Original multiplex assay](https://pubmed.ncbi.nlm.nih.gov/3205741/) |

An oligo’s modeled melting midpoint depends on the duplex, concentration and model conditions. A cycler’s annealing temperature is an experimental setting. The six-temperature reference above measures agreement between software implementations. [SantaLucia thermodynamic model](https://doi.org/10.1073/pnas.95.4.1460) · [Experimental study of annealing temperature](https://pubmed.ncbi.nlm.nih.gov/2243783/)

### Primer variations and their evidence

| Variation | What the current DNA model can represent |
| --- | --- |
| User-declared restriction-site extension | A supplied DNA prefix and its contribution to a predicted product are retained. A user’s intended site, a recognition match on a particular revision, a simulated digest and observed cleavage are separate claims. The primer has no field for a labeled interval within its tail. [Benchling primer representation](https://docs.benchling.com/docs/api/benchling-api/dna-sequence/primers-list) · [Native digest comparison](#restriction-digestion-and-physical-ends) |
| A run of A bases | A user can place an existing DNA poly(dA) tract in the declared tail or the annealing portion. The app uses the declared boundary. An RNA poly(A) tail belongs to an RNA molecule. Oligo(dT) denotes a DNA tract containing thymidines and can feature in reverse transcription. A run of letters alone supplies no assay history. [IUPAC–IUBMB nomenclature](https://iupac.qmul.ac.uk/misc/naabb.html) · [Oligo(dT) and reverse transcription](https://pubmed.ncbi.nlm.nih.gov/11972056/) |
| Mixed bases, chemical modifications and probes | An ambiguity symbol represents a set of possible bases. A chemical modification describes chemistry, potentially at a particular position. A probe’s role in detecting signal is separate from the primer roles in the simulation. Exact PCR accepts unambiguous DNA. A recorded primer with declared modifications yields an unsupported attempt. [IUBMB ambiguity nomenclature](https://iubmb.qmul.ac.uk/misc/naseq.html) · [MIQE reporting guidance](https://pubmed.ncbi.nlm.nih.gov/40272429/) |

### How suppliers describe primer input

Public documentation reviewed on 19 November 2026. Vendors describe the complete individual oligo as the synthesis item. Strandfolio’s annealing and tail fields describe roles within one primer. A vendor’s metadata, syntax and product options belong to its particular form. This review used published materials and involved no submitted orders.

| Supplier and product reviewed | Documented input and scope |
| --- | --- |
| IDT, custom DNA oligos in tubes | Single entry includes an item name and sequence in 5′→3′ order. Multiple entry and spreadsheet or text entry are available. Scale and purification have dedicated fields. Modification controls generate vendor-specific notation. [Oligo Entry](https://www.idtdna.com/site/order/oligoentry) · [Modification fields](https://www.idtdna.com/pages/support/faqs/how-do-i-order-an-oligo-with-a-modification-) |
| Thermo Fisher / Invitrogen, US standard DNA oligos in tubes | Basic Entry and Bulk Upload are documented. The official form guide labels the name, researcher, sequence (5′→3′), synthesis scale, purification and formulation, with terminal modifications in separate controls. [Ordering modes](https://www.thermofisher.com/us/en/home/life-science/oligonucleotides-primers-probes-genes/custom-dna-oligos/oligo-ordering-delivery.html) · [Vendor form guide](https://documents.thermofisher.com/TFS-Assets/BID/Flyers/custom-dna-oligo-online-ordering-guide-flyer.pdf) |
| Eurofins Genomics, US tubes and plates | Tubes accept name and sequence through individual entry, paste or upload. Plate records also identify the plate and well. The US plate guide explicitly labels the sequence 5′→3′. The public standard tube help does not specify direction. [Tube form](https://eurofinsgenomics.com/en/orderpages/oligos/custom-dna-oligos-in-tubes-order-page/) · [Plate guide](https://eurofinsgenomics.com/en/products/dnarna-synthesis/oligo-plate-email-guide/) |
| Merck / Sigma-Aldrich, standard DNA oligos | The India guide documents single name/sequence entry and batch paste or template fields including name, sequence, scale and notes. The public US standard tube configurator reports that it is retired. Current US template compatibility and direction in the standard form are unverified. [India guide](https://www.sigmaaldrich.com/IN/en/life-science/ecommerce/how-to-register-on-sigmaaldrich) · [US configurator](https://www.sigmaaldrich.com/US/en/configurators/tube?product=standard) |
| GENEWIZ / Azenta, US Overnight Custom DNA Oligos | The published service accepts researcher-supplied sequences and describes standard unmodified, desalted oligos, scale and delivery options. The ordering portal requires an account. Exact field labels, direction and bulk-file formats remain unverified. [Product description](https://www.genewiz.com/public/services/oligo/overnight-dna-oligos) |

### Live comparison on the EGFP teaching control

On 20 November 2026, the live Sequence Manipulation Suite **PCR Products** form and Strandfolio each reported one product for the published LMU P1/P9 nucleotide pair on circular pEGFP-N1 (GenBank U55762.1, 4,733 bp). Both returned bases 672–971 inclusive, 300 bp. Strandfolio located P1 at 672→691 and P9 at 971→952. Both uppercase product sequences matched the GenBank slice with SHA-256 `a13faf2f4ce3e484f945b4559856d675fd2c483735742db07dffb16b74706ae0`. To replay, use U55762.1 FASTA, the P1/P9 nucleotide strings printed in §1.4, and circular topology. The published assay used Cy5 labels. This comparison checks nucleotide orientation, coordinates and sequence. [Live SMS2 form](https://www.bioinformatics.org/sms2/pcr_products.html) · [LMU manual, §§1.4–1.5](https://cms-cdn.lmu.de/media/17-physik/17-softmatter/downloads/r1b_pcr_fcs_instructions_english.pdf) · [NCBI U55762.1](https://www.ncbi.nlm.nih.gov/nuccore/U55762.1)

On the same record, Strandfolio also reproduced the published 712 bp P1/P13 result. For P1/P5, the manual lists 101 bp. Its printed bases on U55762.1 give 100 bp in Strandfolio. The one-base discrepancy remains open.

## Alignment

**Pairwise DNA.** Compare the three origin pairs in global and local modes, scoring a match +2, a mismatch −1, the first gap base −4 and each additional gap base −1. Global mode penalizes terminal gaps. Biotite 2.0.0 reproduced the Biopython 1.88 optimal scores, source spans and tracebacks on all six comparisons. EMBOSS 6.6.0.0 reproduced all six optimal scores and four tracebacks. In the two global comparisons between origin families it returned a different equally scoring path. [Biotite API](https://www.biotite-python.org/latest/apidoc/biotite.sequence.align.align_optimal.html) · [EMBOSS needle](https://emboss.sourceforge.net/apps/release/6.6/emboss/apps/needle.html) · [EMBOSS water](https://emboss.sourceforge.net/apps/release/6.6/emboss/apps/water.html)

**Shared-column DNA.** Kalign 4.0.0 (fast preset), MAFFT 7.526 (nucleotide FFT-NS-2) and Clustal Omega 1.2.4 (DNA mode, input order, 128 MB MAC parameter) each used one thread. Every ungapped row recovered its input. Biopython parsed strandfolio’s aligned FASTA and recovered every row with the same gap positions. In strandfolio’s result matrix, every cell mapped to its expected source coordinate or gap. [Kalign distribution](https://pypi.org/project/kalign-python/4.0.0/) · [MAFFT manual](https://mafft.cbrc.jp/alignment/software/manual/manual.html) · [Clustal Omega manual](https://www.theclustal.com/omega/README/)

| Inputs | Comparator | Columns, Kalign / comparator | Identical source columns | Shared residue pairs | Fraction shared, Kalign / comparator |
| --- | --- | ---: | ---: | ---: | ---: |
| Annotated origins | MAFFT | 953 / 1,012 | 558 | 1,415 | 69.19% / 73.43% |
| Annotated origins | Clustal Omega | 953 / 968 | 83 | 683 | 33.40% / 33.90% |
| Whole maps | MAFFT | 9,223 / 9,613 | 4,467 | 5,513 | 50.58% / 49.56% |
| Whole maps | Clustal Omega | 9,223 / 10,907 | 3,429 | 4,468 | 40.99% / 59.77% |

A shared residue pair aligns the same two original positions. An identical column matches the positions or gaps across all three rows. The percentages use each engine’s own total as denominator. The differences show how strongly a whole-map or mixed-origin comparison can depend on gap placement. RNA and protein have separate regression fixtures.

## Assembly and History

**Exact match for the named copy.** The source was the bundled 4,500 bp circular reporter. The app opened its Assembly example, saved a named copy, reopened the revision through a fresh project connection, and exported GenBank. Biopython 1.88, Biotite 2.0.0 and gb-io 0.4.0 each read the original GenBank file and the exported copy. All six reads recovered the same 4,500 bases, circular topology and six feature types, labels, intervals and strands. Both same-named promoter intervals and the reverse feature were retained. The full-sequence SHA-256 is `2e5a06bc00eea6b98c64e9ab5f857f503635b6dec316a9a75e096eab95686271`. The source file stayed byte-identical. The named export had a different file hash. Biopython supplies the app’s I/O backend. Biotite and the Rust gb-io library provide independent GenBank parsers. [Packaged reporter](../strandfolio/data/examples/pReporter_demo.gb) · [Biotite GenBank reader](https://www.biotite-python.org/latest/apidoc/biotite.sequence.io.genbank.html)

The comparison converted each reader’s output to one-based, end-inclusive coordinates. The reverse strand and duplicate labels remained explicit. [gb-io parser](https://pypi.org/project/gb-io/0.4.0/)

The saved revision retained the source credits supplied in the example file. Assembly currently lets users choose and order inputs. Creating a new product from those inputs remains unimplemented, so the independent comparison of a generated product and its ancestry (J01) is open. [Save the illustrated product](sequence_workspace.md#save-the-illustrated-product)

### Partial complementarity in assembly software

A terminal extension leaves bases on one strand without an opposing base. A mismatch has bases on both strands at the same position. A missing strand or end description leaves the local geometry unknown. Restriction cleavage can leave blunt ends or a 5′ or 3′ extension. Ligation joins the DNA backbone. A record of cleavage documents the input ends, while evidence for a subsequent ligation belongs to that separate event. [NEB overview of restriction ends](https://www.neb.com/en-us/tools-and-resources/video-library/cloning-with-restriction-enzymes) · [Mertz and Davis, 1972](https://pubmed.ncbi.nlm.nih.gov/4343968/)

| Software and evidence reviewed | What the software reports |
| --- | --- |
| pydna 5.5.15, duplex representation | The two-strand constructor supports offset strand ends. Its text display can therefore show an unpaired terminal extension. The constructor raises an error for an unsupported opposing pair inside the overlap. This finding applies to the reviewed constructor and alphabet. [Constructor](https://github.com/pydna-group/pydna/blob/8620f5604472136440342f5cafbf5c70120421c4/src/pydna/dseq.py#L540-L563) · [Alphabet](https://github.com/pydna-group/pydna/blob/8620f5604472136440342f5cafbf5c70120421c4/src/pydna/alphabet.py#L210-L223) · [Display tests](https://github.com/pydna-group/pydna/blob/8620f5604472136440342f5cafbf5c70120421c4/tests/test_module_dseq.py#L838-L862) |
| OpenCloning, plan schema and display | Each entry identifies its source, orientation and optional left and right overlap locations. A location may cover part of a fragment. An absent location is displayed blank. The plan records no base-by-base mismatch calls or experimental pairing verdict. [Fragment schema](https://github.com/OpenCloning/OpenCloning_LinkML/blob/ab48f8fb5986a0ffee64f6c17699ae379b83e439/src/opencloning_linkml/schema/opencloning_linkml.yaml#L517-L540) · [Plan display](https://github.com/OpenCloning/OpenCloning/blob/c1b49391c332d48c1949934022138cc2ca93bc53/js/packages/ui/src/components/sources/AssemblyPlanDisplayer.jsx#L3-L25) |
| DNA Cauldron, simulation reports | Methods can supply errors and warnings with a description, a suggestion and optional supporting data. The writer creates a warnings file only when warnings are present. An empty warning list supplies no site-specific evidence about partial pairing. [Diagnostic record](https://github.com/Edinburgh-Genome-Foundry/DnaCauldron/blob/04c46105472723dc1d60f38ea0628916df3ac974/dnacauldron/Assembly/AssemblyFlaw.py#L26-L37) · [Report writer](https://github.com/Edinburgh-Genome-Foundry/DnaCauldron/blob/04c46105472723dc1d60f38ea0628916df3ac974/dnacauldron/Assembly/AssemblyReportWriter/AssemblyReportWriter.py#L121-L137) |
| SnapGene, vendor documentation | **Anneal Oligos** allows terminal extensions and documents exact matching within the detected overlap. **Linear Ligation** separately reports incompatible ends in the interface. These are documented behaviors for two specific tools. [Annealing behavior](https://support.snapgene.com/hc/en-us/articles/10384352624148-Anneal-Oligonucleotide-Pairs) · [Ligation interface](https://support.snapgene.com/hc/en-us/articles/10384064604564-Linear-Ligation) |

These findings come from source and documentation review. In strandfolio, **Planned junctions** shows evidence attached to each individual input, including any recorded strand spans. A feature’s strand describes its location on its source record. The plan orientation belongs to a particular use of that record, so two occurrences can have different orientations. Pairing across the two inputs is unassessed. Experimental and cross-engine qualification of partially complementary junctions remain open. [INSDC feature locations](https://www.insdc.org/submitting-standards/feature-table/) · [Use the native inspector](sequence_workspace.md#choose-assembly-inputs)

## Coverage of the remaining workspace

| Workflow | Existing release evidence | Boundary of this cross-software panel |
| --- | --- | --- |
| Sequence import, project save, exchange and History | Captured file bytes, revision IDs, multi-record ordinals and parent lookups are checked on reopen. The Assembly fixed copy above adds an independent annotation round trip. | Project-specific history and styling have no direct GenBank equivalent. Exchange in the SnapGene desktop application is an open qualification. [Import support](importing_sequences.md#exchange-and-project-support) · [Recorded ancestry](sequence_workspace.md#inspect-recorded-ancestry) |
| Enzyme catalogue and gel | Offline rebuild, content hashes, calibrated ladder sizes, keyed products and co-migrating band contributors are checked. | Catalogue membership and band sizes alone cannot establish exact cleavage, strand ends, digestion efficiency or experimental intensity. [Catalogue](../databases/enzymes/README.md) · [Gel scope](../databases/ladders/README.md#supported-size-previews) |
| Annotation, conversion, map and figure display | Original-coordinate navigation, sequence conversion, genetic codes, annotation edits and figure exports have dedicated desktop and fixture tests. | The plasmid comparisons do not score map aesthetics, accessibility, annotation confidence or publication layout against another editor. [Conversion controls](sequence_workspace.md#copy-a-selection-and-change-its-genetic-code) · [Figure export](sequence_workspace.md#export-a-figure) |

## Reproducing the results

The following replay commands require the maintainer’s local `dev/` and `tests/` directories and the recorded input files. For checks available in a fresh clone, see the [notebook register](notebooks/README.md). Set `REPORTS` to an existing empty directory for the resulting files. The app environment supplies Biopython and Kalign. An independent interpreter supplies pydna 5.5.15, primer3-py 2.4.0, Biotite 2.0.0 and gb-io 0.4.0. MAFFT 7.526 runs locally. No sequence is uploaded. Set `ORACLE_PYTHON` to that interpreter's executable, `MAFFT` to the MAFFT executable and `CLUSTALO` to Clustal Omega 1.2.4. Set `EMBOSS_BIN` to the EMBOSS 6.6.0.0 executables directory, `REBASE_611_ENZ` to the verified reference `embossre.enz` and `SEQKIT` to SeqKit 2.14.0. The 200-case restriction replay also uses the sealed local catalogue. [Catalogue setup](../databases/enzymes/README.md#rebuild-and-verify)

```sh
.venv/bin/python -m dev.compare_pcr_references --oracle-python "$ORACLE_PYTHON" > "$REPORTS/pcr.json"
.venv/bin/python -m dev.compare_pcr_references --oracle-python "$ORACLE_PYTHON" --illustrated-seqkit "$SEQKIT" > "$REPORTS/illustrated.json"
.venv/bin/python -m dev.compare_pairwise_references --oracle-python "$ORACLE_PYTHON" --emboss-bin "$EMBOSS_BIN" > "$REPORTS/pairwise.json"
.venv/bin/python -m dev.compare_alignment_references --mafft "$MAFFT" --clustalo "$CLUSTALO" > "$REPORTS/multiple.json"
.venv/bin/python -m dev.compare_pcr_footprints "$SEQKIT" > "$REPORTS/footprints.json"
QT_QPA_PLATFORM=offscreen .venv/bin/python -m dev.compare_assembly_reference --oracle-python "$ORACLE_PYTHON" --output "$REPORTS/assembly.json"
.venv/bin/python -m dev.compare_restriction_panel --pydna-python "$ORACLE_PYTHON" --restrict "$EMBOSS_BIN/restrict" --rebase "$REBASE_611_ENZ" --output "$REPORTS/restriction.json"
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m dev.verify_project
```

The project verifier selects checks from the Git changes under review. A clean checkout with no review changes selects the full gate. The reports identify skipped checks and any missing optional prerequisites. Exercise native Cocoa interactions separately in a logged-in macOS session. [Native desktop acceptance](sequence_workspace.md#verification-and-native-desktop-acceptance)

The local PCR and alignment reports for the distributed inputs are in `docs/notebooks/_outputs/F06/20261119T174930126510Z_distributed_plasmids/`. The restriction, fixed PCR illustration and saved Assembly-copy reference results are retained in the earlier `docs/notebooks/_outputs/F06/20261119T124702467627Z_release/` run.
