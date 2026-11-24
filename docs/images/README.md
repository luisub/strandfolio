# Workspace screenshots and example maps

The [15-second desktop tour](strandfolio-tour.gif) follows pUC19 through sequence selection, ORF review, an MspI digest, M13 PCR, amplicon alignment and gel comparison. It closes with the bundled reporter in Assembly. The still images below show individual views from the same source checkout.

## Rebuild the screenshots

Activate the project environment and run the native desktop capture from the repository root. The command writes eleven desktop screenshots and the HXB2 coordinate map to the chosen directory. Add `--reference-maps-only` to rebuild just the two λ screenshots and the HXB2 SVG. [Installation guide](../installation/README.md)

```sh
python -m docs.images.render_gallery docs/images
python -m strandfolio databases/plasmids/maps/pUC19.gb
```

The capture opens the distributed records through the desktop importer, works in a temporary project, and builds a temporary enzyme catalogue from the installed Biopython data. It renders at twice the logical window size using Qt's offscreen platform. System fonts and the app's new revision and run IDs can vary between captures. [Capture script](render_gallery.py)

| View | What produced the image |
| --- | --- |
| [Sequence](sequence-workspace.png) | The 2,686 bp pUC19 vector with the multiple cloning site selected at bases 233–289. The map, tracks and feature list share the selection. |
| [PCR](pcr.png) · [Product detail](pcr-product.png) | An exact-match simulation on the circular SB2CL GFP reporter yields one 717 bp product. The result dialog shows its source interval and modeled gel band. |
| [Alignment](alignment.png) · [Shared columns](shared-alignment.png) | The local alignment compares the SB2CL GFP coding sequence with the annotated fusion CDS in the Dryad GFP plasmid. The three-row Kalign view adds the saved GFP amplicon. |
| [Digest](digest.png) | The modeled MspI digest of pUC19 gives 13 fragments. The source map highlights the selected product’s recorded ranges. The generator checks the product lengths, including the two 34 bp products, against the released pUC19/MspI marker profile. |
| [Gel](gel.png) | The pUC19 digest and GFP amplicon appear alongside the ΦX174 RF/HaeIII ladder. The labeled log-scale plot shows predicted sizes within its display range. |
| [History](history.png) | The saved GFP amplicon links to its template and the two primers recorded by the PCR simulation. |
| [Assembly](assembly.png) | The bundled synthetic design displays the recorded source contributions and annotations for its existing 4,500 bp product. |

Notebook 02 exports both circular maps to the run directory it prints under `docs/notebooks/_outputs/V06/`. The pUC19 figure highlights the selected MCS. The larger Dryad map shows how overlapping features share tracks. [pUC19 map](pUC19.svg) · [Dryad GFP map](pBH4-His-SUMO-GFP-LPETGG.svg) · [Notebook 02](../notebooks/02_sequence_visualization.ipynb) · [Notebook setup and invocation](../notebooks/README.md#running-the-notebooks)

## Explore the linear λ genome

The λ gallery edition contains the source annotation, two genes and 66 coding sequences from J02459.1. The complete record retains all 188 annotations. Both files have the same 48,502 bases as the λ ladder reference. [Gallery edition](../../databases/references/lambda-landmarks.gb) · [Complete local reference](../../databases/references/J02459.1.gb) · [GenBank J02459.1](https://www.ncbi.nlm.nih.gov/nuccore/J02459.1) · [Reference checksum](../../databases/ladders/references.json)

To recreate the λ screenshots, open the gallery edition, use **Bacteriophage λ** as the display name and clear the restriction overlays. Keep **Layers → Map features → Landmarks** and select the **Z** coding sequence at 7,977–8,555 in the inspector. **Split** shows its bases beside the map. **Expand** opens the linear map at full width. [Split view](lambda-split.png) · [Expanded map](lambda-linear.png)

## Explore the HXB2 coordinate map

The desktop figure renderer draws nine gene spans using the Los Alamos HXB2 proviral numbering. For spliced genes the arrow covers the outer span, including the intervening region. The example contains 9,719 `N` placeholders. [Example record](../../databases/references/HXB2-coordinate-map.gb) · [Exported gene map](hxb2-gene-map.svg) · [Coordinate definitions](https://www.hiv.lanl.gov/components/sequence/HIV/search/help.html)

## Source records

The SB2CL reporter is from Vignoni and Boada's combinatorial library, release 1.0.2 (MIT). The GFP fusion plasmid is from the Dryad dataset by Weeks and colleagues (CC0). The pUC19 example uses all bases from GenBank M77789.2 with four named landmarks. The [label guide](../../databases/plasmids/README.md#puc19-map-labels) gives their sources and coordinates. The plasmid manifest identifies the distributed records used in these examples. [Plasmid manifest](../../databases/plasmids/references.json) · [Distribution terms](../../databases/plasmids/README.md#sources-and-distribution) · [SB2CL notice](../../databases/plasmids/maps/SB2CL-LICENSE.txt) · [Ladder profiles](../../databases/ladders/README.md)
