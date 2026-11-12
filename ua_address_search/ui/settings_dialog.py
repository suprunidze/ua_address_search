"""Server, encrypted token, and MIT/provider attribution."""

from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
)


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("UA Address Search — налаштування")
        self.resize(490, 220)
        layout = QFormLayout(self)
        self.url = QLineEdit(settings.url())
        self.url.setPlaceholderText("https://your-service.up.railway.app")
        self.token = QLineEdit()
        mode = getattr(QLineEdit, "Password", None)
        if mode is None:
            mode = QLineEdit.EchoMode.Password
        self.token.setEchoMode(mode)
        self.token.setPlaceholderText("Залиште порожнім, щоб зберегти поточний")
        layout.addRow("URL сервера", self.url)
        layout.addRow("Персональний токен", self.token)
        info = QLabel(
            "Токен захищається головним паролем QGIS Authentication Manager.\n"
            "Ключ Visicom налаштовує адміністратор сервера.\n"
            "Код плагіна: MIT (текст у LICENSE). Дані: Visicom та правовласники."
        )
        info.setWordWrap(True)
        layout.addRow(info)
        standard = getattr(QDialogButtonBox, "StandardButton", QDialogButtonBox)
        buttons = QDialogButtonBox(standard.Save | standard.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def save(self):
        try:
            self.settings.save(self.url.text(), self.token.text().strip())
        except ValueError as exc:
            QMessageBox.warning(self, "Налаштування", str(exc))
            return
        self.token.clear()
        self.accept()
