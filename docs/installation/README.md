# Install Strandfolio

[Mac app](#install-the-mac-app) · [Python package from PyPI](#install-the-python-package-from-pypi) · [Source checkout](#install-from-a-checkout)

## Install the Mac app

The Mac app includes Python and the reference data. Its declared minimum is macOS 26.0 on Apple Silicon. The installer was tested on an M3 Pro running macOS 27.2.1. [Current Mac installer](../../README.md#strandfolio)

To update, quit Strandfolio and back up each entire project folder before opening it in the new app. Earlier versions may be unable to reopen a project after it saves results from a newer analysis engine. Open the disk image, drag Strandfolio to **Applications**, and choose **Replace** if asked. Eject the image, then open **Applications → Strandfolio**.

The app has an ad-hoc signature. macOS may offer **Move to Trash** when it cannot verify the developer or check for malicious software. A managed Mac may require administrator approval. [First-launch steps](../../README.md#macos-blocks-strandfolio-on-first-launch)

<a name="install-the-desktop-release"></a>

## Install the Python package from PyPI

PyPI has its own release history. The tested desktop setup is Apple Silicon macOS with Python 3.12 and a graphical login. Linux desktop testing is pending. Windows is unsupported because the required Kalign package excludes it. [Strandfolio on PyPI](https://pypi.org/project/strandfolio/) · [Kalign platform support](https://pypi.org/project/kalign-python/4.0.0/)

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install strandfolio
python -m pip check
strandfolio --example
```

For later sessions, activate the same environment before running `strandfolio`. Run `strandfolio path/to/record.gb` to open your own file. To update from PyPI, run `python -m pip install --upgrade strandfolio`. For a specific published version, replace `VERSION` in `python -m pip install strandfolio==VERSION` with the version you want. To replace a local wheel of the same version in an environment with its dependencies installed, run `python -m pip install --force-reinstall --no-deps strandfolio==VERSION`, then `python -m pip check`. [PyPI release history](https://pypi.org/project/strandfolio/#history)

The desktop package includes both start-screen examples, icons and lookup data. On first use, it builds its restriction catalogue from the installed Biopython dictionary in your user cache.

## Install from a checkout

Clone the public repository to use the source and notebooks. If you already have a checkout, open a terminal in its root directory and skip the clone step. For the first installation, allow access to your Python package index or use a local wheel source.

```sh
git clone https://github.com/luisub/strandfolio.git
cd strandfolio
```

### Install with Python 3.12

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r docs/installation/requirements.txt
python -m pip check
python -m strandfolio --help
python -m strandfolio databases/plasmids/maps/pUC19.gb
```

The last command opens the annotated pUC19 plasmid shown in the README. For later sessions, open a terminal in this checkout, activate the same environment, and run `python -m strandfolio`. To open your own file, pass its path as an argument. The application imports its Python package from this checkout and reads the reference catalogues from its sibling `databases/` directory. Keep those directories together.

### Install with Conda

From the checkout root, create and activate the named environment, then launch with its Python.

```sh
conda env create -f docs/installation/environment.yml
conda activate strandfolio
python -m pip check
python -m strandfolio databases/plasmids/maps/pUC19.gb
```

Both source installation methods install the desktop dependencies and notebook execution tools. The PyPI distribution reads the same desktop requirements at build time. The Conda recipe selects Python 3.12 and pip. For an existing source environment, rerun `python -m pip install -r docs/installation/requirements.txt` to update dependencies. [Desktop requirements](runtime-requirements.txt) · [Checkout requirements](requirements.txt) · [Conda environment](environment.yml)

### Build a wheel from source

From the root of a checkout or an unpacked PyPI source archive, run `python -m pip install build`, then `python -m build --wheel`. The build writes the wheel to `dist/`. The source archive carries the build configuration, desktop requirements and build hook needed for this command. [Build configuration](../../pyproject.toml)

## Reference data

The examples, feature catalogue, ladder catalogue and distributable GenBank maps come with the checkout. For a source checkout, install the local enzyme catalogue to calculate a new digest. With the project environment active and Biopython 1.88 installed, the command below builds it from the installed dictionary. It takes a new output path and preserves any existing database.

```sh
python -m databases.enzymes.build_catalogue --output databases/enzymes/restriction_enzymes.sqlite3
```

If an enzyme catalogue read fails, choose **Retry enzyme catalogue** in the Sequence tab’s Digest panel after correcting the reported problem. In a checkout, build the missing catalogue with the command above. In the wheel installation, retrying also attempts the first-use build if the catalogue is missing.

Notebook 03 can build its own example catalogue from the installed Biopython dictionary. The enzyme guide gives checksums for restoring the historical release. [Enzyme setup and provenance](../../databases/enzymes/README.md#rebuild-and-verify) · [Reference data index](../../databases/README.md)

## Notebook kernel and checks

In the activated environment, register a kernel within that environment and execute a notebook from the checkout root. The same commands work with either installation method.

```sh
python -m ipykernel install --sys-prefix --name strandfolio --display-name "Python (strandfolio)"
jupyter-execute docs/notebooks/02_sequence_visualization.ipynb --kernel_name=strandfolio
```

In a notebook editor, select **Python (strandfolio)**. The notebook index lists each investigation and its additional data requirements. [Notebook index](../notebooks/README.md)
