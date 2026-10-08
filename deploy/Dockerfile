# Образ приложения Plan 24 (веб, миграции, планировщик напоминаний — один образ, разные команды).
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY backend/requirements-prod.txt /srv/backend/
RUN pip install -r backend/requirements-prod.txt
COPY backend /srv/backend
COPY frontend /srv/frontend
COPY deploy/entrypoint.sh /srv/entrypoint.sh
# публичный корневой сертификат кластера БД (если подключение по домену с проверкой): deploy/timeweb/certs/ca.crt → /certs/ca.crt
COPY deploy/timeweb/certs /certs
RUN useradd -m -u 10001 plan24 && chmod +x /srv/entrypoint.sh && chown -R plan24 /srv
USER plan24
WORKDIR /srv/backend
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/ready', timeout=2).status==200 else 1)"
ENTRYPOINT ["/srv/entrypoint.sh"]
CMD ["web"]
