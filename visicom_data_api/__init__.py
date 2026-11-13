"""Visicom Data API for QGIS — MIT licensed QGIS plugin."""


def classFactory(iface):
    from .plugin import VisicomDataApiPlugin

    return VisicomDataApiPlugin(iface)
