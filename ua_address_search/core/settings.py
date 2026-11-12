"""Profile settings and encrypted token storage in QGIS Authentication Manager."""

from dataclasses import dataclass, field
import ipaddress
import secrets
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from qgis.core import QgsApplication, QgsAuthMethodConfig, QgsSettings
from qgis.PyQt.QtCore import QStandardPaths

PREFIX = "ua_address_search/"
DEFAULT_SERVER = "https://uaaddresssearch-production.up.railway.app"
VISICOM_URL = "https://api.visicom.ua/data-api/5.0/uk"
LOCATOR_PREFIX_KEYS = (
    "gui/locator_filters/prefix_ua_address_search",
    "locator-filters/items/ua_address_search/prefix",
)


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
    mode: str = "railway"
    installation_id: str = ""
    quota_limit: int = 1500
    show_quota: bool = True
    usage_path: str = ""

    @property
    def identity(self):
        from .cache import namespace

        return namespace(
            self.url if self.mode == "railway" else self.mode + ":" + self.url, self.token
        )


class Settings:
    def __init__(self):
        self.values = QgsSettings()

    def migrate_locator_prefix(self) -> None:
        for key in LOCATOR_PREFIX_KEYS:
            if self.values.value(key, "") == "uas":
                self.values.setValue(key, "адр")

    def url(self) -> str:
        return str(self.values.value(PREFIX + "server_url", DEFAULT_SERVER))

    def mode(self) -> str:
        return str(self.values.value(PREFIX + "mode", "railway"))

    def auto_geometry(self) -> bool:
        return self.values.value(PREFIX + "auto_geometry", True, type=bool)

    def set_auto_geometry(self, value: bool) -> None:
        self.values.setValue(PREFIX + "auto_geometry", value)

    def quota_limit(self) -> int:
        return self.values.value(PREFIX + "direct_quota_limit", 1500, type=int)

    def show_quota(self) -> bool:
        return self.values.value(PREFIX + "direct_show_quota", True, type=bool)

    def credentials(self) -> Credentials:
        direct = self.mode() == "visicom"
        key = "visicom_authcfg" if direct else "authcfg"
        config_id = str(self.values.value(PREFIX + key, ""))
        if not config_id:
            if direct:
                return Credentials(VISICOM_URL, mode="visicom")
            identifier = str(self.values.value(PREFIX + "installation_id", "")) or str(uuid.uuid4())
            self.values.setValue(PREFIX + "installation_id", identifier)
            self._store_secret("authcfg", self.url(), secrets.token_urlsafe(32))
            self.values.sync()
            config_id = str(self.values.value(PREFIX + key, ""))
        token = self._load_secret(config_id)
        if direct:
            location = getattr(QStandardPaths, "GenericDataLocation", None)
            if location is None:
                location = QStandardPaths.StandardLocation.GenericDataLocation
            path = (
                Path(QStandardPaths.writableLocation(location))
                / "UAAddressSearch"
                / "usage.sqlite3"
            )
            return Credentials(
                VISICOM_URL,
                token,
                "visicom",
                quota_limit=self.quota_limit(),
                show_quota=self.show_quota(),
                usage_path=str(path),
            )
        return Credentials(
            self.url(),
            token,
            installation_id=str(self.values.value(PREFIX + "installation_id", "")),
        )

    def _load_secret(self, config_id):
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
        return config.config("password")

    def _store_secret(self, key, url, token):
        if len(token) > 512 or not token or any(char.isspace() for char in token):
            raise ValueError("Некоректний ключ або токен.")
        manager = QgsApplication.authManager()
        if not manager.setMasterPassword(True):
            raise ValueError("Не вдалося розблокувати сховище QGIS.")
        config = QgsAuthMethodConfig()
        config.setMethod("Basic")
        config.setName(
            "UA Address Search — " + ("Visicom" if key == "visicom_authcfg" else "Railway")
        )
        config.setUri(url)
        config.setConfig("username", "uaas")
        config.setConfig("password", token)
        config_id = str(self.values.value(PREFIX + key, ""))
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
            raise ValueError("Не вдалося зберегти секрет в Authentication Manager.")
        self.values.setValue(PREFIX + key, config.id())

    def save_mode(self, mode, key="", quota_limit=1500, show_quota=True):
        if mode not in ("railway", "visicom") or not 1 <= quota_limit <= 1_000_000_000:
            raise ValueError("Некоректні налаштування доступу.")
        if mode == "visicom":
            if key:
                self._store_secret("visicom_authcfg", VISICOM_URL, key)
            elif not self.values.value(PREFIX + "visicom_authcfg", ""):
                raise ValueError("Введіть власний ключ Visicom API.")
        self.values.setValue(PREFIX + "mode", mode)
        self.values.setValue(PREFIX + "direct_quota_limit", quota_limit)
        self.values.setValue(PREFIX + "direct_show_quota", show_quota)

    def save(self, url: str, token: str = "") -> None:
        url = validate_url(url)
        old_url = self.url()
        config_id = str(self.values.value(PREFIX + "authcfg", ""))
        if old_url and old_url != url and not token:
            raise ValueError("Для нового сервера введіть токен повторно.")
        if not token and not config_id:
            raise ValueError("Введіть персональний токен доступу.")
        if token:
            self._store_secret("authcfg", url, token)
            self.values.remove(PREFIX + "installation_id")
        self.values.setValue(PREFIX + "server_url", url)

    def visible(self) -> bool:
        return self.values.value(PREFIX + "visible", False, type=bool)

    def set_visible(self, value: bool) -> None:
        self.values.setValue(PREFIX + "visible", value)
