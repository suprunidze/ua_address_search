"""Compact native dock with automatic geometry and accessible theme actions."""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QKeySequence

try:
    from qgis.PyQt.QtGui import QAction
except ImportError:
    from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from qgis.core import QgsApplication


class SearchDock(QDockWidget):
    search_requested = pyqtSignal(str)
    selection_requested = pyqtSignal(int)
    download_requested = pyqtSignal(int)
    reverse_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    geometry_toggled = pyqtSignal(bool)
    quota_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("UA Address Search", parent)
        self.setObjectName("UAAddressSearchDock")
        body = QWidget(self)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
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
        self.auto_geometry = QCheckBox("Автоматично отримувати геометрію")
        self.auto_geometry.setChecked(True)
        self.auto_geometry.setToolTip(
            "Завантажувати повну геометрію вибраного результату. "
            "Перше завантаження може витратити додатковий запит API."
        )
        layout.addWidget(self.auto_geometry)
        row = QHBoxLayout()

        def button(title, icon):
            item = QToolButton()
            item.setText(title)
            item.setToolTip(title)
            item.setAccessibleName(title)
            item.setIcon(QgsApplication.getThemeIcon("/" + icon))
            row.addWidget(item)
            return item

        self.download_button = button("Завантажити геометрію в шар", "mActionFileSave.svg")
        self.reverse_button = button("Найближча адреса за кліком", "mActionIdentify.svg")
        self.copy_button = button("Копіювати адресу", "mActionEditCopy.svg")
        self.cancel_button = button("Скасувати", "mTaskCancel.svg")
        row.addStretch()
        self.settings_button = button("Налаштування", "mActionOptions.svg")
        layout.addLayout(row)
        self.download_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.status = QLabel("Введіть адресу та натисніть Enter.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.quota = QLineEdit("Квота: ще немає даних")
        self.quota.setReadOnly(True)
        self.quota.setFrame(False)
        self.quota.setAccessibleName("Використання та залишок квоти")
        layout.addWidget(self.quota)
        layout.addWidget(QLabel("Дані: Visicom • Код плагіна: MIT"))
        self.setWidget(body)
        self.search_button.clicked.connect(self.submit)
        self.query.returnPressed.connect(self.submit)
        self.results.currentRowChanged.connect(self.row_changed)
        self.results.itemActivated.connect(
            lambda item: self.selection_requested.emit(self.results.row(item))
        )
        self.download_button.clicked.connect(
            lambda: self.download_requested.emit(self.results.currentRow())
        )
        self.reverse_button.clicked.connect(self.reverse_requested.emit)
        self.settings_button.clicked.connect(self.settings_requested.emit)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.auto_geometry.toggled.connect(self.geometry_toggled.emit)
        self.copy_button.clicked.connect(self.copy_address)
        self.copy_action = QAction("Копіювати адресу", self.results)
        keys = getattr(QKeySequence, "StandardKey", QKeySequence)
        self.copy_action.setShortcuts(QKeySequence.keyBindings(keys.Copy))
        shortcut = getattr(Qt, "ShortcutContext", Qt)
        self.copy_action.setShortcutContext(shortcut.WidgetShortcut)
        self.copy_action.triggered.connect(self.copy_address)
        self.results.addAction(self.copy_action)
        policy = getattr(Qt, "ContextMenuPolicy", Qt)
        self.results.setContextMenuPolicy(policy.CustomContextMenu)
        self.results.customContextMenuRequested.connect(self.context_menu)
        self.copy_action.setEnabled(False)

    def context_menu(self, position):
        item = self.results.itemAt(position)
        if item is not None:
            self.results.setCurrentItem(item)
        menu = QMenu(self.results)
        menu.addAction(self.copy_action)
        run = getattr(menu, "exec", None) or menu.exec_
        run(self.results.viewport().mapToGlobal(position))

    def copy_address(self):
        item = self.results.currentItem()
        if item is not None:
            QApplication.clipboard().setText(item.text())

    def row_changed(self, row):
        for item in (self.download_button, self.copy_button, self.copy_action):
            item.setEnabled(row >= 0)
        self.selection_requested.emit(row)

    def submit(self):
        self.search_requested.emit(self.query.text().strip())

    def show_results(self, records, nearest=False):
        self.results.clear()
        for record in records:
            self.results.addItem(record.label)
            item = self.results.item(self.results.count() - 1)
            item.setToolTip(f"{record.category}\n{record.attribution}")
        prefix = "Найближча адреса: " if nearest else "Знайдено: "
        self.status.setText(prefix + str(len(records)) if records else "Нічого не знайдено.")

    def busy(self, active):
        self.cancel_button.setEnabled(active)
