"""Worker-safe proxy client using the QGIS network/proxy infrastructure."""

import json
import sqlite3
from urllib.parse import quote, urlencode
from qgis.PyQt.QtCore import (
    QUrl,
    QDateTime,
    QTimeZone,
    QEventLoop,
    QTimer,
    Qt,
    QCoreApplication,
    QEvent,
)
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
from qgis.core import QgsFeedback, QgsNetworkAccessManager, QgsTask, QgsMessageLog, Qgis
from .models import InvalidProviderData, SearchResult, normalize
from .cache import CacheCancelled
from .settings import validate_url
from .usage import LocalUsage

REQUEST_TIMEOUT_MS = 18000
MAX_RESPONSE_BYTES = 5_000_000

MESSAGES = {
    "unauthorized": "Доступ недійсний або відкликаний. Зверніться до адміністратора сервера.",
    "rate_limit": "Забагато запитів. Зачекайте хвилину.",
    "daily_quota": "Добову квоту вичерпано.",
    "provider_quota": "Visicom повідомив про вичерпання квоти.",
    "provider_timeout": "Visicom не відповів вчасно.",
    "provider_auth": "Серверний ключ Visicom недійсний. Зверніться до адміністратора.",
    "provider_not_configured": "Серверний ключ Visicom ще не налаштований.",
    "provider_unavailable": "Сервіс Visicom тимчасово недоступний.",
    "provider_invalid_response": "Visicom повернув некоректну відповідь.",
    "invalid_request": "Перевірте адресу або координати запиту.",
    "registration_disabled": "Автоматична реєстрація тимчасово вимкнена.",
    "registration_limit": "Ліміт нових реєстрацій вичерпано. Спробуйте пізніше.",
    "registration_revoked": "Доступ цієї інсталяції відкликано. Зверніться до адміністратора.",
    "registration_conflict": "Не вдалося підтвердити реєстрацію інсталяції.",
}


class ClientError(Exception):
    pass


class NetworkClient:
    def __init__(self, cache=None):
        self.cache = cache
        self.last_source = "network"

    def request(self, credentials, operation, value=None, feedback=None):
        feedback = feedback or QgsFeedback()
        if self.cache is None or operation == "quota":
            self.last_source = "network"
            return self._request(credentials, operation, value, feedback)
        if not credentials.url or not credentials.token:
            raise ClientError("Налаштуйте доступ до API та розблокуйте сховище QGIS.")

        def load(shared_feedback):
            records = self._request(credentials, operation, value, shared_feedback)
            return [record.to_dict() for record in records]

        try:
            data, self.last_source = self.cache.resolve(
                credentials.identity,
                operation,
                value,
                load,
                feedback,
                QgsFeedback,
            )
        except CacheCancelled:
            raise ClientError("Запит скасовано.") from None
        if self.last_source != "network":
            if credentials.mode == "visicom":
                self.update_quota(credentials, self._local_quota(credentials, False))
            QgsMessageLog.logMessage(
                "Кеш сесії: " + operation + " (" + self.last_source + ")",
                "UA Address Search",
                Qgis.MessageLevel.Info,
            )
        return [SearchResult.from_dict(record) for record in data]

    def update_quota(self, credentials, quota):
        if self.cache is not None:
            self.cache.update_quota(credentials.identity, quota)

    def ensure_registered(self, credentials, feedback):
        if not credentials.installation_id or credentials.mode != "railway":
            return

        def register(shared_feedback):
            result = self._transport(
                credentials,
                credentials.url + "/v1/register",
                {"installation_id": credentials.installation_id},
                shared_feedback,
            )
            if result.get("registered") is not True:
                raise ClientError("Сервер не підтримує автоматичну реєстрацію.")
            return {"registered": True}

        if self.cache is None:
            register(feedback)
        else:
            self.cache.resolve(
                credentials.identity,
                "register",
                credentials.installation_id,
                register,
                feedback,
                QgsFeedback,
            )

    def _request(self, credentials, operation, value=None, feedback=None):
        if not credentials.url or not credentials.token:
            raise ClientError("Налаштуйте доступ до API та розблокуйте сховище QGIS.")
        try:
            base = validate_url(credentials.url)
        except ValueError as exc:
            raise ClientError(str(exc)) from None
        feedback = feedback or QgsFeedback()
        if feedback.isCanceled():
            raise ClientError("Запит скасовано.")
        self.ensure_registered(credentials, feedback)
        if credentials.mode == "visicom":
            return self._direct(credentials, operation, value, feedback)
        if operation == "search":
            path, body = "/v1/search", {"text": value}
        elif operation == "reverse":
            path, body = "/v1/reverse", {"longitude": value[0], "latitude": value[1]}
        elif operation == "feature":
            path, body = "/v1/features/" + quote(value, safe=""), None
        elif operation == "quota":
            path, body = "/v1/quota", None
        else:
            raise ClientError("Невідома операція.")
        payload = self._transport(credentials, base + path, body, feedback)
        if operation == "quota":
            self.update_quota(credentials, payload)
            return payload
        try:
            records = payload["results"]
            if not isinstance(records, list):
                raise InvalidProviderData("Expected result list")
            return [SearchResult.from_dict(record) for record in records]
        except (KeyError, TypeError, ValueError):
            raise ClientError("Сервер повернув некоректні результати.") from None

    def _direct(self, credentials, operation, value, feedback):
        params = {"key": credentials.token}
        if operation == "search":
            path = "/geocode.json"
            params.update(
                text=value,
                categories="adr_address,adr_street,adm_settlement",
                country="ua",
                limit=10,
            )
        elif operation == "reverse":
            path = "/geocode.json"
            params.update(
                near=f"{value[0]},{value[1]}",
                categories="adr_address",
                radius=50,
                limit=1,
                order="distance",
                country="ua",
            )
        elif operation == "feature":
            path = "/feature/" + quote(value, safe="") + ".json"
        elif operation == "quota":
            quota = self._local_quota(credentials, False)
            self.update_quota(credentials, quota)
            return quota
        else:
            raise ClientError("Невідома операція.")
        payload = self._transport(
            credentials,
            credentials.url + path + "?" + urlencode(params),
            None,
            feedback,
            direct=True,
        )
        try:
            return normalize(payload)
        except InvalidProviderData:
            raise ClientError("Visicom повернув некоректні результати.") from None

    @staticmethod
    def _local_quota(credentials, increment):
        zone = QTimeZone(b"Europe/Kyiv")
        if not zone.isValid():
            zone = QTimeZone(b"Europe/Kiev")
        if not zone.isValid():
            raise ClientError("Часовий пояс Europe/Kyiv недоступний.")
        day = QDateTime.currentDateTimeUtc().toTimeZone(zone).date().toString("yyyy-MM-dd")
        try:
            return LocalUsage(credentials.usage_path).record(
                credentials.token, day, credentials.quota_limit, increment
            )
        except (OSError, sqlite3.Error):
            raise ClientError("Не вдалося зберегти локальний лічильник запитів.") from None

    def _transport(self, credentials, url, body, feedback, direct=False):
        if feedback.isCanceled():
            raise ClientError("Запит скасовано.")
        request = QNetworkRequest(QUrl(url))
        attributes = getattr(QNetworkRequest, "Attribute", QNetworkRequest)
        controls = getattr(QNetworkRequest, "CacheLoadControl", QNetworkRequest)
        request.setAttribute(attributes.CacheSaveControlAttribute, False)
        request.setAttribute(attributes.CacheLoadControlAttribute, controls.AlwaysNetwork)
        if not direct:
            request.setRawHeader(b"Authorization", ("Bearer " + credentials.token).encode("utf-8"))
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setRawHeader(b"Accept", b"application/json")
        if self.cache is not None and not direct:
            request.setRawHeader(b"X-UAAS-Session-ID", self.cache.session_id.encode("ascii"))
        request.setTransferTimeout(REQUEST_TIMEOUT_MS)
        # Never forward a credential to a redirected host, even on HTTPS.
        policy = getattr(QNetworkRequest, "RedirectPolicyAttribute", None)
        manual = getattr(QNetworkRequest, "ManualRedirectPolicy", None)
        if policy is None:
            policy = QNetworkRequest.Attribute.RedirectPolicyAttribute
            manual = QNetworkRequest.RedirectPolicy.ManualRedirectPolicy
        request.setAttribute(policy, manual)
        if direct:
            self.update_quota(credentials, self._local_quota(credentials, True))
        status, content, error = self._send(request, body, feedback)
        if feedback.isCanceled():
            raise ClientError("Запит скасовано.")
        if len(content) > MAX_RESPONSE_BYTES:
            raise ClientError("Відповідь сервера завелика.")
        if not status:
            raise ClientError("Немає зв’язку із сервером або сталася помилка TLS.")
        try:
            payload = json.loads(content.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise ClientError("Сервер повернув некоректний JSON.") from None
        if not direct and isinstance(payload, dict):
            self.update_quota(credentials, payload.get("quota"))
        if status != 200:
            if direct:
                if status in (401, 403):
                    raise ClientError(
                        "Ключ Visicom недійсний. Перевірте власний ключ у налаштуваннях."
                    )
                code = {401: "provider_auth", 403: "provider_auth", 429: "provider_quota"}.get(
                    status, "provider_unavailable"
                )
                raise ClientError(MESSAGES[code].replace("Серверний ключ", "Ключ"))
            error_data = payload.get("error", {}) if isinstance(payload, dict) else {}
            code = error_data.get("code", "") if isinstance(error_data, dict) else ""
            raise ClientError(MESSAGES.get(code, f"Помилка сервера HTTP {status}."))
        no_error = getattr(QNetworkReply, "NoError", None)
        if no_error is None:
            no_error = QNetworkReply.NetworkError.NoError
        if error != no_error:
            raise ClientError("Помилка отримання відповіді сервера.")
        if not isinstance(payload, dict):
            raise ClientError("Некоректний формат відповіді сервера.")
        return payload

    @staticmethod
    def _send(request, body, feedback):
        """Use an abortable reply even when cancellation races request creation."""
        manager = QgsNetworkAccessManager.instance()
        if body is None:
            reply = manager.get(request)
        else:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            reply = manager.post(request, data)
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(reply.abort)
        reply.finished.connect(loop.quit)
        connections = getattr(Qt, "ConnectionType", Qt)
        feedback.canceled.connect(reply.abort, connections.QueuedConnection)

        def limit_response():
            if reply.bytesAvailable() > MAX_RESPONSE_BYTES:
                reply.abort()

        reply.readyRead.connect(limit_response)
        timer.start(REQUEST_TIMEOUT_MS)
        try:
            if feedback.isCanceled():
                reply.abort()
            if not reply.isFinished():
                run = getattr(loop, "exec", None) or loop.exec_
                run()
            if not timer.isActive() and not feedback.isCanceled():
                raise ClientError("Час очікування відповіді вичерпано.")
            status_attr = getattr(QNetworkRequest, "HttpStatusCodeAttribute", None)
            if status_attr is None:
                status_attr = QNetworkRequest.Attribute.HttpStatusCodeAttribute
            content = bytes(reply.readAll()) if reply.isOpen() else b""
            return reply.attribute(status_attr), content, reply.error()
        finally:
            timer.stop()
            feedback.canceled.disconnect(reply.abort)
            reply.deleteLater()
            event_types = getattr(QEvent, "Type", QEvent)
            QCoreApplication.sendPostedEvents(reply, event_types.DeferredDelete)


class RequestTask(QgsTask):
    def __init__(self, credentials, operation, value, callback, cache=None):
        flag = getattr(QgsTask, "CanCancel", None)
        if flag is None:
            flag = QgsTask.Flag.CanCancel
        super().__init__("UA Address Search", flag)
        self.credentials = credentials
        self.operation = operation
        self.value = value
        self.callback = callback
        self.feedback = QgsFeedback()
        self.result = None
        self.error = None
        self.client = NetworkClient(cache)
        self.source = "network"

    def run(self):
        try:
            self.result = self.client.request(
                self.credentials, self.operation, self.value, self.feedback
            )
            self.source = self.client.last_source
            return True
        except ClientError as exc:
            self.error = str(exc)
            return False
        except Exception:
            # Do not expose network internals or a credential-bearing request.
            self.error = "Внутрішня помилка обробки відповіді сервера."
            return False

    def cancel(self):
        self.feedback.cancel()
        super().cancel()

    def finished(self, success):
        if self.callback is not None:
            self.callback(self)
