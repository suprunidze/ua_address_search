# Структура UA Address Search

## Плагін
- `ua_address_search/__init__.py`: QGIS classFactory; `plugin.py`: життєвий цикл,
  задачі, панель, Locator і відображення результатів.
- `core/models.py`: незалежні від QGIS моделі, нормалізація Visicom та перевірка WGS84.
- `core/client.py`: HTTP через QgsNetworkAccessManager, RequestTask і скасування.
- `core/settings.py`: URL, стан панелі, Authentication Manager для токенів.
- `locator.py`: фільтр `uas`, підготовка облікових даних у GUI-потоці.
- `map_tools.py`: зворотний пошук, CRS, тимчасові маркери та геометрія.
- `ui/search_dock.py`, `ui/settings_dialog.py`: інтерфейс Qt5/Qt6.
- `resources/icon.svg`: іконка. `metadata.txt`: версія. `LICENSE`: копія MIT.

## Сервер
- `server/app/main.py`: фабрика FastAPI та маршрути /v1/*, /healthz.
- `config.py`, `run.py`: параметри середовища й локальний запуск із .env.
- `schemas.py`: публічний контракт введення; `visicom.py`: upstream-адаптер.
- `storage.py`: хеші токенів та транзакційні квоти SQLite.
- `tokens.py`: видача й відкликання персональних токенів через CLI.
- `server/requirements.txt`, `Dockerfile`, `railway.toml`: середовище Railway.

## Інструменти й документація
- `scripts/package_plugin.py`: ZIP з allowlist та .gitignore, MIT, plugins.xml.
- `scripts/sync_to_profiles.py`: QGIS3/QGIS4, staging, rollback та SHA-256.
- `scripts/smoke_test_all_qgis.py`, `qgis_smoke.py`: ізольовані QGIS-перевірки
  вихідної та встановлених копій; `live_check.py`: максимум 1–3 запити Visicom.
- `tests/unit/`, `tests/server/`, `tests/qgis/`, `tests/release/`: моделі, сервер і QGIS.
  `tests/fixtures/address.json`: синтетична відповідь, не вивантаження Visicom.
- `research/architecture.md`: прийнятий план. `research/visicom_api.md`: контракт API; `research/validation.md`: перевірки випуску.
- `dist/`: версіоновані ZIP та plugins.xml; `artifacts/`: ігноровані локальні звіти.
- `requirements-dev.txt`, `pyproject.toml`: залежності розробки та Ruff.
- `.github/workflows/checks.yml`: CI для lint, unit/API та пакування без живого API.
- `.env`: локальні секрети; `.env.example`: шаблон. `.dockerignore`: контекст Docker.
