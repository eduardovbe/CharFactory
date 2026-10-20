# Contributing to Char Factory

Thanks for wanting to help! Bug reports, translations, documentation and code are all welcome.

## Reporting bugs and asking for features

- Search the [issues](../../issues) first; someone may have reported it already.
- Use the issue templates. For bugs, say what you did, what you expected and what happened, and
  attach the character (or a small one that shows the problem) when you can.
- Security problems go through [SECURITY.md](SECURITY.md), not public issues.

## Setting up

Needs Python 3.11 or newer.

```powershell
git clone https://github.com/eduardovbe/char-factory.git
cd char-factory
python -m venv .venv
.venv\Scripts\pip install -e ".[gui,test]"
.venv\Scripts\charfactory-gui
```

On Linux or macOS, use `.venv/bin/` instead of `.venv\Scripts\`.

## Running the tests

```powershell
.venv\Scripts\python -m pytest
```

Some tests need the official M.U.G.E.N files (kfm, `common1.cns`, the sample stages). Point
`CHARFACTORY_MUGEN` to a MUGEN folder with kfm to run them; without it they are skipped:

```powershell
$env:CHARFACTORY_MUGEN = "C:\path\to\mugen"
.venv\Scripts\python -m pytest
```

The GUI tests run offscreen (`QT_QPA_PLATFORM=offscreen`), so no window opens.

## Making a change

1. Fork the repository and create a branch from `main` (`fix/sff-palette`, `feat/stage-zoom`…).
2. Keep the change focused: one fix or feature per pull request.
3. Match the style of the code around it. Formats live in `charfactory/formats`, editing logic
   (usable without the interface) in `charfactory/tools`, and the Qt interface in `charfactory/gui`.
4. Add or update tests in `tests/` for what you changed.
5. Run the tests and, if you touched visible text, `python tools/i18n_check.py`.
6. Add a line to the `Unreleased` section of [CHANGELOG.md](CHANGELOG.md).
7. Open the pull request and fill in the template.

## Translations

Every visible text goes through `tr("...")` with the English text as the key. The translations
live in `charfactory/locale/pt_BR.json` and `charfactory/locale/es.json`. To add or fix a text,
edit the JSON files and run:

```powershell
.venv\Scripts\python tools/i18n_check.py
```

It reports missing entries, mismatched placeholders (`{0}`, `{1}`) and entries the code no longer
uses. To add a new language, copy `pt_BR.json` to `<code>.json`, translate the values and open a
pull request.

## Building the executable

```powershell
.venv\Scripts\pip install pyinstaller
.venv\Scripts\python tools/build_exe.py
```

The output goes to `dist/Char Factory/`. Releases are built by the `release` workflow when a
`v*` tag is pushed.

## Updating the screenshots

The images in `docs/media` are generated from the real app, without showing a window:

```powershell
.venv\Scripts\python tools/build_screenshots.py C:\path\to\mugen
```

It needs a MUGEN folder with kfm and writes PNG screenshots at the screen scale, animated WebP
clips and the banner (`docs/media/social-preview.png` is the GitHub social preview).

## Code of conduct

This project follows the [Code of Conduct](CODE_OF_CONDUCT.md). By taking part you agree to it.
