# Third-Party Notices

Seas Audio Tools is distributed under the **GNU General Public License v2.0 or
later** (see `LICENSE`). That choice is not arbitrary: this application links
against mutagen, which is GPL-2.0-or-later, so the combined work must be
distributed under the GPL.

The portable Windows release bundles the components below. Their licences are
reproduced or referenced here to satisfy their distribution terms.

## Linked into the application

| Component | Version | Licence | Project |
| --- | --- | --- | --- |
| mutagen | 1.47+ | GPL-2.0-or-later | https://mutagen.readthedocs.io/ |
| NumPy | 2.0+ | BSD-3-Clause (plus bundled 0BSD, MIT, Zlib, CC0-1.0) | https://numpy.org/ |
| Pillow | 10+ | MIT-CMU | https://python-pillow.org/ |
| tqdm | 4.66+ | MPL-2.0 AND MIT | https://tqdm.github.io/ |
| PySide6 (Qt for Python) | 6.6+ | LGPL-3.0 | https://doc.qt.io/qtforpython/ |
| Python | 3.11+ | PSF-2.0 | https://www.python.org/ |

`mutagen`, `NumPy`, `Pillow`, `tqdm` and `PySide6` are imported into the
same process as this application. Their licence texts are shipped inside the
release folder under `_internal/*.dist-info/`.

## Bundled executable (separate process)

### Chromaprint / `fpcalc`

* **File:** `_internal/fpcalc.exe`
* **Version:** 1.6.1
* **Licence:** LGPL-2.1-or-later
* **Project:** https://acoustid.org/chromaprint
* **Source:** https://github.com/acoustid/chromaprint

`fpcalc` is a standalone executable. This application invokes it as a separate
process via `subprocess` and does not link against it. Per LGPL-2.1 section 6,
you may replace `_internal/fpcalc.exe` with a compatible build of your own; the
application will use whatever binary it finds (see the lookup order in
`src/seas_audio/core/fpcalc.py`). Source for Chromaprint is available at the
link above.

### FFmpeg (compiled into `fpcalc`)

The distributed `fpcalc.exe` build links FFmpeg libraries
(`Lavc62`, `Lavf62`, `SwR6`, `SwS`).

* **Licence:** LGPL-2.1-or-later
* **Project:** https://ffmpeg.org/
* **Source:** https://ffmpeg.org/download.html

## Runtime dependencies on Qt

The graphical interface uses Qt via the PySide6 bindings. Qt is distributed
under the LGPL-3.0 (and, optionally, a commercial licence); the application
links it dynamically, so the LGPL's relinking provisions are satisfied. Drag
and drop is provided natively by Qt itself - no third-party drop extension is
required.

## Licence texts

Full texts for the permissively licensed components are included in the
release's `_internal/` folder alongside each package's metadata. If any text is
missing from your copy, request it via
https://github.com/nicholasabisha/seas-audio-tools/issues and it will be
provided.