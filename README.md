# Strandfolio

DNA cloning software for exploring annotated sequences in a desktop workspace. Select a feature on a plasmid map to see its bases, inspect a predicted gel band, or trace a PCR product back to its template and primers.

**[Download Strandfolio 0.1.7 for Mac (Apple Silicon, 62.7 MB)](https://github.com/luisub/strandfolio/raw/refs/heads/main/docs/releases/Strandfolio-0.1.7-macOS-arm64.dmg)**

Requires an Apple Silicon Mac running macOS 26.0 or later. If Strandfolio is running, quit it first. Open the DMG and drag Strandfolio into **Applications**. Choose **Replace** if Finder asks. Eject the disk image, then open **Applications → Strandfolio**. If macOS blocks the first launch, follow the [steps in Troubleshooting](#troubleshooting).

Version 0.1.7 reduces repeated work when refreshing the Digest enzyme chooser, opening recorded analyses in History, and reading portable digests. Existing projects and portable digests reopen with their recorded results. [Workspace guide](docs/sequence_workspace.md)

[Install and launch](#install-and-launch) · [Try pUC19](#try-puc19) · [Gallery](#workspace-gallery) · [Workspace guide](docs/sequence_workspace.md) · [PyPI release](https://pypi.org/project/strandfolio/0.1.0/)

![15-second Strandfolio tour showing pUC19 bases and map, region selection, restriction enzymes, PCR, electrophoresis and an example plasmid design](docs/images/strandfolio-tour.gif)

## Install and launch

For first-launch help with the Mac download, see the [Mac installation guide](docs/installation/README.md#install-the-mac-app). To install with Python 3.12 on Apple Silicon macOS, run these commands in Terminal. The last command opens the bundled reporter so you can explore immediately.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install strandfolio
strandfolio --example
```

To open your own sequence, run `strandfolio path/to/record.gb` or choose **Open…** in the app. For later sessions, activate the same environment first. [Installation and platform support](docs/installation/README.md) · [Source checkout](docs/installation/README.md#install-from-a-checkout) · [Conda](docs/installation/README.md#install-with-conda)

## Try pUC19

1. Save the annotated [pUC19 GenBank file](databases/plasmids/maps/pUC19.gb) and open it in Strandfolio.
2. In **Sequence**, select **MCS** in the feature list. The map highlights the multiple cloning site, and the sequence view shows its 57 bases at positions 233–289.
3. Choose **Expand** to inspect the circular map. Choose **Back to tracks** to return to the bases.

To preview a digest, choose an enzyme from **Add digest enzyme…** in the Sequence sidebar and click **Digest**. You can inspect the fragments immediately and use **Save digestion…** if you want to keep the result.

[![Strandfolio Sequence workspace showing the selected pUC19 multiple cloning site in the sequence tracks and circular map](docs/images/sequence-workspace.png)](docs/images/sequence-workspace.png)

## Workspace gallery

| Restriction digest | PCR |
| --- | --- |
| [![A selected pUC19 digest fragment highlighted on the labeled source map above the product details and gel](docs/images/digest.png)](docs/images/digest.png) | [![PCR workspace showing the predicted GFP amplicon](docs/images/pcr.png)](docs/images/pcr.png) |
| Inspect predicted fragments, their ends and their positions on the source sequence. [Digest guide](docs/sequence_workspace.md#digest-a-sequence-and-reopen-its-gel) | Test an existing primer pair, inspect the predicted amplicon, and review possible hairpins and primer dimers. [PCR guide](docs/sequence_workspace.md#simulate-pcr-with-existing-primers) |

| Alignment | Gel |
| --- | --- |
| [![Aligned plasmid regions showing mismatches at their original sequence coordinates](docs/images/alignment.png)](docs/images/alignment.png) | [![Virtual gel comparing predicted digest fragments and a PCR product alongside a size ladder](docs/images/gel.png)](docs/images/gel.png) |
| Compare DNA, RNA or protein sequences at their source coordinates. Align 3–9 sequences with Kalign and export aligned FASTA. [Alignment guide](docs/sequence_workspace.md#compare-open-sequences) | Compare predicted band sizes against a ladder and select a band to see its contributing fragments. [Gel previews and ladders](databases/ladders/README.md#supported-size-previews) |

| Assembly | History |
| --- | --- |
| [![Assembly workspace showing backbone and insert contributions in the bundled reporter](docs/images/assembly.png)](docs/images/assembly.png) | [![History view tracing a saved GFP amplicon back to its template and primers](docs/images/history.png)](docs/images/history.png) |
| Inspect junctions and source contributions in the bundled 4,500 bp reporter. Generating a new construct from your own assembly plan is in development. [Assembly guide](docs/sequence_workspace.md#choose-assembly-inputs) | Trace a saved product back to its inputs, reopen its source or export the recorded ancestry as JSON. [History guide](docs/sequence_workspace.md#inspect-recorded-ancestry) |

[More screenshots and example maps](docs/images/README.md)

## Examples and exports

The checkout includes four annotated plasmids and a linear bacteriophage λ example. Open pUC19 for cloning landmarks, the SB2CL GFP reporter for PCR, or the larger GFP expression plasmid to explore a crowded map. [Plasmid collection](databases/plasmids/README.md) · [Linear examples](databases/README.md#linear-reference-examples)

[![Exported circular pUC19 map with the multiple cloning site highlighted](docs/images/pUC19.svg)](docs/images/pUC19.svg)

Export maps, sequence tracks and gel previews as SVG or PNG. Save annotated DNA as GenBank or share sequence text as FASTA. [Figure export](docs/sequence_workspace.md#export-a-figure) · [Supported file formats](docs/importing_sequences.md#supported-inputs-and-fidelity)

## User guides

- [Workspace guide](docs/sequence_workspace.md) — open and save projects, work across tabs and inspect results.
- [Python notebooks](docs/notebooks/README.md) — reproduce sequence maps, explore ladder profiles and work with annotations in Python.
- [Scientific validation](docs/validation.md) — see the assumptions and reference comparisons behind each simulation.

## Contributing

For a bug report, include your operating system, the steps to reproduce it and what you expected to see. If a bundled example reproduces the issue, name the record and the selected feature. Contributions to the code, examples and documentation are welcome. [Open an issue](https://github.com/luisub/strandfolio/issues/new) · [Pull requests](https://github.com/luisub/strandfolio/pulls)

## License

Copyright © 2026 Luis Aguilera. Original Strandfolio code is available under the BSD 3-Clause License. Third-party libraries and datasets retain their own terms. [License](LICENSE) · [Data notices](docs/installation/DATA-NOTICES.md)

## Troubleshooting

### macOS blocks Strandfolio on first launch

1. In Finder, open **Applications** and double-click **Strandfolio**. If macOS says it can’t verify the app, dismiss the alert.
2. Open **System Settings → Privacy & Security** and scroll to **Security**. If you downloaded Strandfolio from this repository and trust the copy, click **Open Anyway** beside the message about Strandfolio.
3. Click **Open** in the confirmation dialog. On future launches, open Strandfolio normally from Applications. [Apple’s first-open guide](https://support.apple.com/en-us/102445)

![macOS Privacy & Security settings showing the Open Anyway button beside the message that Strandfolio was blocked](docs/images/mac-install.png)
