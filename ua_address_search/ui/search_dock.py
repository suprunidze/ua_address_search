"""Native QGIS dock; no embedded browser or bundled Qt dependency."""

from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SearchDock(QDockWidget):
    search_requested = pyqtSignal(str)
    selection_requested = pyqtSignal(int)
    geometry_requested = pyqtSignal(int)
    download_requested = pyqtSignal(int)
    reverse_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    quota_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("UA Address Search", parent)
        self.setObjectName("UAAddressSearchDock")
        body = QWidget(self)
        layout = QVBoxLayout(body)
        row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("Київ, Хрещатик, 26 • Locator: адр адреса")
        self.query.setAccessibleName("Адреса для пошуку")
        self.search_button = QPushButton("Знайти")
        row.addWidget(self.query)
        row.addWidget(self.search_button)
        layout.addLayout(row)
        self.results = QListWidget()
        self.results.setAccessibleName("Результати адресного пошуку")
        layout.addWidget(self.results)
        self.status = QLabel("Введіть адресу та натисніть Enter.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.geometry_button = QPushButton("Показати геометрію")
        self.geometry_button.setEnabled(False)
        self.download_button = QPushButton("Завантажити геометрію")
        self.download_button.setEnabled(False)
        self.reverse_button = QPushButton("Найближча адреса за кліком")
        self.cancel_button = QPushButton("Скасувати")
        self.cancel_button.setEnabled(False)
        for button in (
            self.geometry_button,
            self.download_button,
            self.reverse_button,
            self.cancel_button,
        ):
            layout.addWidget(button)
        row = QHBoxLayout()
        self.settings_button = QPushButton("Налаштування")
        self.quota_button = QPushButton("Квота")
        row.addWidget(self.settings_button)
        row.addWidget(self.quota_button)
        layout.addLayout(row)
        attribution = QLabel("Дані: Visicom • Код плагіна: MIT")
        attribution.setWordWrap(True)
        layout.addWidget(attribution)
        self.setWidget(body)
        self.search_button.clicked.connect(self.submit)
        self.query.returnPressed.connect(self.submit)
        self.results.itemActivated.connect(
            lambda item: self.selection_requested.emit(self.results.row(item))
        )
        self.results.currentRowChanged.connect(self.row_changed)
        self.geometry_button.clicked.connect(
            lambda: self.geometry_requested.emit(self.results.currentRow())
        )
        self.download_button.clicked.connect(
            lambda: self.download_requested.emit(self.results.currentRow())
        )
        self.reverse_button.clicked.connect(self.reverse_requested.emit)
        self.settings_button.clicked.connect(self.settings_requested.emit)
        self.quota_button.clicked.connect(self.quota_requested.emit)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)

    def row_changed(self, row):
        self.geometry_button.setEnabled(row >= 0)
        self.download_button.setEnabled(row >= 0)
        self.selection_requested.emit(row)

    def submit(self):
        self.search_requested.emit(self.query.text().strip())

    def show_results(self, records, nearest=False):
        self.results.clear()
        for record in records:
            self.results.addItem(record.label)
            self.results.item(self.results.count() - 1).setToolTip(
                f"{record.category}\n{record.attribution}"
            )
        prefix = "Найближча адреса: " if nearest else "Знайдено: "
        self.status.setText(prefix + str(len(records)) if records else "Нічого не знайдено.")

    def busy(self, active):
        self.cancel_button.setEnabled(active)
        if active:
            self.status.setText("Пошук…")
