# Pilatus 6M Live CBF Viewer

A real-time, **read-only** desktop application for watching Pilatus 6M `.cbf`
diffraction images as they stream to disk during data collection. Point it at a
folder and it continuously displays the newest frame, auto-refreshing many times
per second. Built for the **QM² beamline (CHESS ID4B)**, but it works with any
Pilatus-style `.cbf` data.

> **Safety first — this program never modifies your data.** It only reads files.
> It never writes, renames, moves, deletes, or alters anything in your data
> folders, so it is safe to run against live acquisition directories.

---

## Highlights

- **Real-time display** — refreshes at 0.1 s (or faster) to show the latest frame as it lands.
- **Auto-follow the active scan** — point it at a parent folder such as `.../raw6M` and it searches the nested subfolders, finds the scan currently being written, and follows new scans as they start.
- **Cursor readout** — hover over the image to see the detector pixel coordinate and the raw counts under the cursor.
- **Live ROI monitor** — drag a box over a Bragg peak and watch its integrated intensity plotted versus frame number as frames arrive.
- **Flexible display** — log / linear scaling, multiple colormaps, per-frame auto-contrast, and max-pool downsampling that preserves sharp peaks.
- **Resizable, reflowing layout** — a wrapping control bar and a movable image/plot splitter let you make the window as small or large as you like.

---

## Requirements

- Python 3.8+
- A graphical display (see the remote-machine note below)
- Python packages:

```
fabio
numpy
pyqtgraph
PyQt5
```

`PySide2` or `PySide6` also work — pyqtgraph will use whichever Qt binding is
installed.

---

## Installation

```bash
git clone https://github.com/<your-username>/pilatus-6m-live-viewer.git
cd pilatus-6m-live-viewer
pip install -r requirements.txt
```

Or install the dependencies directly:

```bash
pip install fabio numpy pyqtgraph PyQt5
```

---

## Usage

Open the window and pick a folder interactively:

```bash
python pilatus_live_viewer.py
```

Then type or **Browse…** to a folder and click **Watch**.

Or start already watching a folder (auto-searches nested scan folders):

```bash
python pilatus_live_viewer.py --folder /nfs/chess/id4b/2026-2/sarker-4910-a/raw6M
```

### Command-line options

| Option | Default | Description |
|---|---|---|
| `--folder PATH` | *(none)* | Folder to watch on startup. |
| `--recurse` | off | Always auto-search subfolders for the newest `.cbf`. (Even with this off, watching a folder that has no direct `.cbf` will auto-search.) |
| `--interval SEC` | `0.1` | Refresh interval in seconds. |
| `--downsample N` | `1` | Max-pool factor for display; `1` is full resolution. |
| `--discover SEC` | `2.0` | Idle seconds before re-searching for a newly started scan. |

### In-window controls

`auto-search subfolders`, `log`, `auto-contrast`, colormap selector,
`downsample`, `refresh(s)`, `Freeze` (pause updates), `ROI monitor`, and
`reset plot`. The status bar shows the current filename, frame size, max counts,
frame age, live FPS, and which scan folder is being followed.

---

## Running on a remote beamline machine

This is a native desktop GUI, so it needs a display. On a headless beamline node
(e.g. `lnx201`) you have two options:

- **X11 forwarding:** `ssh -X you@lnx201.classe.cornell.edu`, then run the viewer. Works over the network but can be laggy on slow links.
- **VNC / NoMachine:** run it inside a remote desktop session for the smoothest experience.

If you have no display at all, use the companion **web-app** version, which
serves the live view to a browser instead of opening a desktop window.

---

## How it works

The viewer separates file I/O from the UI so the display never blocks. A
background thread finds the newest frame and decodes it; the main thread only
draws.

- **Newest frame by filename.** Pilatus frames are zero-padded (`..._03650.cbf`), so the highest filename is the newest frame. Finding it needs a single directory scan — fast even on busy NFS mounts.
- **Active-scan discovery.** When pointed at a parent tree, it locates the scan folder currently being written by directory modification time, then re-checks only that folder until it goes idle. It re-discovers a new scan when writing stops for `--discover` seconds.
- **Safe mid-write handling.** A frame is only loaded once its file size is stable across a short pause, avoiding half-written images.
- **Peak-preserving downsampling.** Optional max-pooling shrinks the image for speed while keeping sharp Bragg peaks visible (unlike averaging).
- **Read-only decoding.** Each `.cbf` is opened read-only with `fabio` and closed immediately; negative gap/dead pixels are masked for display.

---

## Troubleshooting

- **`ERROR: 'fabio' is required`** — run `pip install fabio`.
- **No window appears over SSH** — reconnect with `ssh -X` (or `-Y`) and confirm `echo $DISPLAY` is set; otherwise use a VNC/NoMachine session.
- **"Searching for .cbf files…" never resolves** — confirm the folder path is correct and readable, and that frames are actually being written; enable *auto-search subfolders* if you pointed at a parent directory.
- **Display feels slow on huge frames** — increase `downsample` to 2 and/or raise `refresh(s)` slightly.

---

## License

Suggested license: **MIT** (see the `LICENSE` file). This is a beamline-developed
tool, so please confirm the appropriate license and any redistribution policy
with the CHESS / QM² beamline staff before publishing.

---

## Open in the CHESS beamline
`Activate the environement`: `source /nfs/chess/sw/qm2_6M_viewer/bin/activate`
`Go to the folder`: `cd /nfs/chess/id4baux/suchi/2026/nxrefine_data_analysis/pil_live_app/`
`python code`: `python pilatus_live_GUI_update.py`



## Acknowledgements

Developed for the **QM² (Quantum Materials) beamline, CHESS ID4B**, Cornell High
Energy Synchrotron Source. Reads Pilatus `.cbf` images via
[`fabio`](https://github.com/silx-kit/fabio) and renders them with
[`pyqtgraph`](https://www.pyqtgraph.org/).

