"""Native function pages and ordered, CRS-aware point input."""

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QScrollArea,
)
from qgis.core import QgsProject, QgsWkbTypes, QgsCsException
from ..core.models import point
from ..core.qgis_io import source_points

TITLES = ("Адреси", "Об’єкти", "Маршрут", "Матриця відстаней", "Оптимізація", "Прив’язка до доріг")
OPERATIONS = ("addresses", "objects", "route", "matrix", "optimize", "snap")


class PointEditor(QWidget):
    pick_requested = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, title, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(title + " — WGS84: довгота, широта")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["ID", "Довгота", "Широта"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(105)
        self.table.setMaximumHeight(165)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        for title, action in (
            ("+", self.add_empty),
            ("Клік", self.pick),
            ("Із шару", self.import_layer),
            ("↑", lambda: self.move(-1)),
            ("↓", lambda: self.move(1)),
            ("−", self.remove),
        ):
            button = QPushButton(title)
            button.setToolTip(
                {
                    "+": "Додати рядок",
                    "−": "Видалити рядок",
                    "↑": "Перемістити вгору",
                    "↓": "Перемістити вниз",
                }.get(title, title)
            )
            button.clicked.connect(action)
            row.addWidget(button)
        layout.addLayout(row)
        self.selected_only = QCheckBox("Із шару — лише виділені об’єкти")
        layout.addWidget(self.selected_only)

    def add_empty(self):
        self.append(None)

    def pick(self):
        self.pick_requested.emit(self)

    def append(self, coordinates, identifier=None):
        row = self.table.rowCount()
        if identifier is None:
            used = {
                self.table.item(i, 0).text()
                for i in range(row)
                if self.table.item(i, 0) is not None
            }
            number = 1
            while str(number) in used:
                number += 1
            identifier = str(number)
        self.table.insertRow(row)
        values = [identifier]
        values.extend(["", ""] if coordinates is None else [f"{c:.12g}" for c in coordinates])
        for col, value in enumerate(values):
            self.table.setItem(row, col, QTableWidgetItem(str(value)))
        self.table.setCurrentCell(row, 1)

    def remove(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def move(self, delta):
        row = self.table.currentRow()
        other = row + delta
        if row < 0 or not 0 <= other < self.table.rowCount():
            return
        for column in range(3):
            first = self.table.takeItem(row, column)
            second = self.table.takeItem(other, column)
            self.table.setItem(row, column, second)
            self.table.setItem(other, column, first)
        self.table.setCurrentCell(other, 1)

    def records(self):
        result = []
        for row in range(self.table.rowCount()):
            try:
                coords = point([float(self.table.item(row, c).text()) for c in (1, 2)])
                identifier = self.table.item(row, 0).text().strip()
            except (ValueError, AttributeError):
                raise ValueError(f"Рядок {row + 1}: введіть довготу та широту WGS84.") from None
            if not identifier or identifier in [r["source_id"] for r in result]:
                raise ValueError("ID точок мають бути непорожніми й унікальними.")
            result.append({"source_id": identifier, "point": list(coords)})
        return result

    def import_layer(self):
        layers = [
            layer
            for layer in QgsProject.instance().mapLayers().values()
            if hasattr(layer, "geometryType") and layer.geometryType() == QgsWkbTypes.PointGeometry
        ]
        if not layers:
            self.error.emit("У проєкті немає точкового шару.")
            return
        names = [layer.name() + " [" + layer.id() + "]" for layer in layers]
        name, ok = QInputDialog.getItem(self, "Точки", "Вхідний шар", names, 0, False)
        if not ok:
            return
        layer = layers[names.index(name)]
        fields = [field.name() for field in layer.fields()]
        if not fields:
            self.error.emit("Для імпорту потрібне унікальне поле порядку.")
            return
        field, ok = QInputDialog.getItem(
            self, "Порядок точок", "Унікальне поле порядку", fields, 0, False
        )
        if not ok:
            return
        try:
            records = source_points(layer, field, self.selected_only.isChecked())
        except (ValueError, RuntimeError, QgsCsException) as exc:
            self.error.emit(str(exc))
            return
        self.table.setRowCount(0)
        for record in records:
            self.append(record["point"], record["source_id"])


class FunctionPage(QWidget):
    execute_requested = pyqtSignal(str)
    cancel_requested = pyqtSignal()
    export_requested = pyqtSignal(str)
    pick_requested = pyqtSignal(object)
    route_requested = pyqtSignal()

    def __init__(self, operation, title, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.title = title
        self.editors = {}
        self.last_result = None
        self.last_request = None
        self.last_ids = {}
        self.output_records = []
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        layout = QVBoxLayout(body)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        if operation == "objects":
            self.categories = QComboBox()
            self.categories.setEditable(True)
            self.categories.addItems(
                [
                    "adm_settlement",
                    "adr_address",
                    "adr_street",
                    "poi_restaurant",
                    "adm_level1",
                    "adm_level2",
                ]
            )
            self.categories.setToolTip("Коди категорій Visicom через кому; можна ввести власні.")
            form.addRow("Категорії", self.categories)
            self.text = QLineEdit()
            form.addRow("Текст (необов’язково)", self.text)
            self.ids = QLineEdit()
            self.ids.setPlaceholderText("ID через кому; отримання об’єктів замість пошуку")
            form.addRow("Отримати за ID", self.ids)
            self.limit = QSpinBox()
            self.limit.setRange(1, 250)
            self.limit.setValue(100)
            form.addRow("Ліміт результатів", self.limit)
            self.scope = QComboBox()
            self.scope.addItems(
                [
                    "Україна",
                    "Радіус від точки",
                    "Охоплення карти",
                    "Виділений полігон активного шару",
                ]
            )
            form.addRow("Область пошуку", self.scope)
            self.radius = QSpinBox()
            self.radius.setRange(1, 20_000_000)
            self.radius.setValue(1000)
            form.addRow("Радіус, м", self.radius)
        else:
            self.mode = QComboBox()
            values = (
                [("Автомобільні дороги", "driving"), ("Усі дороги", "walking")]
                if operation == "snap"
                else [
                    ("Автомобільний оптимальний", "driving"),
                    ("Автомобільний найкоротший", "driving-shortest"),
                    ("По прямій", "direct"),
                ]
            )
            for label, value in values:
                self.mode.addItem(label, value)
            form.addRow("Режим", self.mode)
        if operation == "optimize":
            self.round_trip = QCheckBox("Повернутися до старту")
            self.round_trip.setChecked(True)
            form.addRow(self.round_trip)
        if operation == "snap":
            self.separate = QCheckBox("Прив’язувати точки незалежно")
            self.interpolate = QCheckBox("Повернути лінію вздовж доріг")
            self.separate.toggled.connect(lambda active: self.interpolate.setEnabled(not active))
            form.addRow(self.separate)
            form.addRow(self.interpolate)
        layout.addLayout(form)
        editors = {
            "objects": [("near", "Центр радіуса (одна точка)")],
            "matrix": [
                ("origins", "Початкові точки — максимум 25"),
                ("destinations", "Кінцеві точки — максимум 25"),
            ],
            "route": [("points", "Старт → проміжні точки → фініш (до 27)")],
            "optimize": [("points", "Пункти відвідування — до 50")],
            "snap": [("points", "Точки треку / незалежні точки — до 250")],
        }[operation]
        if operation in ("route", "matrix", "optimize"):
            editors = editors + [("locks", "Блокування проїздів (необов’язково)")]
        for key, label in editors:
            editor = PointEditor(label)
            self.editors[key] = editor
            editor.pick_requested.connect(self.pick_requested.emit)
            editor.error.connect(lambda message: self.status.setText(message))
            layout.addWidget(editor)
        if operation == "objects":
            self.editors["near"].setVisible(False)
            self.radius.setEnabled(False)
            self.scope.currentIndexChanged.connect(self.scope_changed)
        scroll.setWidget(body)
        outer.addWidget(scroll)
        row = QHBoxLayout()
        self.execute_button = QPushButton("Виконати")
        self.cancel_button = QPushButton("Скасувати")
        self.cancel_button.setEnabled(False)
        self.export_button = QPushButton("Додати результат у проєкт")
        self.export_button.setEnabled(False)
        row.addWidget(self.execute_button)
        row.addWidget(self.cancel_button)
        row.addWidget(self.export_button)
        outer.addLayout(row)
        if operation == "optimize":
            self.route_button = QPushButton("Побудувати дорожню лінію — додатковий запит")
            self.route_button.setEnabled(False)
            self.route_button.clicked.connect(self.route_requested.emit)
            outer.addWidget(self.route_button)
        self.status = QLabel("Налаштуйте параметри. Автоматичного розбиття запитів немає.")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        self.results = QTableWidget()
        self.results.setMinimumHeight(100)
        self.results.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        outer.addWidget(self.results)
        self.execute_button.clicked.connect(lambda: self.execute_requested.emit(operation))
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.export_button.clicked.connect(lambda: self.export_requested.emit(operation))

    def scope_changed(self, index):
        self.editors["near"].setVisible(index == 1)
        self.radius.setEnabled(index == 1)

    def parameters(self):
        if self.operation == "objects":
            identifiers = [i.strip() for i in self.ids.text().split(",") if i.strip()]
            if identifiers:
                return "features", {"ids": identifiers}, {}
            data = {
                "categories": [
                    c.strip() for c in self.categories.currentText().split(",") if c.strip()
                ],
                "text": self.text.text(),
                "limit": self.limit.value(),
            }
            if self.scope.currentIndex() == 1:
                records = self.editors["near"].records()
                if len(records) != 1:
                    raise ValueError("Для радіуса потрібна рівно одна центральна точка.")
                data.update(near=records[0]["point"], radius=self.radius.value())
            return "objects", data, {}
        data = {"mode": self.mode.currentData()}
        ids = {}
        for key, editor in self.editors.items():
            records = editor.records()
            data[key] = [r["point"] for r in records]
            ids[key] = [r["source_id"] for r in records]
        if self.operation == "optimize":
            data["round_trip"] = self.round_trip.isChecked()
        if self.operation == "snap":
            data.update(
                separate=self.separate.isChecked(), interpolate=self.interpolate.isChecked()
            )
        return self.operation, data, ids

    def show_records(self, records):
        self.output_records = list(records)
        columns = sorted({key for record in self.output_records for key in record["attributes"]})
        self.results.clear()
        self.results.setColumnCount(len(columns))
        self.results.setHorizontalHeaderLabels(columns)
        self.results.setRowCount(len(self.output_records))
        for row, record in enumerate(self.output_records):
            for col, key in enumerate(columns):
                value = record["attributes"].get(key)
                item = QTableWidgetItem("" if value is None else str(value))
                self.results.setItem(row, col, item)
        self.export_button.setEnabled(bool(self.output_records))
