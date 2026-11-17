# desktop app for merging maps
# run with:  map-merger   or   python3 -m map_merger

import os
import sys
import traceback

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QImage, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QDoubleSpinBox, QFileDialog, QFrame,
    QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QGridLayout, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QSizePolicy, QSpinBox, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from . import __version__, core

STYLE = """
* { font-family: "Inter", "Segoe UI", "Ubuntu", sans-serif; font-size: 13px; color: #e6e8ec; }
QMainWindow, #central { background: #14161b; }
#sidebar { background: #1b1e24; border-right: 1px solid #262a32; }
#title { font-size: 18px; font-weight: 600; }
#subtitle { color: #8b93a1; font-size: 12px; }
#section { color: #8b93a1; font-size: 11px; font-weight: 600; letter-spacing: 1px; margin-top: 6px; }
#muted { color: #8b93a1; }
#statValue { font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace; font-size: 12px; }
#note { color: #8b93a1; font-size: 12px; }

QListWidget { background: #14161b; border: 1px solid #262a32; border-radius: 8px; padding: 4px; outline: 0; }
QListWidget::item { padding: 6px 6px; border-radius: 6px; }
QListWidget::item:selected { background: #26324a; color: #e6e8ec; }
QListWidget::item:hover:!selected { background: #1f232b; }

QPushButton, QToolButton {
    background: #242832; border: 1px solid #2f3440; border-radius: 7px; padding: 7px 12px;
}
QPushButton:hover, QToolButton:hover { background: #2b303b; }
QPushButton:pressed, QToolButton:pressed { background: #20242c; }
QPushButton:disabled, QToolButton:disabled { color: #5c6370; background: #1e2128; border-color: #262a32; }
#primary { background: #3d7bfd; border: 1px solid #3d7bfd; font-weight: 600; padding: 9px 12px; }
#primary:hover { background: #5a8ffd; }
#primary:disabled { background: #233457; border-color: #233457; color: #7f8aa3; }
#iconBtn { padding: 6px 9px; min-width: 14px; }

#seg QToolButton { border-radius: 0; padding: 6px 14px; background: #1b1e24; }
#seg QToolButton:checked { background: #3d7bfd; border-color: #3d7bfd; font-weight: 600; }
#segLeft { border-top-left-radius: 7px; border-bottom-left-radius: 7px; }
#segRight { border-top-right-radius: 7px; border-bottom-right-radius: 7px; }

QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid #3a404d; background: #14161b; }
QCheckBox::indicator:checked { background: #3d7bfd; border-color: #3d7bfd; image: none; }

QSpinBox, QDoubleSpinBox {
    background: #14161b; border: 1px solid #2f3440; border-radius: 6px; padding: 0px 8px;
    min-width: 90px; min-height: 28px;
}
QSpinBox:focus, QDoubleSpinBox:focus { border-color: #3d7bfd; }
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    width: 0px; border: none;
}  /* no arrows, scroll or type to change */

#card { background: #14161b; border: 1px solid #262a32; border-radius: 8px; }
#topbar { background: #14161b; border-bottom: 1px solid #262a32; }
#statusbar { background: #14161b; border-top: 1px solid #262a32; }
#statusbar QLabel { color: #8b93a1; font-size: 12px; }
QGraphicsView { background: #0f1115; border: none; }
#emptyTitle { font-size: 17px; font-weight: 600; }
#chip { border-radius: 5px; }
QToolTip { background: #242832; color: #e6e8ec; border: 1px solid #2f3440; padding: 4px; }
"""


def to_qimage(arr):
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    if arr.ndim == 2:
        img = QImage(arr.data, w, h, w, QImage.Format_Grayscale8)
    else:
        img = QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888)
    return img.copy()  # detach from numpy memory


def rgb(c):
    return "rgb(%d,%d,%d)" % c


# ---- background merge so the window doesnt freeze on big maps

class MergeSignals(QObject):
    done = Signal(int, object)
    failed = Signal(int, str)


class MergeJob(QRunnable):
    def __init__(self, job_id, maps, opts):
        super().__init__()
        self.job_id = job_id
        self.maps = maps
        self.opts = opts
        self.signals = MergeSignals()

    def run(self):
        try:
            r = core.merge(self.maps, **self.opts)
            self.signals.done.emit(self.job_id, r)
        except Exception as e:
            traceback.print_exc()
            self.signals.failed.emit(self.job_id, str(e))


# ---- zoomable map view

class MapView(QGraphicsView):
    hovered = Signal(float, float)  # col, row in image coords (row 0 = top)
    left = Signal()

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.item = QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.FastTransformation)  # keep cells sharp
        self.scene().addItem(self.item)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setRenderHint(QPainter.SmoothPixmapTransform, False)
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.has_image = False

    def set_image(self, qimg, fit=False):
        self.item.setPixmap(QPixmap.fromImage(qimg))
        self.scene().setSceneRect(self.item.boundingRect().adjusted(-200, -200, 200, 200))
        if fit or not self.has_image:
            self.fit()
        self.has_image = True

    def clear(self):
        self.item.setPixmap(QPixmap())
        self.has_image = False

    def fit(self):
        r = self.item.boundingRect()
        if r.isEmpty():
            return
        self.fitInView(r.adjusted(-10, -10, 10, 10), Qt.KeepAspectRatio)

    def zoom(self, f):
        s = self.transform().m11() * f
        if 0.2 < s < 80:
            self.scale(f, f)

    def wheelEvent(self, e):
        self.zoom(1.2 if e.angleDelta().y() > 0 else 1 / 1.2)

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        p = self.mapToScene(e.position().toPoint())
        self.hovered.emit(p.x(), p.y())

    def leaveEvent(self, e):
        self.left.emit()
        super().leaveEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.has_image and getattr(self, "_auto_fit", True):
            self.fit()

    def mousePressEvent(self, e):
        self._auto_fit = False  # user started moving around, stop refitting on resize
        super().mousePressEvent(e)


# ---- main window

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Map Merger")
        self.resize(1280, 800)
        self.setAcceptDrops(True)

        self.maps = []
        self.result = None
        self.job_id = 0
        self.pool = QThreadPool.globalInstance()
        self.view_mode = "merged"

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self.run_merge)

        central = QWidget(objectName="central")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.build_sidebar())
        root.addWidget(self.build_canvas(), 1)

        save = QAction(self, shortcut=QKeySequence.Save, triggered=self.save)
        add = QAction(self, shortcut=QKeySequence.Open, triggered=self.add_dialog)
        self.addActions([save, add])

        self.refresh()

    # -- layout

    def build_sidebar(self):
        side = QFrame(objectName="sidebar")
        side.setFixedWidth(330)
        lay = QVBoxLayout(side)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)

        lay.addWidget(QLabel("Map Merger", objectName="title"))
        lay.addWidget(QLabel("Combine occupancy maps that started from the same point",
                             objectName="subtitle", wordWrap=True))

        lay.addWidget(QLabel("MAPS", objectName="section"))
        self.list = QListWidget()
        self.list.setMinimumHeight(150)
        self.list.currentRowChanged.connect(lambda _: self.update_buttons())
        lay.addWidget(self.list)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.add_btn = QPushButton("Add maps…", clicked=self.add_dialog)
        self.add_btn.setToolTip("Add map .yaml files (Ctrl+O). You can also drag them onto the window.")
        self.up_btn = QToolButton(text="↑", objectName="iconBtn", clicked=lambda: self.move(-1))
        self.up_btn.setToolTip("Move up. The first map is the reference for drift refine.")
        self.down_btn = QToolButton(text="↓", objectName="iconBtn", clicked=lambda: self.move(1))
        self.down_btn.setToolTip("Move down")
        self.del_btn = QToolButton(text="✕", objectName="iconBtn", clicked=self.remove)
        self.del_btn.setToolTip("Remove selected map")
        row.addWidget(self.add_btn, 1)
        row.addWidget(self.up_btn)
        row.addWidget(self.down_btn)
        row.addWidget(self.del_btn)
        lay.addLayout(row)

        lay.addWidget(QLabel("OPTIONS", objectName="section"))
        self.clear_walls = QCheckBox("Open walls another map sees through")
        self.clear_walls.setChecked(True)
        self.clear_walls.setToolTip("If one run saw a wall (e.g. a closed door) and another run clearly\n"
                                    "saw open space there, the cell becomes free.")
        self.refine = QCheckBox("Refine drift against first map")
        self.refine.setToolTip("Image registration (ECC) to fix small shifts/rotations between runs.\n"
                               "Only turn on if the overlay shows walls that dont line up.")
        lay.addWidget(self.clear_walls)
        lay.addWidget(self.refine)

        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.res = QDoubleSpinBox(decimals=3, minimum=0.0, maximum=1.0, singleStep=0.01)
        self.res.setSpecialValueText("Auto")
        self.res.setSuffix(" m")
        self.res.setToolTip("Output cell size. Auto uses the finest input map.")
        self.radius = QSpinBox(minimum=0, maximum=20, value=3)
        self.radius.setSuffix(" cells")
        self.radius.setToolTip("How far a map must be from its own wall before it can clear\n"
                               "another map's wall. Raise it if real walls get erased.")
        grid.addWidget(QLabel("Resolution", objectName="muted"), 0, 0)
        grid.addWidget(self.res, 0, 1)
        grid.addWidget(QLabel("Clear radius", objectName="muted"), 1, 0)
        grid.addWidget(self.radius, 1, 1)
        lay.addLayout(grid)

        for w in (self.clear_walls, self.refine):
            w.toggled.connect(self.schedule)
        self.clear_walls.toggled.connect(self.radius.setEnabled)
        self.res.valueChanged.connect(self.schedule)
        self.radius.valueChanged.connect(self.schedule)

        lay.addWidget(QLabel("RESULT", objectName="section"))
        card = QFrame(objectName="card")
        cg = QGridLayout(card)
        cg.setContentsMargins(12, 10, 12, 10)
        cg.setVerticalSpacing(5)
        self.stats = {}
        for i, key in enumerate(["Size", "Resolution", "Origin", "Free / Wall / Unknown", "Walls opened"]):
            cg.addWidget(QLabel(key, objectName="muted"), i, 0)
            v = QLabel("–", objectName="statValue")
            v.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            cg.addWidget(v, i, 1)
            self.stats[key] = v
        lay.addWidget(card)

        self.notes = QLabel("", objectName="note", wordWrap=True)
        lay.addWidget(self.notes)

        lay.addStretch(1)
        self.save_btn = QPushButton("Save merged map…", objectName="primary", clicked=self.save)
        self.save_btn.setToolTip("Writes .pgm + .yaml for map_server / nav2 (Ctrl+S)")
        lay.addWidget(self.save_btn)
        lay.addWidget(QLabel("v" + __version__, objectName="note"), 0, Qt.AlignRight)
        return side

    def build_canvas(self):
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        top = QFrame(objectName="topbar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(14, 10, 14, 10)

        seg = QWidget(objectName="seg")
        sl = QHBoxLayout(seg)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        self.mode_group = QButtonGroup(self)
        for i, (key, text, tip) in enumerate([
            ("merged", "Merged", "The map that will be saved"),
            ("overlay", "Overlay", "Each map's walls in its own colour, to check alignment"),
        ]):
            b = QToolButton(text=text, checkable=True)
            b.setObjectName("segLeft" if i == 0 else "segRight")
            b.setToolTip(tip)
            b.setChecked(key == self.view_mode)
            b.clicked.connect(lambda _=False, k=key: self.set_mode(k))
            self.mode_group.addButton(b)
            sl.addWidget(b)
        tl.addWidget(seg)

        self.legend = QWidget()
        self.legend_lay = QHBoxLayout(self.legend)
        self.legend_lay.setContentsMargins(14, 0, 0, 0)
        self.legend_lay.setSpacing(12)
        tl.addWidget(self.legend)
        tl.addStretch(1)

        for text, tip, fn in [("−", "Zoom out", lambda: self.view.zoom(1 / 1.25)),
                              ("+", "Zoom in", lambda: self.view.zoom(1.25)),
                              ("Fit", "Fit map to window", lambda: self.view.fit())]:
            b = QToolButton(text=text, objectName="iconBtn", clicked=fn)
            b.setToolTip(tip)
            tl.addWidget(b)
        lay.addWidget(top)

        self.stack = QStackedWidget()
        self.view = MapView()
        self.view.hovered.connect(self.show_cursor)
        self.view.left.connect(lambda: self.cursor_label.setText(""))
        self.stack.addWidget(self.build_empty())
        self.stack.addWidget(self.view)
        lay.addWidget(self.stack, 1)

        bar = QFrame(objectName="statusbar")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 6, 14, 6)
        self.status = QLabel("")
        self.cursor_label = QLabel("")
        bl.addWidget(self.status)
        bl.addStretch(1)
        bl.addWidget(self.cursor_label)
        lay.addWidget(bar)
        return wrap

    def build_empty(self):
        w = QWidget()
        l = QVBoxLayout(w)
        l.addStretch(1)
        t = QLabel("Add at least two maps to start", objectName="emptyTitle", alignment=Qt.AlignCenter)
        s = QLabel("Pick the .yaml files saved by map_saver, or drag them here.\n"
                   "The merged map updates as you change options.",
                   objectName="muted", alignment=Qt.AlignCenter)
        b = QPushButton("Add maps…", objectName="primary", clicked=self.add_dialog)
        b.setFixedWidth(160)
        l.addWidget(t)
        l.addSpacing(6)
        l.addWidget(s)
        l.addSpacing(16)
        l.addWidget(b, 0, Qt.AlignCenter)
        l.addStretch(1)
        return w

    # -- maps list

    def add_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add maps", self.last_dir(),
                                                "Map YAML (*.yaml *.yml)")
        self.add_files(files)

    def last_dir(self):
        return os.path.dirname(self.maps[-1]["path"]) if self.maps else os.path.expanduser("~")

    def add_files(self, files):
        bad = []
        for f in files:
            if any(m["path"] == os.path.abspath(f) for m in self.maps):
                continue
            try:
                self.maps.append(core.load_map(f))
            except Exception as e:
                bad.append("%s: %s" % (os.path.basename(f), e))
        if bad:
            QMessageBox.warning(self, "Some maps could not be loaded", "\n".join(bad))
        self.refresh()

    def remove(self):
        i = self.list.currentRow()
        if 0 <= i < len(self.maps):
            self.maps.pop(i)
            self.refresh(select=min(i, len(self.maps) - 1))

    def move(self, d):
        i = self.list.currentRow()
        j = i + d
        if 0 <= i < len(self.maps) and 0 <= j < len(self.maps):
            self.maps[i], self.maps[j] = self.maps[j], self.maps[i]
            self.refresh(select=j)

    def refresh(self, select=None):
        self.list.clear()
        for i, m in enumerate(self.maps):
            h, w = m["grid"].shape
            item = QListWidgetItem(self.list)
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(6, 4, 6, 4)
            rl.setSpacing(10)
            chip = QLabel(objectName="chip")
            chip.setFixedSize(10, 10)
            chip.setStyleSheet("background:%s;" % rgb(core.COLORS[i % len(core.COLORS)]))
            text = QVBoxLayout()
            text.setSpacing(0)
            name = QLabel(m["name"] + ("  · reference" if i == 0 else ""))
            name.setStyleSheet("background: transparent;")
            meta = QLabel("%d×%d  ·  %g m/cell" % (w, h, m["res"]), objectName="note")
            meta.setStyleSheet("background: transparent;")
            text.addWidget(name)
            text.addWidget(meta)
            rl.addWidget(chip)
            rl.addLayout(text, 1)
            row.setToolTip(m["path"])
            item.setSizeHint(QSize(0, 50))
            self.list.setItemWidget(item, row)
        if select is not None and select >= 0:
            self.list.setCurrentRow(select)
        self.update_buttons()
        self.update_legend()
        self.schedule()

    def update_buttons(self):
        i = self.list.currentRow()
        n = len(self.maps)
        self.up_btn.setEnabled(i > 0)
        self.down_btn.setEnabled(0 <= i < n - 1)
        self.del_btn.setEnabled(0 <= i < n)
        self.save_btn.setEnabled(self.result is not None and n >= 2)

    def update_legend(self):
        while self.legend_lay.count():
            w = self.legend_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        if self.view_mode != "overlay" or len(self.maps) < 2:
            return
        items = [(core.COLORS[i % len(core.COLORS)], m["name"]) for i, m in enumerate(self.maps)]
        items.append(((30, 30, 30), "agree"))
        if self.clear_walls.isChecked():
            items.append(((46, 204, 113), "opened"))
        for color, text in items:
            box = QWidget()
            bl = QHBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(5)
            chip = QLabel(objectName="chip")
            chip.setFixedSize(10, 10)
            border = "border:1px solid #555;" if color == (30, 30, 30) else ""
            chip.setStyleSheet("background:%s;%s" % (rgb(color), border))
            bl.addWidget(chip)
            bl.addWidget(QLabel(text, objectName="note"))
            self.legend_lay.addWidget(box)

    # -- merging

    def schedule(self):
        self.timer.start()

    def run_merge(self):
        self.job_id += 1
        if len(self.maps) < 2:
            self.result = None
            self.stack.setCurrentIndex(0)
            self.view.clear()
            self.show_stats()
            self.status.setText("%d map loaded" % len(self.maps) if self.maps else "No maps loaded")
            self.update_buttons()
            return
        opts = {
            "res": self.res.value() or None,
            "refine": self.refine.isChecked(),
            "keep_all_walls": not self.clear_walls.isChecked(),
            "radius": self.radius.value(),
        }
        self.status.setText("Merging…")
        job = MergeJob(self.job_id, list(self.maps), opts)
        job.signals.done.connect(self.merge_done)
        job.signals.failed.connect(self.merge_failed)
        self.pool.start(job)

    def merge_done(self, job_id, r):
        if job_id != self.job_id:
            return  # an older job, options changed since
        first = self.result is None
        self.result = r
        self.stack.setCurrentIndex(1)
        self.draw(fit=first)
        self.show_stats()
        self.status.setText("Merged %d maps" % len(self.maps))
        self.update_buttons()

    def merge_failed(self, job_id, msg):
        if job_id == self.job_id:
            self.status.setText("Merge failed: " + msg)

    def set_mode(self, mode):
        self.view_mode = mode
        self.update_legend()
        self.draw()

    def draw(self, fit=False):
        if not self.result:
            return
        if self.view_mode == "overlay":
            img = core.overlay_image(self.result["grids"], self.result["removed"])
        else:
            img = core.to_image(self.result["grid"])
        self.view.set_image(to_qimage(img), fit)

    def show_stats(self):
        r = self.result
        if not r:
            for v in self.stats.values():
                v.setText("–")
            self.notes.setText("")
            return
        g = r["grid"]
        h, w = g.shape
        n = g.size
        self.stats["Size"].setText("%d × %d" % (w, h))
        self.stats["Resolution"].setText("%g m" % r["res"])
        self.stats["Origin"].setText("%.2f, %.2f" % (r["origin"][0], r["origin"][1]))
        self.stats["Free / Wall / Unknown"].setText("%d / %d / %d %%" % (
            round(100 * (g == core.FREE).sum() / n),
            round(100 * (g == core.OCC).sum() / n),
            round(100 * (g == core.UNKNOWN).sum() / n)))
        self.stats["Walls opened"].setText(
            "%d cells" % r["removed"].sum() if self.clear_walls.isChecked() else "off")
        self.notes.setText("\n".join("Refine " + s for s in r["notes"]))

    def show_cursor(self, x, y):
        r = self.result
        if not r:
            return
        h, w = r["grid"].shape
        col, row = int(x), int(y)
        if not (0 <= col < w and 0 <= row < h):
            self.cursor_label.setText("")
            return
        row_b = h - 1 - row
        wx = r["origin"][0] + (col + 0.5) * r["res"]
        wy = r["origin"][1] + (row_b + 0.5) * r["res"]
        state = {core.FREE: "free", core.OCC: "wall", core.UNKNOWN: "unknown"}[int(r["grid"][row_b, col])]
        self.cursor_label.setText("x %.2f m   y %.2f m   ·   cell %d, %d   ·   %s" % (wx, wy, col, row_b, state))

    # -- saving

    def save(self):
        if not self.result or len(self.maps) < 2:
            return
        start = os.path.join(self.last_dir(), "merged_map.yaml")
        f, _ = QFileDialog.getSaveFileName(self, "Save merged map", start, "Map YAML (*.yaml)")
        if not f:
            return
        try:
            pgm, yml = core.save_map(self.result, f, self.maps[0]["info"])
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self.status.setText("Saved %s and %s" % (os.path.basename(pgm), os.path.basename(yml)))

    # -- drag and drop

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls()]
        files = [f for f in files if f.lower().endswith((".yaml", ".yml"))]
        self.add_files(files)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Map Merger")
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.add_files([f for f in sys.argv[1:] if f.lower().endswith((".yaml", ".yml"))])
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
