"""Worker-safe proxy client using the QGIS network/proxy infrastructure."""

import json
from urllib.parse import quote
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest
from qgis.core import QgsFeedback, QgsNetworkAccessManager, QgsTask, QgsMessageLog, Qgis
from .models import InvalidProviderData, SearchResult
from .cache import CacheCancelled, namespace
from .settings import validate_url

MESSAGES = {
    "unauthorized": "Токен відсутній, недійсний або відкликаний. Перевірте налаштування.",
    "rate_limit": "Забагато запитів. Зачекайте хвилину.",
    "daily_quota": "Добову квоту вичерпано.",
    "provider_quota": "Visicom повідомив про вичерпання квоти.",
    "provider_timeout": "Visicom не відповів вчасно.",
    "provider_auth": "Серверний ключ Visicom недійсний. Зверніться до адміністратора.",
    "provider_not_configured": "Серверний ключ Visicom ще не налаштований.",
    "provider_unavailable": "Сервіс Visicom тимчасово недоступний.",
    "provider_invalid_response": "Visicom повернув некоректну відповідь.",
    "invalid_request": "Перевірте адресу або координати запиту.",
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
            raise ClientError("Спочатку налаштуйте URL сервера і персональний токен.")

        def load(shared_feedback):
            records = self._request(credentials, operation, value, shared_feedback)
            return [record.to_dict() for record in records]

        try:
            data, self.last_source = self.cache.resolve(
                namespace(credentials.url, credentials.token),
                operation,
                value,
                load,
                feedback,
                QgsFeedback,
            )
        except CacheCancelled:
            raise ClientError("Запит скасовано.") from None
        if self.last_source != "network":
            QgsMessageLog.logMessage(
                "Кеш сесії: " + operation + " (" + self.last_source + ")",
                "UA Address Search",
                Qgis.MessageLevel.Info,
            )
        return [SearchResult.from_dict(record) for record in data]

    def _request(self, credentials, operation, value=None, feedback=None):
        if not credentials.url or not credentials.token:
            raise ClientError("Спочатку налаштуйте URL сервера і персональний токен.")
        try:
            base = validate_url(credentials.url)
        except ValueError as exc:
            raise ClientError(str(exc)) from None
        feedback = feedback or QgsFeedback()
        if feedback.isCanceled():
            raise ClientError("Запит скасовано.")
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
        request = QNetworkRequest(QUrl(base + path))
        request.setRawHeader(b"Authorization", ("Bearer " + credentials.token).encode("utf-8"))
        request.setRawHeader(b"Content-Type", b"application/json")
        request.setRawHeader(b"Accept", b"application/json")
        if self.cache is not None:
            request.setRawHeader(b"X-UAAS-Session-ID", self.cache.session_id.encode("ascii"))
        request.setTransferTimeout(18000)
        # Never forward a credential to a redirected host, even on HTTPS.
        policy = getattr(QNetworkRequest, "RedirectPolicyAttribute", None)
        manual = getattr(QNetworkRequest, "ManualRedirectPolicy", None)
        if policy is None:
            policy = QNetworkRequest.Attribute.RedirectPolicyAttribute
            manual = QNetworkRequest.RedirectPolicy.ManualRedirectPolicy
        request.setAttribute(policy, manual)
        if body is None:
            reply = QgsNetworkAccessManager.blockingGet(request, "", True, feedback)
        else:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            reply = QgsNetworkAccessManager.blockingPost(request, data, "", True, feedback)
        if feedback.isCanceled():
            raise ClientError("Запит скасовано.")
        status_attr = getattr(QNetworkRequest, "HttpStatusCodeAttribute", None)
        if status_attr is None:
            status_attr = QNetworkRequest.Attribute.HttpStatusCodeAttribute
        status = reply.attribute(status_attr)
        content = bytes(reply.content())
        if len(content) > 5_000_000:
            raise ClientError("Відповідь сервера завелика.")
        if not status:
            raise ClientError("Немає зв’язку із сервером або сталася помилка TLS.")
        try:
            payload = json.loads(content.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise ClientError("Сервер повернув некоректний JSON.") from None
        if status != 200:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            code = error.get("code", "") if isinstance(error, dict) else ""
            raise ClientError(MESSAGES.get(code, f"Помилка сервера HTTP {status}."))
        no_error = getattr(QNetworkReply, "NoError", None)
        if no_error is None:
            no_error = QNetworkReply.NetworkError.NoError
        if reply.error() != no_error:
            raise ClientError("Помилка отримання відповіді сервера.")
        if not isinstance(payload, dict):
            raise ClientError("Некоректний формат відповіді сервера.")
        if operation == "quota":
            return payload
        try:
            records = payload["results"]
            if not isinstance(records, list):
                raise InvalidProviderData("Expected result list")
            return [SearchResult.from_dict(record) for record in records]
        except (KeyError, TypeError, ValueError):
            raise ClientError("Сервер повернув некоректні результати.") from None


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
