"""QGIS test entry point; isolated settings/auth DB and real Qt event loop."""

import argparse
import faulthandler
import os
from pathlib import Path
import sys
import tempfile
import unittest


def main():
    faulthandler.dump_traceback_later(45)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-parent", type=Path, required=True)
    parser.add_argument("--test-name", help="Run a specific unittest name from tests/qgis")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(args.plugin_parent))
    with tempfile.TemporaryDirectory(prefix="uaas-qgis-") as temporary:
        os.environ["QGIS_AUTH_DB_DIR_PATH"] = temporary
        os.environ["QGIS_CUSTOM_CONFIG_PATH"] = temporary
        from qgis.PyQt.QtCore import QCoreApplication, QSettings
        from qgis.core import QgsApplication

        enum = getattr(QSettings, "Format", QSettings)
        scope = getattr(QSettings, "Scope", QSettings)
        QSettings.setDefaultFormat(enum.IniFormat)
        QSettings.setPath(enum.IniFormat, scope.UserScope, temporary)
        app = QgsApplication([], True, temporary)
        QCoreApplication.setOrganizationName("UAAS-Isolated-Test")
        QCoreApplication.setApplicationName("UAAS-Isolated-Test")
        app.initQgis()
        try:
            import ua_address_search

            actual = Path(ua_address_search.__file__).resolve().parent.parent
            if actual != args.plugin_parent.resolve():
                raise RuntimeError("The wrong plugin copy was imported")
            if args.test_name:
                sys.path.insert(0, str(root / "tests/qgis"))
                suite = unittest.defaultTestLoader.loadTestsFromName(args.test_name)
            else:
                suite = unittest.defaultTestLoader.discover(
                    str(root / "tests/qgis"), pattern="test_*.py"
                )
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            app.processEvents()
            if result.wasSuccessful():
                print("UAAS_SMOKE_OK")
                return 0
            return 1
        finally:
            app.exitQgis()
            faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    sys.exit(main())
