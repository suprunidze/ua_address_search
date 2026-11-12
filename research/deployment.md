# Railway deployment — 2026-11-12

- Workspace: suprunidze's Projects; project: inspiring-smile.
- Service: ua_address_search; environment: production; source: main, 94a9d3f.
- Public HTTPS: https://uaaddresssearch-production.up.railway.app.
- Docker build і запуск успішні; Python 3.12, один Uvicorn worker.
- Постійний Volume 500 МБ: /data; SQLite: /data/uaas.sqlite3.
- Ключ Visicom перенесено з локального .env через stdin, без виведення значення.
- Добова квота 1500 загалом / 100 на токен; 10 клієнтських запитів/хвилину.
- Healthcheck /healthz, timeout 30 с; ON_FAILURE, максимум 3 повтори.
- Free-план вимагає sleepApplication=true; зміну тарифу не виконували.
- Параметри застосовані безпосередньо до сервісу: Railway відхилив
  railwayConfigFile як застарілий Config as Code.

Підтверджено: /healthz HTTP 200, /v1/quota без токена HTTP 401.
Персональний токен активовано погодженою користувачем одноразовою ініціалізацією:
сервер отримав лише SHA-256 хеш; у SQLite зберігається хеш із міткою suprunidze-qgis.
Після ініціалізації повернуто штатний Docker CMD, bootstrap-змінну видалено.
Фінальний deployment: e6d943e9-3cb0-4bca-9144-8c0ed835acbc, SUCCESS.

## Авторизовані перевірки

- /v1/search: 1 результат для публічної контрольної адреси у Києві.
- /v1/reverse: 1 найближча адреса в радіусі 50 м.
- /v1/features/{id}: отримано Polygon.
- Квота після цих трьох запитів: 100 → 97.
- Після повернення штатного запуску токен валідний, залишок квоти — 97.
- Встановлені копії QGIS3/default (QGIS 3.40.8, Qt5) та QGIS4/default
  (QGIS 4.0.2, Qt6): реальний HTTPS-пошук, маркер і CRS EPSG:3857 — PASS.
- Загалом витрачено 5 upstream-запитів; залишок персональної квоти — 95.
- Налаштування і проєкти відкритих QGIS не змінювалися; перевірки ізольовані.
- Санітизований звіт: artifacts/railway_deployment_verification.json (не в Git).

## Підключення користувача

У налаштуваннях плагіна вкажіть HTTPS URL вище та токен із локального
ігнорованого файла artifacts/railway-suprunidze-qgis.secret. Значення токена
не публікується; QGIS збереже його через Authentication Manager.
Для додаткових користувачів видавайте окремі токени через штатний CLI.
SSH host key не приймався: fingerprint не підтверджений довіреним джерелом.
Токени та інші секрети до цього документа й Git не додаються.
