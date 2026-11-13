# Visicom Data API: перевірений контракт

## Розширення 0.1.0.8

Документацію перевірено 2026-11-13, без платних запитів Visicom.
Геокодування й feature використовують гілку `5.0/uk`, дорожні операції — `5.0/core`.
Нові POST-маршрути проксі: `/v1/objects/search`, `/v1/features`, `/v1/route`,
`/v1/matrix`, `/v1/optimize`, `/v1/snap`. Вхідні схеми опубліковані в OpenAPI.
Оболонка відповіді: `result` і `quota`; наявні адресні маршрути зберігають `results`.
Тіло запиту обмежено 64 КіБ. Валідація передує резервуванню квоти.

- objects: categories, необов’язковий text, country=ua, limit 1–250;
  near + radius або contains з Polygon/MultiPolygon до 250 вершин.
- features: до 250 ID одним запитом; повна геометрія. Типи геометрій розділяються у виходах QGIS.
- distance: до 25 проміжних точок, geometry=path; довжина в метрах, порожній об’єкт означає
  відсутність маршруту. Час, піший і велосипедний профілі не заявлені.
- distancematrix: до 25 origins та 25 destinations; дистанція та статус кожної пари.
  Неуспішна пара має distance_m=null, не 0.
- tsp: до 50 точок; перша фіксована, остання фіксована для round_trip=false;
  повертається перестановка індексів, не лінія. Повторні/некоректні індекси відхиляються.
- snaptoroad: до 250 точок, driving/walking; separate і interpolate.
  separate вимикає interpolate. Відповідь Point, набір Point або LineString.

Для route/matrix/tsp: driving, driving-shortest, direct; блокування застосовуються
до автомобільних режимів. Жодна операція не ділиться на запити автоматично.
Processing передає тільки власний ключ, не використовує проксі та не змінює режим панелі.
Кеш включає всі параметри та порядок точок; лічильник власного ключа спільний.

Офіційні джерела: https://api.visicom.ua/uk/products/data-api/data-api-references
та розділи geocode, feature, distance, distancematrix, tsp, snaptoroad.
Повнота інтеграції підтверджується синтетичними контрактними та QGIS-тестами;
живу перевірку нових функцій не виконували.

Перевірено 2026-11-12. Версія API 5.0, мова uk, формат JSON, координати WGS84
у порядку longitude, latitude. Ключ передається лише upstream як параметр key;
не журналювати URL або сирі винятки HTTP-клієнта.

- geocode.json: text, categories=adr_address,adr_street,adm_settlement,
  country=ua, limit=10; порядок результатів зберігається.
- Зворотний пошук: categories=adr_address, near=lng,lat, radius=50,
  limit=1, order=distance, country=ua.
- feature/{id}.json: повна геометрія вибраного результату; автоматично, коли
  ввімкнений чекбокс, або за кнопкою завантаження в шар. Кеш спільний для обох шляхів.
- Відповідь може бути Feature або FeatureCollection. Геокодування повертає
  geo_centroid (Point) і bbox; properties різняться між категоріями.
- Порожня FeatureCollection — коректний результат. Відсутній bbox дозволений;
  відсутній ідентифікатор або валідний центр — помилка контракту.
- Сирі URL і довільні properties не передаються клієнту. Зберігається copyright.
- Тестовий тариф: 1500/добу протягом 30 днів; часовий пояс за замовчуванням Київ.

Live-перевірка реалізованого адаптера: forward, reverse та feature успішні;
використано 3 запити. Повний локальний сценарій QGIS → FastAPI → Visicom → карта
та облік квоти також успішний (ще 1 запит). Попереднє дослідження форми відповіді використало ще 2.
Сирі відповіді та секрети не зберігалися. Fixtures повністю синтетичні.

Джерела:
- https://api.visicom.ua/uk/products/data-api/data-api-references/geocode
- https://api.visicom.ua/uk/products/data-api/data-api-references/feature
- https://api.visicom.ua/uk/price
- https://api.visicom.ua/uk/docs/terms-of-free-use
