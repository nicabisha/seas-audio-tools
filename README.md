# Seas Audio Tools

[![CI](https://github.com/nicholasabisha/seas-audio-tools/actions/workflows/ci.yml/badge.svg)](https://github.com/nicholasabisha/seas-audio-tools/actions/workflows/ci.yml)

A drag-and-drop Windows app for two music-library chores:

| Tool | What it does |
| --- | --- |
| **Sanitize tags** | Rewrites embedded cover art to **baseline JPEG** and forces **ID3v2.3**, so car stereos and TVs stop choking on the tags. |
| **Find duplicates** | Fingerprints a library with Chromaprint and reports near-identical tracks - including re-encodes, trimmed intros and offset copies. |

Drop a file or folder onto the window, pick a tab, press Run.

---

## Download and run

No installer, no Python, no admin rights.

1. Download **`SeasAudioTools-1.1.0-win64.zip`** from the
   [latest release](https://github.com/nicholasabisha/seas-audio-tools/releases/latest).
2. Extract it anywhere, e.g. `D:\Tools\SeasAudioTools`.
3. Run **`SeasAudioTools.exe`**.

Keep the folder together - the `.exe` needs the `_internal` folder beside it.

> **Windows SmartScreen:** the binary is not code-signed, so the first launch
> may say "Windows protected your PC". Choose **More info -> Run anyway**.

Requires 64-bit Windows 10 or 11.

## How it works

Drag one or more files or folders straight from Explorer onto the window.

**Sanitize tags** - MP3 only. Options:

* *Dry run* - show what would change, write nothing.
* *Keep a .bak copy* - writes `track.mp3.bak` before touching the file.

Files that are already baseline JPEG + ID3v2.3 are reported `clean` and are
**not rewritten**, so re-running is safe and fast.

**Find duplicates** - accepts `.mp3 .flac .m4a .ogg .opus .wav`.

* Drop a *reference library* to compare a new batch against your existing
  collection; leave it empty to scan a batch against itself.
* Tune the similarity threshold (default `0.82`) and duration window
  (default `5s`).
* Fingerprints are cached, so repeat runs skip straight to comparison.
* Double-click a result to reveal the file in Explorer, and **Export CSV** to
  save the full report.

Nothing is ever deleted or moved. The duplicate tool only reports.

## Command line

The GUI is a shell over two ordinary commands, available when running from
source:

```powershell
seas-audio sanitize "D:\Music\Library" --dry-run
seas-audio sanitize "D:\Music\Library" --backup
seas-audio dupes "D:\Music\Library" --report duplicates.csv
seas-audio dupes "D:\Music\Inbox" --reference "D:\Music\Library"
seas-audio cache path
```

## From source

Requires Python **3.11+**.

```powershell
git clone https://github.com/nicholasabisha/seas-audio-tools.git
cd seas-audio-tools
python -m pip install -e ".[dev,build]"

seas-audio-gui          # the window
python -m pytest tests  # 65 tests
```

## Building the portable release

```powershell
python -m PyInstaller build\seas-audio.spec --noconfirm --distpath build\dist --workpath build\work
python build\make_release.py --version v1.1.0
```

Output: `build\dist\SeasAudioTools\`. The second command stages that folder with
`LICENSE` and `THIRD-PARTY-NOTICES.md` added, and writes
`release\SeasAudioTools-<version>-win64.zip`.

Tagging `v*` runs `.github/workflows/release.yml`, which does exactly this and
attaches the zip to the GitHub release.

## Layout

```
bin/fpcalc.exe          Chromaprint binary (v1.6.1)
src/seas_audio/core     scanner, fpcalc wrapper, cache, settings
src/seas_audio/tools    sanitizer + duplicates (no cross-imports)
src/seas_audio/ui       PySide6 (Qt) shell with native drag-and-drop
tests                   unittest suites, runnable with pytest
build                   PyInstaller spec
```

The two tools never import each other, so a failure in one cannot affect the
other, and each runs standalone from the CLI.

## Why the comparison is fast

The obvious implementation walks a NumPy array element by element, in Python, for
every pair and every offset. On a 2,211-track library that is 2,443,155 pairs at
~25 ms each - about **17 hours** worst case.

Two changes, measured on a real library:

* `np.bitwise_count` replaces `sum(int(x).bit_count() for x in ...)` - **13.5x**.
* Pairs are only generated inside a +/-5 s duration window - **93.8% fewer**
  pairs (2,443,155 -> 151,124).

Net result: worst case drops to roughly **five minutes**, with identical output.
The rewrite was verified to produce the same pair sets and the same similarity
values as the original algorithm.

## Where data lives

`%LOCALAPPDATA%\seas-audio-tools\` - cache, reports and settings per user.
Override with the `SEAS_AUDIO_HOME` environment variable.

Only successful fingerprints are cached, so an unreadable file is retried on the
next run rather than being written off permanently.

## Licence

[GPL-2.0-or-later](LICENSE).

Bundled third-party components, including the LGPL-licensed `fpcalc`, are
documented in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).