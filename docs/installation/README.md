# Install strandfolio

## Install the Mac app

Use the Strandfolio 0.1.7 Mac download at the [top of the project README](../../README.md#strandfolio). It requires an Apple Silicon Mac running macOS 26.0 or later and includes Python and the reference data. To update, quit Strandfolio and back up each entire project folder before opening it in the new app. Earlier versions may be unable to reopen a project after it saves results from a newer analysis engine. Open the disk image, drag Strandfolio to Applications, and choose **Replace** if asked. Eject the image, then launch Strandfolio from Applications.

The app has an ad-hoc signature. If macOS cannot verify the developer and you trust this download, dismiss the alert, open **System Settings → Privacy & Security** and choose **Open Anyway** for Strandfolio. A managed Mac may require administrator approval. Local testing used an Apple M3 Pro running macOS 27.2.1. [Apple’s first-open guidance](https://support.apple.com/en-us/102445)

## Install the desktop release

Install strandfolio directly from PyPI. The tested setup is Apple Silicon macOS with Python 3.12 and a graphical login. Linux desktop testing is pending. Windows is unsupported because the required Kalign 4.0.0 package excludes it. [Strandfolio 0.1.0 on PyPI](https://pypi.org/project/strandfolio/0.1.0/) · [Kalign platform support](https://pypi.org/project/kalign-python/4.0.0/)

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install strandfolio
python -m pip check
strandfolio --example
```

To install this exact release, use `python -m pip install strandfolio==0.1.0`. If you already installed an earlier private 0.1.0 build, use `python -m pip install --force-reinstall --no-deps strandfolio==0.1.0` to replace it with the published build, then run `python -m pip check`.

The desktop package includes both start-screen examples, icons and lookup data. It builds its restriction catalogue from the installed Biopython dictionary in the current user's cache on first use. Run `strandfolio` to open the workspace or pass a sequence file to the command.

## Install from a checkout

For source access, ask the maintainer for a repository invitation and authenticate Git with the invited account before cloning. If you already have a checkout, open a terminal in its root directory and skip the clone step. For the first installation, allow access to your Python package index or use a local wheel source.

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

## Reference data

The examples, feature catalogue, ladder catalogue and distributable GenBank maps come with the checkout. For a source checkout, install the local enzyme catalogue to calculate a new digest. With the project environment active and Biopython 1.88 installed, the command below builds it from the installed dictionary. It takes a new output path and preserves any existing database.

```sh
python -m databases.enzymes.build_catalogue --output databases/enzymes/restriction_enzymes.sqlite3
```

If an enzyme catalogue read fails, choose **Retry enzyme catalogue** in the Sequence tab’s Digest panel after correcting the reported problem. In a checkout, build the missing catalogue with the command above. In the wheel installation, retrying also attempts the first-use build if the catalogue is missing.

Notebook 03 can build its own example catalogue from the installed Biopython dictionary. The enzyme guide gives checksums for restoring the historical release. [Enzyme setup and provenance](../../databases/enzymes/README.md#rebuild-and-verify) · [Reference data index](../../databases/README.md)

## Publish a new release

For the next release, set a new version in `pyproject.toml`, build it and test the wheel in a fresh environment. Run the commands below from the checkout root with that pair in `dist/`. Twine accepts a strandfolio project-scoped PyPI token at its hidden password prompt. [PyPI API token help](https://docs.pypi.org/publishing/publishing-to-pypi/#using-an-api-token)

```sh
python -m pip install twine
version=$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
wheel="dist/strandfolio-${version}-py3-none-any.whl"
sdist="dist/strandfolio-${version}.tar.gz"
python -m twine check --strict "$wheel" "$sdist"
python -m twine upload --repository-url https://upload.pypi.org/legacy/ --username __token__ "$wheel" "$sdist"
```

To rebuild a release from source, install `build` in the build environment and run `python -m build` from the checkout root. This also verifies that a wheel can be built from the source archive. The [build configuration](../../pyproject.toml) selects the application code, desktop resources and data notices. The source archive also carries the three files needed to rebuild it. Notebook tools, screenshots, private sequence collections, cached enzyme databases and maintainer files stay in the checkout.

## Notebook kernel and checks

In the activated environment, register a kernel within that environment and execute a notebook from the checkout root. The same commands work with either installation method.

```sh
python -m ipykernel install --sys-prefix --name strandfolio --display-name "Python (strandfolio)"
jupyter-execute docs/notebooks/02_sequence_visualization.ipynb --kernel_name=strandfolio
```

In a notebook editor, select **Python (strandfolio)**. The notebook index lists each investigation and its additional data requirements. [Notebook index](../notebooks/README.md)
