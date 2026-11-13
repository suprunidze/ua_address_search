"""Choose shared access or a privately held, direct Visicom key."""

from qgis.PyQt.QtCore import QEvent
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QLayout,
    QMessageBox,
    QSpinBox,
    QWidget,
)


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None, initial_mode=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Visicom Data API for QGIS — налаштування")
        layout = QFormLayout(self)
        constraints = getattr(QLayout, "SizeConstraint", QLayout)
        layout.setSizeConstraint(constraints.SetNoConstraint)
        self.setSizeGripEnabled(False)
        wrapping = getattr(QFormLayout, "RowWrapPolicy", QFormLayout)
        layout.setRowWrapPolicy(wrapping.WrapLongRows)
        self.mode = QComboBox()
        self.mode.addItem("Спільний доступ Railway", "railway")
        self.mode.addItem("Власний ключ Visicom API", "visicom")
        self.mode.setCurrentIndex(1 if (initial_mode or settings.mode()) == "visicom" else 0)
        layout.addRow("Режим доступу", self.mode)
        self.railway_info = QLabel(
            "Автоматична реєстрація цієї інсталяції. Добова квота обмежена; "
            "ключ або токен вводити не потрібно."
        )
        self.railway_info.setWordWrap(True)
        layout.addRow(self.railway_info)
        self.direct = QWidget()
        form = QFormLayout(self.direct)
        form.setRowWrapPolicy(wrapping.WrapLongRows)
        form.setContentsMargins(0, 0, 0, 0)
        self.key = QLineEdit()
        echo = getattr(QLineEdit, "EchoMode", QLineEdit)
        self.key.setEchoMode(echo.Password)
        self.key.setPlaceholderText("Введіть ключ або залиште збережений")
        form.addRow("Ключ Visicom API", self.key)
        self.show_quota = QCheckBox("Показувати залишок добової квоти")
        self.show_quota.setChecked(settings.show_quota())
        form.addRow(self.show_quota)
        self.limit = QSpinBox()
        self.limit.setRange(1, 1_000_000_000)
        self.limit.setValue(settings.quota_limit())
        self.limit.setEnabled(self.show_quota.isChecked())
        self.show_quota.toggled.connect(self.limit.setEnabled)
        form.addRow("Запитів на добу", self.limit)
        info = QLabel("Локальна оцінка цього плагіна на комп’ютері. Перевищення не блокує запити.")
        info.setWordWrap(True)
        form.addRow(info)
        layout.addRow(self.direct)
        security = QLabel("Секрети захищає QGIS Authentication Manager. Код: MIT. Дані: Visicom.")
        security.setWordWrap(True)
        layout.addRow(security)
        standard = getattr(QDialogButtonBox, "StandardButton", QDialogButtonBox)
        buttons = QDialogButtonBox(standard.Save | standard.Cancel)
        buttons.button(standard.Save).setText("Зберегти")
        buttons.button(standard.Cancel).setText("Скасувати")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)
        self.mode.currentIndexChanged.connect(self.mode_changed)
        self.mode_changed()

    def mode_changed(self, index=None):
        direct = self.mode.currentData() == "visicom"
        self.direct.setVisible(direct)
        self.railway_info.setVisible(not direct)
        self.fit_contents()

    def fit_contents(self):
        layout = self.layout()
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        layout.invalidate()
        layout.activate()
        width = max(500, layout.minimumSize().width())
        height = (
            layout.totalHeightForWidth(width)
            if layout.hasHeightForWidth()
            else layout.sizeHint().height()
        )
        self.setFixedSize(width, max(height, layout.minimumSize().height()))

    def changeEvent(self, event):
        super().changeEvent(event)
        kinds = getattr(QEvent, "Type", QEvent)
        if event.type() in (kinds.FontChange, kinds.StyleChange) and self.layout() is not None:
            self.fit_contents()

    def save(self):
        try:
            self.settings.save_mode(
                self.mode.currentData(),
                self.key.text().strip(),
                self.limit.value(),
                self.show_quota.isChecked(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Налаштування", str(exc))
            return
        self.key.clear()
        self.accept()
