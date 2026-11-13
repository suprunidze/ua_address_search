"""Explicit-prefix Locator filter; network work stays in the worker thread."""

from qgis.core import QgsLocatorFilter, QgsLocatorResult
from .core.client import ClientError, NetworkClient
from .core.settings import Credentials


class AddressLocatorFilter(QgsLocatorFilter):
    def __init__(self, credentials=None, activate=None, credentials_provider=None, cache=None):
        super().__init__()
        self.credentials = credentials or Credentials()
        self.activate = activate
        self.credentials_provider = credentials_provider
        self.cache = cache
        self.setUseWithoutPrefix(False)
        self.setFetchResultsDelay(1000)

    def name(self):
        return "visicom_data_api"

    def displayName(self):
        return "Адреси України (Visicom)"

    def prefix(self):
        return "адр"

    def prepare(self, string, context):
        if context.usingPrefix and len(string.strip()) >= 3 and self.credentials_provider:
            self.credentials = self.credentials_provider()
        return []

    def clone(self):
        return AddressLocatorFilter(
            self.credentials, self.activate, self.credentials_provider, self.cache
        )

    def fetchResults(self, string, context, feedback):
        text = string.strip()
        if len(text) < 3 or feedback.isCanceled() or not context.usingPrefix:
            return
        try:
            records = NetworkClient(self.cache).request(self.credentials, "search", text, feedback)
        except ClientError as exc:
            if not feedback.isCanceled():
                result = QgsLocatorResult()
                result.filter = self
                result.displayString = str(exc)
                result.userData = {"error": True}
                self.resultFetched.emit(result)
            return
        for index, record in enumerate(records):
            if feedback.isCanceled():
                return
            result = QgsLocatorResult()
            result.filter = self
            result.displayString = record.label
            result.description = record.category
            result.score = 1.0 / (index + 1)
            result.userData = record.to_dict()
            self.resultFetched.emit(result)

    def triggerResult(self, result):
        if self.activate is not None:
            self.activate(result.userData)
