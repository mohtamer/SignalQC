"""Independent HDF5 Reader page. All file access runs outside the UI thread."""
from pathlib import Path
from html import escape

from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFileDialog, QSplitter, QTreeWidget, QTreeWidgetItem, QPlainTextEdit,
    QTabWidget, QScrollArea, QComboBox, QSpinBox, QDoubleSpinBox, QMessageBox,
)
import pyqtgraph as pg

from .hdf5_reader_v1 import inspect_node
from .theme_v1 import configure_accessibility


class ReadWork(QThread):
    def __init__(self, function, parent):
        super().__init__(parent)
        self.function = function
        self.output = None
        self.error = None

    def run(self):
        try:
            self.output = self.function()
        except Exception as exc:
            self.error = str(exc)


class ReaderPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.filename = ""
        self.selected_path = "/"
        self.worker = None
        self.series = []
        self.plots = []
        self.tree_items = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.filename_edit = QLineEdit()
        self.filename_edit.setReadOnly(True)
        self.filename_edit.setPlaceholderText("Open an unprocessed or processed HDF5 file")
        self.filename_edit.setAccessibleName("HDF5 file open in Reader")
        row.addWidget(self.filename_edit, 1)
        self.open_button = QPushButton("Open HDF5 file")
        self.open_button.clicked.connect(self.choose_file)
        row.addWidget(self.open_button)
        self.reload_button = QPushButton("Reload")
        self.reload_button.clicked.connect(lambda: self.open_file(self.filename))
        row.addWidget(self.reload_button)
        layout.addLayout(row)
        note = QLabel("Read only · No project required. Select a record to plot its stored waveforms, or select a dataset directly.")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.status = QLabel("No file open.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        split = QSplitter()
        split.setChildrenCollapsible(False)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("Events, records and datasets"))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Contents / shape"])
        self.tree.setAccessibleName("HDF5 file structure")
        self.tree.setMinimumWidth(250)
        self.tree.setColumnWidth(0, 230)
        self.tree.setUniformRowHeights(True)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.tree.currentItemChanged.connect(self.select_item)
        self.tree.itemExpanded.connect(self.expand_item)
        ll.addWidget(self.tree, 1)
        jump = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Optional: paste an HDF5 dataset path")
        self.path_edit.setAccessibleName("Path within the HDF5 file")
        self.path_edit.returnPressed.connect(self.go_to_path)
        jump.addWidget(self.path_edit, 1)
        self.go_button = QPushButton("Go")
        self.go_button.clicked.connect(self.go_to_path)
        jump.addWidget(self.go_button)
        ll.addLayout(jump)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        waveform_page = QWidget()
        wl = QVBoxLayout(waveform_page)
        self.display_options = QWidget()
        options = QHBoxLayout(self.display_options)
        options.setContentsMargins(0, 0, 0, 0)
        options.addWidget(QLabel("Time axis"))
        self.rate = QDoubleSpinBox()
        self.rate.setDecimals(6)
        self.rate.setRange(0, 10000000)
        self.rate.setSpecialValueText("Use stored rate")
        self.rate.setSuffix(" Hz")
        self.rate.setAccessibleName("Sampling rate for display only; zero uses stored metadata")
        self.rate.setToolTip("0 uses stored timing; if unavailable, the plot uses sample numbers. This setting changes display only.")
        options.addWidget(self.rate)
        self.refresh_button = QPushButton("Update plot")
        self.refresh_button.clicked.connect(self.refresh_plot)
        options.addWidget(self.refresh_button)
        options.addStretch()
        wl.addWidget(self.display_options)
        self.matrix_options = QWidget()
        matrix = QHBoxLayout(self.matrix_options)
        matrix.setContentsMargins(0, 0, 0, 0)
        matrix.addWidget(QLabel("For 2D datasets"))
        self.axis = QComboBox()
        self.axis.addItems(["Samples in rows", "Samples in columns"])
        self.axis.setAccessibleName("Sample axis for a two-dimensional dataset")
        matrix.addWidget(self.axis)
        matrix.addWidget(QLabel("Component"))
        self.component = QSpinBox()
        self.component.setRange(1, 1000000)
        self.component.setAccessibleName("One-based matrix component to display")
        matrix.addWidget(self.component)
        matrix.addStretch()
        wl.addWidget(self.matrix_options)
        plot_scroll = QScrollArea()
        plot_scroll.setWidgetResizable(True)
        self.plot_body = QWidget()
        self.plot_layout = QVBoxLayout(self.plot_body)
        self.plot_layout.setContentsMargins(0, 0, 0, 0)
        self.empty_plot = QLabel("Select a record or numeric waveform dataset from the file structure.")
        self.empty_plot.setWordWrap(True)
        self.plot_layout.addWidget(self.empty_plot)
        self.plot_layout.addStretch()
        plot_scroll.setWidget(self.plot_body)
        plot_scroll.setMinimumHeight(270)
        wl.addWidget(plot_scroll, 1)
        self.plot_info = QLabel("Drag to pan; scroll over a plot to zoom. Select Fit waveforms to reset the view.")
        self.plot_info.setWordWrap(True)
        self.plot_info.setMinimumHeight(42)
        wl.addWidget(self.plot_info)
        buttons = QHBoxLayout()
        self.fit_button = QPushButton("Fit waveforms")
        self.fit_button.clicked.connect(self.fit_plots)
        buttons.addWidget(self.fit_button)
        self.export_button = QPushButton("Save plot image")
        self.export_button.clicked.connect(self.export_plot)
        buttons.addWidget(self.export_button)
        buttons.addStretch()
        wl.addLayout(buttons)
        self.tabs.addTab(waveform_page, "Waveforms")
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Stored HDF5 metadata and attribute locations")
        self.tabs.addTab(self.details, "Stored metadata")
        rl.addWidget(self.tabs)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([320, 700])
        split.setMinimumHeight(440)
        layout.addWidget(split, 1)
        self.enable_controls()
        self.matrix_options.hide()
        self.tree.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        configure_accessibility(self)

    def busy(self):
        return self.worker is not None

    def enable_controls(self):
        idle = not self.busy()
        self.open_button.setEnabled(idle)
        self.reload_button.setEnabled(idle and bool(self.filename))
        for control in (self.tree, self.go_button, self.path_edit, self.display_options, self.matrix_options):
            control.setEnabled(idle and bool(self.filename))
        self.fit_button.setEnabled(idle and bool(self.series))
        self.export_button.setEnabled(idle and bool(self.series))

    def submit(self, function, callback):
        if self.busy():
            return
        worker = ReadWork(function, self)
        self.worker = worker
        self.enable_controls()
        self.status.setText("Reading HDF5 data…")
        worker.finished.connect(lambda: self.finished(worker, callback))
        worker.start()

    def finished(self, worker, callback):
        self.worker = None
        try:
            if worker.error:
                self.clear_plots()
                self.details.setPlainText(worker.error)
                self.status.setText("Unable to read: " + worker.error +
                                    "\nIf another program is updating this file, let it finish and select Reload.")
            else:
                callback(worker.output)
        except Exception as exc:
            self.status.setText("Reader display error: " + str(exc))
        finally:
            worker.deleteLater()
            self.enable_controls()

    def choose_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Open HDF5 file", str(Path(self.filename).parent) if self.filename else "",
                                                "HDF5 files (*.h5 *.hdf5 *.hdf *.he5);;All files (*)")
        if filename:
            self.open_file(filename)

    def open_file(self, filename):
        if not filename or self.busy():
            return
        self.filename = str(filename)
        self.filename_edit.setText(self.filename)
        self.tree.blockSignals(True)
        self.tree.clear()
        self.tree_items.clear()
        self.tree.blockSignals(False)
        self.rate.setValue(0)
        self.axis.setCurrentIndex(0)
        self.component.setValue(1)
        self.selected_path = "/"
        self.path_edit.setText("/")
        self.clear_plots()
        self.details.clear()
        self.submit(lambda: inspect_node(filename, "/", plot=False), self.opened)

    def opened(self, result):
        self.tree.blockSignals(True)
        root = QTreeWidgetItem(self.tree, [Path(self.filename).name, result["summary"]])
        root.setData(0, Qt.UserRole, dict(path="/", kind="group"))
        self.tree_items["/"] = root
        self.add_children(root, result)
        root.setExpanded(True)
        self.tree.setCurrentItem(root)
        self.tree.blockSignals(False)
        self.present(result)

    def add_children(self, item, result):
        if not result["offset"]:
            item.takeChildren()
        else:
            for index in reversed(range(item.childCount())):
                data = item.child(index).data(0, Qt.UserRole) or {}
                if data.get("kind") == "more":
                    item.takeChild(index)
        for row in result["children"]:
            child = QTreeWidgetItem(item, [row["name"], row["detail"]])
            child.setData(0, Qt.UserRole, row)
            child.setToolTip(0, row["path"])
            self.tree_items[row["path"]] = child
            if row["kind"] == "group":
                QTreeWidgetItem(child, ["Expand to read…"])
        if result["more"] is not None:
            more = QTreeWidgetItem(item, ["Load next 250 items…"])
            more.setData(0, Qt.UserRole, dict(kind="more", path=result["path"], offset=result["more"]))
        item.setData(1, Qt.UserRole, True)

    def options(self):
        return dict(sample_axis=self.axis.currentIndex(), component=self.component.value() - 1,
                    override_hz=self.rate.value())

    def expand_item(self, item):
        if not item.data(1, Qt.UserRole):
            self.select_item(item)

    def select_item(self, item, previous=None):
        if item is None or self.busy():
            return
        data = item.data(0, Qt.UserRole) or {}
        kind = data.get("kind")
        if kind not in ("group", "dataset", "more"):
            self.clear_plots()
            self.status.setText(data.get("detail", "Select a group or dataset."))
            self.enable_controls()
            return
        path = data["path"]
        self.selected_path = path
        self.path_edit.setText(path)
        options = self.options()
        offset = data.get("offset", 0)
        parent = item.parent() if kind == "more" else item
        def loaded(result):
            if kind in ("group", "more"):
                self.tree.blockSignals(True)
                self.add_children(parent, result)
                self.tree.setCurrentItem(parent)
                parent.setExpanded(True)
                self.tree.blockSignals(False)
            self.present(result)
        self.submit(lambda: inspect_node(self.filename, path, offset=offset, **options), loaded)

    def go_to_path(self):
        if self.busy() or not self.filename:
            return
        path = "/" + self.path_edit.text().strip().lstrip("/")
        self.selected_path = path
        options = self.options()
        self.submit(lambda: inspect_node(self.filename, path, **options), self.present)

    def refresh_plot(self):
        if self.filename and not self.busy():
            options = self.options()
            self.submit(lambda: inspect_node(self.filename, self.selected_path, **options), self.present)

    def clear_plots(self):
        for plot in self.plots:
            self.plot_layout.removeWidget(plot)
            plot.deleteLater()
        self.plots.clear()
        self.series = []
        self.empty_plot.show()

    def present(self, result):
        self.selected_path = result["path"]
        self.clear_plots()
        self.details.setPlainText(result["path"] + "\n" + result["summary"] + "\n\n" + result["details"])
        self.series = result["series"]
        colors = ["#455a73", "#175fa3", "#147d70", "#8750a2"]
        descriptions = [result["path"] + " · " + result["summary"]]
        for index, series in enumerate(self.series):
            plot = pg.PlotWidget(background="w")
            plot.setMinimumHeight(180)
            plot.setAccessibleName(series["path"] + " waveform plot")
            plot.setTitle(escape(Path(series["path"]).name.replace("_", " ")))
            plot.showGrid(x=True, y=True, alpha=.18)
            plot.setLabel("left", series["kind"].title(), units=series["units"] if series["units"] != "unknown" else "unspecified units")
            plot.getAxis("left").enableAutoSIPrefix(False)
            plot.setLabel("bottom", "Time from first stored sample" if series["dt"] else "Sample index (zero-based)",
                          units="s" if series["dt"] else None)
            curve = plot.plot(series["x"], series["y"], pen=pg.mkPen(colors[index % len(colors)], width=1), connect="finite")
            curve.setClipToView(True)
            # Only link axes with the same unit. Missing timing must not align
            # sample indices with seconds from another dataset.
            for earlier, other in zip(self.plots, self.series[:index]):
                if bool(other["dt"]) == bool(series["dt"]):
                    plot.setXLink(earlier); break
            self.plot_layout.insertWidget(self.plot_layout.count() - 1, plot)
            self.plots.append(plot)
            detail = f'{Path(series["path"]).name}: {series["samples"]:,} samples · {series["timing"]}'
            if series["component"] is not None:
                detail += f' · component {series["component"]}'
            if series["reduced"]:
                detail += " · min/max display preview"
            descriptions.append(detail)
            self.details.appendPlainText(f'\n{series["path"]}\nStored record start: {series["start"]}\nUnits: {series["units"]}\n{detail}')
        self.empty_plot.setVisible(not self.series)
        self.matrix_options.setVisible(result["has_matrix"]
                                       or bool(result["shape"] and len(result["shape"]) == 2))
        if not self.series and not result["notes"]:
            descriptions.append("Expand the group to select a record or waveform dataset.")
        descriptions.extend(result["notes"])
        self.status.setText("\n".join(descriptions))
        self.enable_controls()

    def fit_plots(self):
        for plot in self.plots:
            plot.enableAutoRange()

    def export_plot(self):
        if not self.series:
            return
        filename, _ = QFileDialog.getSaveFileName(self, "Save Reader plot", "waveforms.png", "PNG image (*.png);;PDF figure (*.pdf)")
        if not filename:
            return
        try:
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            figure = Figure(figsize=(12, max(3, len(self.series) * 2.6)), layout="constrained")
            FigureCanvasAgg(figure)
            axes = figure.subplots(len(self.series), 1, squeeze=False)
            for axis, series in zip(axes[:, 0], self.series):
                axis.plot(series["x"], series["y"], linewidth=.6)
                axis.set_title(series["path"], fontsize=9)
                axis.set_ylabel(series["units"])
                axis.set_xlabel("Time from first stored sample (s)" if series["dt"] else "Sample index")
                axis.grid(alpha=.2)
            figure.suptitle(Path(self.filename).name + (" · display preview" if any(s["reduced"] for s in self.series) else ""))
            figure.savefig(filename, dpi=160)
            self.status.setText("Saved plot image: " + filename)
        except Exception as exc:
            QMessageBox.warning(self, "Could not save plot", str(exc))
