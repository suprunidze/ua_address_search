"""Profile settings and encrypted token storage in QGIS Authentication Manager."""

from dataclasses import dataclass, field
import ipaddress
from urllib.parse import urlsplit
from qgis.core import QgsApplication, QgsAuthMethodConfig, QgsSettings

PREFIX = "ua_address_search/"


def validate_url(value: str) -> str:
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("Некоректна адреса сервера.") from None
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Адреса сервера не повинна містити пароль, параметри або фрагмент.")
    local = parsed.hostname == "localhost"
    try:
        local = local or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        pass
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise ValueError("Потрібен HTTPS; HTTP дозволений лише для localhost.")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Некоректний порт.")
    return value


@dataclass(frozen=True)
class Credentials:
    url: str = ""
    token: str = field(default="", repr=False)


class Settings:
    def __init__(self):
        self.values = QgsSettings()

    def url(self) -> str:
        return str(self.values.value(PREFIX + "server_url", ""))

    def credentials(self) -> Credentials:
        config_id = str(self.values.value(PREFIX + "authcfg", ""))
        if not config_id:
            return Credentials(self.url())
        manager = QgsApplication.authManager()
        if not manager.setMasterPassword(True):
            raise ValueError("Не вдалося розблокувати сховище токена QGIS.")
        config = QgsAuthMethodConfig()
        loaded = manager.loadAuthenticationConfig(config_id, config, True)
        if isinstance(loaded, tuple):
            success, config = loaded
        else:
            success = loaded
        if not success:
            raise ValueError("Не вдалося прочитати токен. Відкрийте налаштування.")
        return Credentials(self.url(), config.config("password"))

    def save(self, url: str, token: str = "") -> None:
        url = validate_url(url)
        old_url = self.url()
        config_id = str(self.values.value(PREFIX + "authcfg", ""))
        if old_url and old_url != url and not token:
            raise ValueError("Для нового сервера введіть токен повторно.")
        if not token and not config_id:
            raise ValueError("Введіть персональний токен доступу.")
        if token:
            if len(token) > 512 or any(char.isspace() for char in token):
                raise ValueError("Некоректний токен.")
            manager = QgsApplication.authManager()
            if not manager.setMasterPassword(True):
                raise ValueError("Не вдалося розблокувати сховище токена QGIS.")
            config = QgsAuthMethodConfig()
            # Basic is used only as an encrypted credential container. HTTP uses Bearer.
            config.setMethod("Basic")
            config.setName("UA Address Search — персональний токен")
            config.setUri(url)
            config.setConfig("username", "uaas")
            config.setConfig("password", token)
            if config_id:
                config.setId(config_id)
                success = manager.updateAuthenticationConfig(config)
            else:
                stored = manager.storeAuthenticationConfig(config)
                if isinstance(stored, tuple):
                    success, config = stored
                else:
                    success = stored
            if not success:
                raise ValueError("Не вдалося зберегти токен в Authentication Manager.")
            self.values.setValue(PREFIX + "authcfg", config.id())
        self.values.setValue(PREFIX + "server_url", url)

    def visible(self) -> bool:
        return self.values.value(PREFIX + "visible", False, type=bool)

    def set_visible(self, value: bool) -> None:
        self.values.setValue(PREFIX + "visible", value)
