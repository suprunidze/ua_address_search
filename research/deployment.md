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
Видача персонального токена та авторизовані інтеграційні перевірки ще тривають.
SSH host key не приймався: fingerprint не підтверджений довіреним джерелом.
Токени та інші секрети до цього документа й Git не додаються.
