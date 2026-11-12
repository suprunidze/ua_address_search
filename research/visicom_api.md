# Visicom Data API: перевірений контракт

Перевірено 2026-11-12. Версія API 5.0, мова uk, формат JSON, координати WGS84
у порядку longitude, latitude. Ключ передається лише upstream як параметр key;
не журналювати URL або сирі винятки HTTP-клієнта.

- geocode.json: text, categories=adr_address,adr_street,adm_settlement,
  country=ua, limit=10; порядок результатів зберігається.
- Зворотний пошук: categories=adr_address, near=lng,lat, radius=50,
  limit=1, order=distance, country=ua.
- feature/{id}.json: повна геометрія лише за явною дією користувача.
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
