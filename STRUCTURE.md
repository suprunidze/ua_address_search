# Структура Visicom Data API for QGIS

## Плагін
- `visicom_data_api/core/operations.py`: спільні контракти, ліміти, параметри запитів
  і нормалізація результатів шести додаткових операцій без залежності від QGIS.
- `core/qgis_io.py`: CRS, порядок точок, стандартні поля й розділення виходів за геометрією.
- `ui/function_tabs.py`: п’ять нових сторінок, редактор упорядкованих точок.
- `function_controller.py`: задачі, перемикання вкладок, вибір на карті й результати.
- `processing_provider.py`: провайдер visicom_data_api та вісім алгоритмів лише з власним ключем.
- `visicom_data_api/__init__.py`: QGIS classFactory; `plugin.py`: життєвий цикл,
  задачі, панель, Locator і відображення результатів.
- `core/models.py`: незалежні від QGIS моделі, нормалізація Visicom та перевірка WGS84.
- `core/cache.py`: спільний кеш сесії 128 МіБ, LRU, об’єднання запитів та скасування.
- `core/client.py`: HTTP через QgsNetworkAccessManager, RequestTask і скасування.
- `core/usage.py`: атомарний SQLite-лічильник прямих запитів, спільний для профілів комп’ютера.
- `core/settings.py`: режими доступу, стан панелі й геометрії, Authentication Manager для секретів.
- `locator.py`: фільтр `адр`, підготовка облікових даних у GUI-потоці.
- `map_tools.py`: зворотний пошук, CRS, маркери, геометрія та керовані flash-анімації.
- `ui/search_dock.py`, `ui/settings_dialog.py`: інтерфейс Qt5/Qt6.
- `ui/quota_dialog.py`: немодальне повідомлення добової квоти та реєстрація ключа Visicom.
- `resources/icon.svg`: іконка; `resources/help.html`: українська офлайн-довідка для браузера.
  `metadata.txt`: версія. `LICENSE`: копія MIT.

## Сервер
- `server/app/main.py`: фабрика FastAPI та маршрути /v1/*, /healthz.
- `config.py`, `run.py`: параметри середовища й локальний запуск із .env.
- `schemas.py`: публічний контракт пошуку й реєстрації; `visicom.py`: upstream-адаптер.
- `storage.py`: хеші токенів, реєстрації інсталяцій та їх ліміти, транзакційні квоти SQLite.
- `journal.py`: явний stdout JSON-журнал INFO без адрес, координат або секретів.
- `tokens.py`: видача й відкликання персональних токенів через CLI.
- `server/requirements.txt`, `Dockerfile`, `railway.toml`: середовище Railway.

## Інструменти й документація
- `scripts/package_plugin.py`: ZIP з allowlist та .gitignore, MIT, plugins.xml.
- `scripts/sync_to_profiles.py`: QGIS3/QGIS4, staging, rollback та SHA-256.
- `scripts/smoke_test_all_qgis.py`, `qgis_smoke.py`: ізольовані QGIS-перевірки
  вихідної та встановлених копій; `live_check.py`: максимум 1–3 запити Visicom.
  Smoke runner пише окремі журнали, діагностує зависання та завершує дерево тесту
  після тайм-ауту; `qgis_smoke.py --test-name` дозволяє точкове відтворення.
- `tests/unit/`, `tests/server/`, `tests/qgis/`, `tests/release/`: моделі, сервер і QGIS.
  `tests/fixtures/address.json`: синтетична відповідь, не вивантаження Visicom.
- `research/architecture.md`: прийнятий план. `research/visicom_api.md`: контракт API; `research/validation.md`: перевірки випуску; `research/deployment.md`: Railway deployment.
- `dist/`: версіоновані ZIP та plugins.xml; `artifacts/`: ігноровані локальні звіти.
- `requirements-dev.txt`, `pyproject.toml`: залежності розробки та Ruff.
- `.github/workflows/checks.yml`: CI для lint, unit/API та пакування без живого API.
- `.env`: локальні секрети; `.env.example`: шаблон. `.dockerignore`: контекст Docker.
