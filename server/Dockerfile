FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UAAS_DB_PATH=/data/uaas.sqlite3
COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt
COPY server server
COPY ua_address_search/core ua_address_search/core
COPY ua_address_search/__init__.py ua_address_search/__init__.py
RUN useradd --uid 10001 --create-home uaas && mkdir /data && chown uaas:uaas /data
USER uaas
CMD ["sh", "-c", "uvicorn server.app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]
