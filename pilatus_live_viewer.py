#!/usr/bin/env python3
"""
Pilatus 6M Live CBF Viewer  -  native desktop GUI
=================================================

A real-time, READ-ONLY desktop window for watching Pilatus 6M `.cbf`
diffraction images as they are collected. Point it at a folder and it
continuously displays the newest `.cbf`, auto-refreshing many times per
second. If you point it at a parent folder (e.g. .../raw6M), it auto-searches
the nested subfolders, finds the scan that is currently being written, and
follows new scans as they start.

Features:
  * Live image with log/linear, colormap, downsample and contrast controls.
  * Auto-search nested folders + follow the active scan.
  * Cursor readout: hover to see detector pixel and raw counts.
  * ROI monitor: drag a box over a peak and watch its integrated intensity
    vs frame number update live as frames arrive.
  * Wrapping control bar + resizable image/plot splitter, so the window can
    be made as small or large as you like.

This is the desktop-window version of the web app. It uses pyqtgraph, which
is fast enough to redraw full 6-megapixel frames in real time.

SAFETY  -  READ ONLY
--------------------
This program ONLY READS your data files. It never writes, renames, moves,
deletes, or modifies anything in your data folders. Each `.cbf` is opened
read-only and closed immediately. Your raw data is never touched.

RUN
---
    python pilatus_live_viewer.py
        Open the window; type/Browse to a folder and click "Watch".

    python pilatus_live_viewer.py --folder /nfs/chess/id4b/2026-2/sarker-4910-a/raw6M
        Start already watching; auto-searches nested scan folders.

NOTE: This is a desktop GUI, so it needs a display. On a remote beamline
machine (e.g. lnx201) run it over X11 forwarding ( ssh -X you@lnx201 ) or
from a VNC/NoMachine session. If you have no display, use the web-app
version (pilatus_live_app.py) and open it in a browser instead.

REQUIREMENTS
------------
    pip install fabio numpy pyqtgraph PyQt5
(PySide2 / PySide6 also work; pyqtgraph picks whichever is installed.)
"""

import os
import re
import sys
import time
import argparse
from collections import deque

import numpy as np

try:
    import fabio
except ImportError:
    sys.stderr.write("\nERROR: 'fabio' is required to read .cbf files.\n"
                     "Install with:  pip install fabio\n\n")
    raise

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

# Display detector images row-major (data[row, col]) without transposing.
pg.setConfigOptions(imageAxisOrder="row-major")

COLORMAPS = ["viridis", "inferno", "magma", "plasma",
             "cividis", "turbo", "gray", "jet"]

_FRAME_NO_RE = re.compile(r"(\d+)\.cbf$")


# ---------------------------------------------------------------------------
# A simple wrapping (flow) layout so the control bar reflows when the window
# is made narrow - this is what lets you shrink the window horizontally.
# ---------------------------------------------------------------------------
class FlowLayout(QtWidgets.QLayout):
    def __init__(self, parent=None, margin=0, spacing=6):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)
        self._items = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return QtCore.Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QtCore.QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QtCore.QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only):
        x, y, line_height = rect.x(), rect.y(), 0
        spacing = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + spacing
            if next_x - spacing > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + spacing
                next_x = x + hint.width() + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y()


# ---------------------------------------------------------------------------
# Read-only file helpers (identical logic to the web app)
# ---------------------------------------------------------------------------
def find_newest_cbf_fast(folder):
    """Newest .cbf in one folder, by FILENAME (Pilatus frames are zero-padded,
    so the highest name is the newest). Only one stat call - fast on NFS.
    Read-only."""
    best = None
    try:
        with os.scandir(folder) as it:
            for e in it:
                n = e.name
                if n.endswith(".cbf") and (best is None or n > best):
                    best = n
    except OSError:
        return None, -1.0
    if best is None:
        return None, -1.0
    p = os.path.join(folder, best)
    try:
        m = os.path.getmtime(p)
    except OSError:
        m = -1.0
    return p, m


def find_active_scan_dir(folder):
    """Locate the scan folder currently being written, by directory mtime.
    Only stats directories, so it stays fast on a big tree. Read-only."""
    best_dir, best_mtime = None, -1.0
    try:
        for root, _dirs, files in os.walk(folder, followlinks=True):
            if not any(f.endswith(".cbf") for f in files):
                continue
            try:
                m = os.path.getmtime(root)
            except OSError:
                continue
            if m > best_mtime:
                best_mtime, best_dir = m, root
    except OSError:
        return None
    return best_dir


def size_stable(path, wait=0.02):
    """True if file size is unchanged across a tiny pause (avoids mid-write
    frames). Read-only."""
    try:
        s1 = os.path.getsize(path)
        if s1 <= 0:
            return False
        time.sleep(wait)
        s2 = os.path.getsize(path)
    except OSError:
        return False
    return s1 == s2


def maxpool(a, f):
    """Downsample by max over f x f blocks (preserves sharp Bragg peaks)."""
    if f <= 1:
        return a
    h, w = a.shape
    h2, w2 = (h // f) * f, (w // f) * f
    if h2 == 0 or w2 == 0:
        return a
    a = a[:h2, :w2]
    return a.reshape(h2 // f, f, w2 // f, f).max(axis=(1, 3))


def frame_number_from_path(path):
    """Extract the trailing zero-padded frame number from a Pilatus filename."""
    m = _FRAME_NO_RE.search(os.path.basename(path))
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Background loader thread
# ---------------------------------------------------------------------------
class Loader(QtCore.QThread):
    newImage = QtCore.Signal(object, str, float, object)  # data, path, mtime, active
    status = QtCore.Signal(str)

    def __init__(self):
        super().__init__()
        self.folder = None
        self.recurse = False            # "always auto-search subfolders"
        self.interval = 0.1
        self.discover_interval = 2.0
        self.paused = False
        self._running = True
        self._active_dir = None
        self._active_last_path = None
        self._active_last_change = 0.0
        self._loaded_path = None
        self._loaded_mtime = -1.0

    def configure(self, **kw):
        if "folder" in kw and kw["folder"] is not None:
            self.folder = kw["folder"]
            self._active_dir = None
            self._loaded_path = None
            self._loaded_mtime = -1.0
        if "recurse" in kw and kw["recurse"] is not None:
            self.recurse = bool(kw["recurse"])
            self._active_dir = None
        if "interval" in kw and kw["interval"]:
            self.interval = max(0.03, float(kw["interval"]))
        if "discover_interval" in kw and kw["discover_interval"]:
            self.discover_interval = float(kw["discover_interval"])
        if "paused" in kw and kw["paused"] is not None:
            self.paused = bool(kw["paused"])

    def stop(self):
        self._running = False

    def _locate_recursive(self, folder):
        now = time.time()
        ad = self._active_dir
        if ad and os.path.isdir(ad):
            path, mtime = find_newest_cbf_fast(ad)
            if path is not None:
                if path != self._active_last_path:
                    self._active_last_path = path
                    self._active_last_change = now
                if (now - self._active_last_change) < self.discover_interval:
                    return path, mtime
        active = find_active_scan_dir(folder)
        self._active_dir = active
        if active:
            path, mtime = find_newest_cbf_fast(active)
            self._active_last_path = path
            self._active_last_change = now
            return path, mtime
        return None, -1.0

    def run(self):
        while self._running:
            interval = self.interval
            if self.paused or not self.folder:
                time.sleep(interval)
                continue

            folder = self.folder
            if not os.path.isdir(folder):
                self.status.emit("Folder not found: %s" % folder)
                time.sleep(max(0.5, interval))
                continue

            if self.recurse:
                path, mtime = self._locate_recursive(folder)
            else:
                path, mtime = find_newest_cbf_fast(folder)
                if path is None:
                    path, mtime = self._locate_recursive(folder)

            if path is None:
                self.status.emit("Searching for .cbf files under %s ..." % folder)
                time.sleep(max(0.3, interval))
                continue

            if path == self._loaded_path and mtime == self._loaded_mtime:
                time.sleep(interval)
                continue

            if not size_stable(path):
                time.sleep(interval)
                continue

            try:
                data = np.asarray(fabio.open(path).data)   # read-only open
            except Exception as exc:                        # noqa: BLE001
                self.status.emit("Load skipped (%s): %s"
                                 % (os.path.basename(path), exc))
                time.sleep(interval)
                continue

            self._loaded_path = path
            self._loaded_mtime = mtime
            self.newImage.emit(data, path, mtime, self._active_dir)
            time.sleep(interval)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class LiveViewer(QtWidgets.QMainWindow):
    def __init__(self, folder=None, recurse=False, interval=0.1,
                 downsample=1, discover=2.0):
        super().__init__()
        self.setWindowTitle("Pilatus 6M - Live CBF Viewer (read-only)")
        self.resize(1150, 980)
        self.setMinimumSize(420, 340)        # allow shrinking the window freely

        self._raw = None                     # raw counts (full res)
        self._disp_counts = None             # masked + downsampled counts shown
        self._ds_factor = 1
        self._first = True
        self._fps_t0 = time.time()
        self._fps_n = 0
        self._last_fps = 0.0

        # ROI monitor history
        self._roi_x = deque(maxlen=200000)   # frame numbers
        self._roi_y = deque(maxlen=200000)   # integrated ROI intensity
        self._roi_counter = 0
        self._cur_active = None

        self._build_ui(folder, recurse, interval, downsample)

        self.loader = Loader()
        self.loader.configure(folder=folder, recurse=recurse, interval=interval,
                              discover_interval=discover)
        self.loader.newImage.connect(self.on_new_image)
        self.loader.status.connect(self.statusBar().showMessage)
        self.loader.start()

    # -- UI -----------------------------------------------------------------
    def _build_ui(self, folder, recurse, interval, downsample):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        v = QtWidgets.QVBoxLayout(central)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        # row 1: folder + browse + watch (path field can shrink)
        r1 = QtWidgets.QHBoxLayout()
        r1.addWidget(QtWidgets.QLabel("Folder:"))
        self.path_edit = QtWidgets.QLineEdit(folder or "")
        self.path_edit.setPlaceholderText(
            "Paste a folder, e.g. /nfs/chess/id4b/2026-2/sarker-4910-a/raw6M")
        self.path_edit.setMinimumWidth(120)
        self.path_edit.returnPressed.connect(self.apply_folder)
        r1.addWidget(self.path_edit, 1)
        b_browse = QtWidgets.QPushButton("Browse...")
        b_browse.clicked.connect(self.browse_folder)
        r1.addWidget(b_browse)
        self.watch_btn = QtWidgets.QPushButton("Watch")
        self.watch_btn.clicked.connect(self.apply_folder)
        r1.addWidget(self.watch_btn)
        v.addLayout(r1)

        # row 2: options, in a wrapping flow layout
        opts = QtWidgets.QWidget()
        flow = FlowLayout(opts, margin=0, spacing=6)

        self.recurse_cb = QtWidgets.QCheckBox("auto-search subfolders")
        self.recurse_cb.setToolTip(
            "Always dive into subfolders to find the newest .cbf. (Even when "
            "off, watching a folder with no .cbf will auto-search.)")
        self.recurse_cb.setChecked(recurse)
        self.recurse_cb.toggled.connect(
            lambda val: self.loader.configure(recurse=val))
        flow.addWidget(self.recurse_cb)

        self.log_cb = QtWidgets.QCheckBox("log")
        self.log_cb.setChecked(True)
        self.log_cb.toggled.connect(self.redraw_current)
        flow.addWidget(self.log_cb)

        self.auto_cb = QtWidgets.QCheckBox("auto-contrast")
        self.auto_cb.setChecked(True)
        flow.addWidget(self.auto_cb)

        flow.addWidget(QtWidgets.QLabel("cmap"))
        self.cmap_combo = QtWidgets.QComboBox()
        self.cmap_combo.addItems(COLORMAPS)
        self.cmap_combo.currentTextChanged.connect(self.apply_colormap)
        flow.addWidget(self.cmap_combo)

        flow.addWidget(QtWidgets.QLabel("downsample"))
        self.ds_spin = QtWidgets.QSpinBox()
        self.ds_spin.setRange(1, 8)
        self.ds_spin.setValue(max(1, int(downsample)))
        self.ds_spin.valueChanged.connect(self.redraw_current)
        flow.addWidget(self.ds_spin)

        flow.addWidget(QtWidgets.QLabel("refresh(s)"))
        self.int_spin = QtWidgets.QDoubleSpinBox()
        self.int_spin.setRange(0.03, 5.0)
        self.int_spin.setSingleStep(0.05)
        self.int_spin.setDecimals(2)
        self.int_spin.setValue(interval)
        self.int_spin.valueChanged.connect(
            lambda val: self.loader.configure(interval=val))
        flow.addWidget(self.int_spin)

        self.pause_btn = QtWidgets.QPushButton("Freeze")
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self.toggle_pause)
        flow.addWidget(self.pause_btn)

        self.roi_cb = QtWidgets.QCheckBox("ROI monitor")
        self.roi_cb.setToolTip(
            "Show a box on the image and plot its integrated intensity "
            "vs frame number as frames arrive.")
        self.roi_cb.toggled.connect(self.toggle_roi)
        flow.addWidget(self.roi_cb)

        self.roi_reset_btn = QtWidgets.QPushButton("reset plot")
        self.roi_reset_btn.clicked.connect(self.reset_roi_history)
        flow.addWidget(self.roi_reset_btn)

        v.addWidget(opts)

        # splitter: image on top, ROI plot below (both resizable)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)

        self.imv = pg.ImageView()
        self.imv.ui.roiBtn.hide()
        self.imv.ui.menuBtn.hide()
        self.imv.view.invertY(True)
        self.splitter.addWidget(self.imv)

        self.roi_plot = pg.PlotWidget()
        self.roi_plot.setLabel("bottom", "frame number")
        self.roi_plot.setLabel("left", "ROI integrated intensity")
        self.roi_plot.showGrid(x=True, y=True, alpha=0.3)
        self.roi_curve = self.roi_plot.plot(pen=pg.mkPen("y", width=1),
                                            symbol="o", symbolSize=3,
                                            symbolBrush="y")
        self.roi_plot.hide()
        self.splitter.addWidget(self.roi_plot)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)
        v.addWidget(self.splitter, 1)

        # ROI box (created once, added/removed from the view on toggle)
        self.roi = pg.RectROI([100, 100], [300, 300], pen=pg.mkPen("r", width=2))
        self.roi.addScaleHandle([1, 1], [0, 0])
        self.roi.addScaleHandle([0, 0], [1, 1])
        self.roi.sigRegionChanged.connect(self._roi_moved)

        self.apply_colormap(self.cmap_combo.currentText())

        # cursor readout in the status bar (right side)
        self.readout = QtWidgets.QLabel("cursor: -")
        self.statusBar().addPermanentWidget(self.readout)
        self.imv.getView().scene().sigMouseMoved.connect(self._on_mouse_moved)

        self.statusBar().showMessage(
            "Read-only viewer ready. Enter a folder and click Watch.")

    # -- actions ------------------------------------------------------------
    def browse_folder(self):
        start = self.path_edit.text().strip() or os.path.expanduser("~")
        if not os.path.isdir(start):
            start = os.path.expanduser("~")
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select folder to watch", start)
        if chosen:
            self.path_edit.setText(chosen)
            self.apply_folder()

    def apply_folder(self):
        folder = self.path_edit.text().strip()
        if not folder:
            return
        self._first = True
        self.loader.configure(folder=folder)
        self.statusBar().showMessage("Watching: %s" % folder)

    def apply_colormap(self, name):
        cmap = None
        for source in (None, "matplotlib"):
            try:
                cmap = pg.colormap.get(name) if source is None \
                    else pg.colormap.get(name, source=source)
                if cmap is not None:
                    break
            except Exception:                  # noqa: BLE001
                continue
        if cmap is not None:
            self.imv.setColorMap(cmap)

    def toggle_pause(self, checked):
        self.loader.configure(paused=checked)
        self.pause_btn.setText("Frozen - click to resume" if checked else "Freeze")

    # -- ROI monitor --------------------------------------------------------
    def toggle_roi(self, on):
        view = self.imv.getView()
        if on:
            view.addItem(self.roi)
            self.roi_plot.show()
            self.splitter.setSizes([self.height() - 240, 220])
            self._update_roi_point(append=False)
        else:
            view.removeItem(self.roi)
            self.roi_plot.hide()

    def reset_roi_history(self):
        self._roi_x.clear()
        self._roi_y.clear()
        self.roi_curve.setData([], [])

    def _roi_sum(self):
        """Integrated intensity inside the ROI, in displayed counts."""
        if self._disp_counts is None:
            return None
        try:
            region = self.roi.getArrayRegion(self._disp_counts,
                                             self.imv.getImageItem())
        except Exception:                      # noqa: BLE001
            return None
        if region is None or region.size == 0:
            return None
        return float(np.nansum(region))

    def _roi_moved(self):
        # When the box is dragged, refresh the latest plotted point.
        if self.roi_cb.isChecked():
            self._update_roi_point(append=False)

    def _update_roi_point(self, append, frame_no=None):
        val = self._roi_sum()
        if val is None:
            return
        if append:
            self._roi_x.append(frame_no if frame_no is not None
                               else self._roi_counter)
            self._roi_y.append(val)
        elif self._roi_y:
            self._roi_y[-1] = val              # update current frame's point
        if self._roi_x:
            self.roi_curve.setData(list(self._roi_x), list(self._roi_y))

    # -- display ------------------------------------------------------------
    def redraw_current(self, *_):
        if self._raw is not None:
            self._show(self._raw, reset_range=False)

    def _show(self, data, reset_range):
        counts = np.asarray(data, dtype=np.float32)
        counts = np.where(counts < 0, 0.0, counts)   # mask gap/dead pixels
        f = self.ds_spin.value()
        if f > 1:
            counts = maxpool(counts, f)
        self._disp_counts = counts
        self._ds_factor = f
        disp = np.log10(counts + 1.0) if self.log_cb.isChecked() else counts
        auto = self.auto_cb.isChecked()
        self.imv.setImage(disp, autoLevels=auto, autoRange=reset_range,
                          autoHistogramRange=auto)

    def _on_mouse_moved(self, pos):
        if self._disp_counts is None:
            return
        img_item = self.imv.getImageItem()
        vb = self.imv.getView()
        if not vb.sceneBoundingRect().contains(pos):
            self.readout.setText("cursor: -")
            return
        mp = img_item.mapFromScene(pos)
        col = int(mp.x())
        row = int(mp.y())
        h, w = self._disp_counts.shape
        if 0 <= row < h and 0 <= col < w:
            cnt = self._disp_counts[row, col]
            f = self._ds_factor
            # report approximate full-resolution detector pixel
            det_r, det_c = row * f, col * f
            self.readout.setText(
                "pixel (row=%d, col=%d)   counts=%d%s"
                % (det_r, det_c, int(cnt),
                   ("  [%dx binned]" % f) if f > 1 else ""))
        else:
            self.readout.setText("cursor: -")

    def on_new_image(self, data, path, mtime, active_dir):
        self._raw = data
        self._show(data, reset_range=self._first)
        self._first = False

        # New scan? clear the ROI plot so traces don't run together.
        frame_no = frame_number_from_path(path)
        self._roi_counter += 1
        new_scan = (active_dir != self._cur_active) or (
            frame_no is not None and self._roi_x and frame_no < self._roi_x[-1])
        if new_scan:
            self._cur_active = active_dir
            self.reset_roi_history()
        if self.roi_cb.isChecked():
            self._update_roi_point(append=True, frame_no=frame_no)

        # fps
        self._fps_n += 1
        now = time.time()
        dt = now - self._fps_t0
        if dt >= 1.0:
            self._last_fps = self._fps_n / dt
            self._fps_t0 = now
            self._fps_n = 0

        try:
            mx = int(np.max(data))
        except ValueError:
            mx = 0
        following = ""
        if active_dir and os.path.abspath(active_dir) != os.path.abspath(
                self.path_edit.text().strip() or active_dir):
            following = "  following: %s" % os.path.basename(active_dir.rstrip("/"))
        self.statusBar().showMessage(
            "%s   (%d x %d)   max=%d cts   age=%.1fs   %.1f fps%s"
            % (os.path.basename(path), data.shape[0], data.shape[1], mx,
               now - mtime, self._last_fps, following))

    # -- shutdown -----------------------------------------------------------
    def closeEvent(self, event):
        try:
            self.loader.stop()
            self.loader.wait(2000)
        finally:
            super().closeEvent(event)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="Real-time, read-only Pilatus 6M .cbf desktop viewer.")
    ap.add_argument("--folder", default=None, help="Folder to watch.")
    ap.add_argument("--recurse", action="store_true",
                    help="Always auto-search subfolders for the newest .cbf.")
    ap.add_argument("--interval", type=float, default=0.1,
                    help="Refresh interval in seconds (default 0.1).")
    ap.add_argument("--downsample", type=int, default=1,
                    help="Max-pool factor for display (1 = full resolution).")
    ap.add_argument("--discover", type=float, default=2.0,
                    help="Idle seconds before re-searching for a new scan "
                         "(default 2.0).")
    args = ap.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    win = LiveViewer(folder=args.folder, recurse=args.recurse,
                     interval=args.interval, downsample=args.downsample,
                     discover=args.discover)
    win.show()
    sys.exit(app.exec_() if hasattr(app, "exec_") else app.exec())


if __name__ == "__main__":
    main()
