# UA Address Search

Українськомовний плагін **QGIS 3.28–4.99**: адресний пошук у панелі та Locator,
найближча адреса за кліком, тимчасове підсвічування обраного об’єкта.
Власний код плагіна — **MIT**. Текст ліцензії входить до ZIP.
Дані Visicom і сторонні компоненти зберігають власні умови та атрибуцію.

## Встановлення та використання

1. У QGIS: «Модулі → Керування та встановлення модулів → Встановити з ZIP»;
   виберіть `dist/ua_address_search.zip` і ввімкніть UA Address Search.
2. Натисніть іконку плагіна → «Налаштування». Вкажіть HTTPS URL проксі та
   персональний токен адміністратора. Ключ Visicom у плагін не вводиться.
3. Задайте головний пароль QGIS Authentication Manager, якщо QGIS його запитає.
   У QgsSettings зберігається лише ідентифікатор конфігурації, не сам токен.
4. Введіть адресу та натисніть Enter/«Знайти», оберіть результат.
   Або використовуйте Locator: `uas Київ, Хрещатик, 26`.
5. «Показати геометрію» виконує окремий запит. «Найближча адреса за кліком»
   шукає адресу в радіусі 50 м. «Квота» перевіряє залишок; «Скасувати» перериває запит.

Префікс `uas` має три символи: коротші префікси QGIS резервує для вбудованих фільтрів.

HTTP дозволено лише на localhost для розробки. Зміна сервера вимагає повторно
ввести токен. Для Locator розблокування сховища відбувається при явному пошуку.
Плагін використовує поточну карту й CRS, не додає підкладку та не створює шарів.
Пакетна обробка й експорт не входять до цієї версії.

## Локальний сервер (PowerShell, із кореня репозиторію)

Рекомендоване середовище сервера — Python 3.12. Плагін не потребує pip-залежностей.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -B -m server.app.tokens issue --label pilot --output artifacts/pilot.secret
.\.venv\Scripts\python.exe -B -m server.app.run --env-file .env
```

`.env` містить `VISICOM_DATA_API_KEY`; інші параметри наведені у `.env.example`.
За замовчуванням сервер слухає `http://127.0.0.1:8000`, SQLite —
`artifacts/uaas.sqlite3`. Токен видається в ігнорований файл `.secret`, а не в лог;
передайте його користувачу приватним каналом. У Windows захистіть доступ до файлу
засобами ACL. Видаліть приватний файл після передачі, якщо він більше не потрібен.

Відкликання всіх токенів із заданою міткою:

```powershell
.\.venv\Scripts\python.exe -B -m server.app.tokens revoke --label pilot
```

CLI і сервер повинні використовувати той самий `UAAS_DB_PATH`.
Для нестандартного шляху задайте цю змінну також у середовищі CLI.

## API та квоти

Робочі маршрути вимагають `Authorization: Bearer <personal-token>`:

| Метод | Маршрут | Вхід |
|---|---|---|
| POST | `/v1/search` | `{"text":"Київ, Хрещатик, 26"}` |
| POST | `/v1/reverse` | `{"longitude":30.5234,"latitude":50.4501}` |
| GET | `/v1/features/{id}` | Ідентифікатор із результату пошуку |
| GET | `/v1/quota` | Без параметрів |
| GET | `/healthz` | Без токена, без upstream-запиту |

Пошук і геометрія повертають `{"results":[...]}`; поля результату: `id`, `label`,
`address`, `category`, `center`, `bbox`, `attribution`, `geometry`.
`center` — `[longitude, latitude]`, `bbox` — `[west, south, east, north]`, WGS84.
Помилки: `{"error":{"code":"daily_quota"}}`; квоти — HTTP 429,
неправильний токен — 401, введення — 422, upstream — 502/503/504.
`/healthz` підтверджує роботу процесу, а не доступність Visicom.

Типові межі: 1500 upstream-запитів/добу загалом, 100 на токен, 10 клієнтських
запитів/хвилину на токен. `/v1/quota` враховується лише у хвилинному ліміті.
Кожний upstream-запит резервує бюджет перед відправленням; помилки й скасування
не повертають резерв, автоматичних повторів немає. Межа доби — Europe/Kyiv.
Звернення інших програм із тим самим ключем сервер не бачить.

## Railway: підготовка до окремого розгортання

- Build context — корінь репозиторію; config file — `/server/railway.toml`.
- Dockerfile — `server/Dockerfile`; один процес Uvicorn і один екземпляр сервісу.
- Приєднайте постійний Volume у `/data`, задайте `UAAS_DB_PATH=/data/uaas.sqlite3`.
- `VISICOM_DATA_API_KEY` додайте як серверну секретну змінну, не через Git.
- Для прав доступу до Railway Volume задайте `RAILWAY_RUN_UID=0`.
- Після запуску видайте токени через CLI усередині сервісу з тією самою БД.
- Налаштуйте HTTPS domain. Перевірте `/healthz`, захищений пошук і збереження
  квоти після перезапуску. Журнали містять статус/тривалість і коди помилок,
  не містять адрес запитів або секретів.
- Перед оновленням зробіть backup тому; rollback — попередній commit/image
  із тим самим томом. Поточна схема створюється ідемпотентно, міграцій ще немає.

Розгортання та push цим етапом не виконуються. Тестовий тариф Visicom має
1500 запитів/добу й термін 30 днів. Закритий пілот із токенами потребує
відповідного режиму використання API; MIT не змінює умов провайдера.
Офіційні джерела: [API](https://api.visicom.ua/uk/products/data-api),
[тарифи](https://api.visicom.ua/uk/price),
[умови публічного доступу](https://api.visicom.ua/uk/docs/terms-of-free-use),
[Railway Volumes](https://docs.railway.com/volumes/reference).

## Розробка, тести та випуск

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests/unit -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests/server -v
.\.venv\Scripts\ruff.exe check .
python -B scripts/smoke_test_all_qgis.py
python -B scripts/package_plugin.py
python -B scripts/sync_to_profiles.py
python -B scripts/smoke_test_all_qgis.py --installed
```

Unit/API-тести синтетичні, QGIS-тести використовують локальний HTTP-сервер.
Smoke runner знаходить підтримувані QGIS у Program Files; налаштування й auth DB
кожного запуску ізольовані. Він перевіряє джерело та, з `--installed`, кожну
встановлену копію; звіти — `artifacts/`. Це не автоматизація видимого вікна QGIS.

Синхронізація охоплює наявні профілі QGIS3/QGIS4, використовує staging/rollback,
перевіряє SHA-256 кожного файла й відмовляє при запущеному QGIS. Явний виняток:
`python -B scripts/sync_to_profiles.py --allow-running-qgis`; після нього
перезапустіть QGIS або перезавантажте плагін.

Живий API перевіряється лише явно, максимум трьома запитами:

```powershell
.\.venv\Scripts\python.exe -B scripts/live_check.py --env-file .env --max-requests 3
```

Ця діагностика звертається прямо до Visicom і не входить у квоту проксі.
ZIP відтворюваний, містить лише runtime-файли та MIT; локальний `plugins.xml`
має відносний download URL, який потрібно замінити адресою хостингу при публікації.
Версія береться з metadata.txt; публічне розміщення й заповнення контактів автора
в метаданих є окремим етапом.
