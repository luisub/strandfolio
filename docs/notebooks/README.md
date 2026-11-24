# Using strandfolio from Python

These five notebooks use the Python package in the checkout’s [strandfolio/](../../strandfolio/) directory. Start with the pUC19 examples to see the same reader, selection model and map renderer used by the desktop.

| Notebook | What you can do |
| --- | --- |
| [01 · Open and export a plasmid](01_import_export.ipynb) | Compare pUC19 with the M77789.2 deposit, export and reopen GenBank, FASTA and text files, and open a SnapGene record. |
| [02 · Map pUC19](02_sequence_visualization.ipynb) | Select the multiple cloning site, display three pUC19 views, draw the larger GFP plasmid and export all four SVGs. Compare translations under three genetic codes. |
| [03 · Explore the enzyme catalogue](03_enzyme_catalogue.ipynb) | Look up EcoRI, search by name or motif and inspect uncertain cleavage data. |
| [08 · Compare DNA ladders](08_ladders_and_gels.ipynb) | Switch markers on the same samples, render an illustrative gel and export its SVG and lane inventory. |
| [09 · Review and edit an annotation](09_interactive_workspace.ipynb) | Select a feature across the circular origin, rename it, undo and redo the edit, and reopen the original and edited records in a saved project. |

## Running the notebooks

Select the **Python (strandfolio)** kernel in your notebook editor and run the cells in order. You can also execute a notebook from the checkout root after activating the project environment and registering its kernel. [Kernel setup](../installation/README.md#notebook-kernel-and-checks)

```sh
jupyter execute docs/notebooks/02_sequence_visualization.ipynb --kernel_name=strandfolio
```

From `docs/notebooks/`, use the filename alone. All five examples run offline with the project dependencies installed. Notebook 03 uses the local enzyme catalogue when available and otherwise builds a copy from the installed Biopython dictionary. [Catalogue guide](../../databases/enzymes/README.md)

## Plasmid examples

Each example that writes files prints its output location under the ignored `docs/notebooks/_outputs/` directory. Notebook 01 writes the three pUC19 exchange files under `I09/`. Notebook 02 writes the pUC19 circular, linear and overview figures under `V06/`, along with the [6,759 bp GFP plasmid map](../images/pBH4-His-SUMO-GFP-LPETGG.svg). The pUC19 figure highlights its 57-base multiple cloning site. [Plasmid maps and sources](../../databases/plasmids/README.md) · [README figure](../images/pUC19.svg)
