"""Compact native dock with automatic geometry and accessible theme actions."""

from qgis.PyQt.QtCore import Qt, pyqtSignal, QSignalBlocker
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
    QTabWidget,
)
from qgis.core import QgsApplication
from .function_tabs import FunctionPage, TITLES, OPERATIONS


class SearchDock(QDockWidget):
    search_requested = pyqtSignal(str)
    selection_requested = pyqtSignal(int)
    download_requested = pyqtSignal(int)
    reverse_requested = pyqtSignal(bool)
    settings_requested = pyqtSignal()
    cancel_requested = pyqtSignal()
    help_requested = pyqtSignal()
    geometry_toggled = pyqtSignal(bool)
    quota_changed = pyqtSignal()
    access_error = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__("Visicom Data API for QGIS", parent)
        self.setObjectName("VisicomDataApiDock")
        body = QWidget(self)
        outer = QVBoxLayout(body)
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setDocumentMode(True)
        outer.addWidget(self.tabs)
        self.address_page = QWidget()
        self.tabs.addTab(self.address_page, TITLES[0])
        layout = QVBoxLayout(self.address_page)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        row = QHBoxLayout()
        self.search_row = row
        self.reverse_button = self.button("Найближча адреса за кліком", "mActionIdentify.svg")
        self.reverse_button.setCheckable(True)
        row.addWidget(self.reverse_button)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Київ, Хрещатик, 26 • Locator: адр адреса")
        self.query.setAccessibleName("Адреса для пошуку")
        self.search_button = QPushButton("Знайти")
        row.addWidget(self.query)
        row.addWidget(self.search_button)
        self.cancel_button = self.button("Скасувати", "mTaskCancel.svg")
        self.cancel_button.setEnabled(False)
        row.addWidget(self.cancel_button)
        layout.addLayout(row)
        self.results = QListWidget()
        self.results.setAccessibleName("Результати адресного пошуку")
        layout.addWidget(self.results)
        self.status = QLabel("Введіть адресу та натисніть Enter.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.auto_geometry = QCheckBox("Автоматично отримувати геометрію")
        self.auto_geometry.setChecked(True)
        self.auto_geometry.setToolTip(
            "Завантажувати повну геометрію вибраного результату. "
            "Перше завантаження може витратити додатковий запит API."
        )
        layout.addWidget(self.auto_geometry)
        row = QHBoxLayout()
        self.action_row = row
        self.download_button = self.button("Завантажити геометрію", "downloading_svg.svg", True)
        self.copy_button = self.button("Копіювати адресу", "mActionEditCopy.svg")
        row.addWidget(self.download_button)
        row.addWidget(self.copy_button)
        row.addStretch()
        self.settings_button = self.button("Налаштування", "mActionOptions.svg", True)
        self.help_button = self.button("Довідка", "mActionHelpContents.svg")
        layout.addLayout(row)
        self.download_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.quota = QLineEdit("Квота: ще немає даних")
        self.quota.setReadOnly(True)
        self.quota.setFrame(False)
        self.quota.setAccessibleName("Використання та залишок квоти")
        self.pages = {}
        for operation, title in zip(OPERATIONS[1:], TITLES[1:]):
            page = FunctionPage(operation, title)
            self.pages[operation] = page
            self.tabs.addTab(page, title)
        self.footer = QHBoxLayout()
        self.footer.addWidget(self.quota, 1)
        self.footer.addWidget(self.settings_button)
        self.footer.addWidget(self.help_button)
        outer.addLayout(self.footer)
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
        self.reverse_button.toggled.connect(self.reverse_requested.emit)
        self.settings_button.clicked.connect(self.settings_requested.emit)
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.help_button.clicked.connect(self.help_requested.emit)
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

    @staticmethod
    def button(title, icon, show_text=False):
        item = QToolButton()
        item.setText(title)
        item.setToolTip(title)
        item.setAccessibleName(title)
        item.setIcon(QgsApplication.getThemeIcon("/" + icon))
        style = getattr(Qt, "ToolButtonStyle", Qt)
        item.setToolButtonStyle(
            style.ToolButtonTextBesideIcon if show_text else style.ToolButtonIconOnly
        )
        return item

    def set_reverse_active(self, active):
        blocker = QSignalBlocker(self.reverse_button)
        self.reverse_button.setChecked(active)
        del blocker

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
