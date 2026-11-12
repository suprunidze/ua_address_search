"""UA Address Search ? MIT licensed QGIS plugin."""


def classFactory(iface):
    from .plugin import AddressSearchPlugin
    return AddressSearchPlugin(iface)
