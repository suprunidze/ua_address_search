"""Non-modal guidance when shared access reaches its daily quota."""

from qgis.PyQt.QtCore import QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

REGISTRATION_URL = "https://api.visicom.ua/accounts/forms?action=register_form&lang=uk"


class QuotaDialog(QDialog):
    settings_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("UA Address Search — добову квоту вичерпано")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        self.message = QLabel(
            "Добову квоту спільного доступу вичерпано. Ви можете дочекатися наступної доби "
            "або отримати власний ключ Visicom і вказати його в налаштуваннях плагіна."
        )
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.registration = QLabel(
            '<a href="' + REGISTRATION_URL.replace("&", "&amp;") + '">'
            "Зареєструватися та отримати ключ Visicom</a>"
        )
        self.registration.setWordWrap(True)
        self.registration.setOpenExternalLinks(False)
        self.registration.linkActivated.connect(self.open_registration)
        layout.addWidget(self.registration)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        standard = getattr(QDialogButtonBox, "StandardButton", QDialogButtonBox)
        roles = getattr(QDialogButtonBox, "ButtonRole", QDialogButtonBox)
        buttons = QDialogButtonBox(standard.Close)
        buttons.button(standard.Close).setText("Закрити")
        self.settings_button = buttons.addButton("Налаштування", roles.ActionRole)
        self.settings_button.clicked.connect(self.settings_requested.emit)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.adjustSize()

    def open_registration(self, link):
        if not QDesktopServices.openUrl(QUrl(REGISTRATION_URL)):
            self.status.setText("Не вдалося відкрити сайт Visicom у браузері.")
