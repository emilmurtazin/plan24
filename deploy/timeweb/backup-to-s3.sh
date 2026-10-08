#!/bin/sh
# Резервная копия БД (под владельцем БД) + выгрузка в S3-хранилище Timeweb. Запускать на сервере по расписанию.
# Переменные (из /opt/plan24/deploy/timeweb/backup.env): PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE PGSSLMODE
#   S3_BUCKET  S3_ENDPOINT (адрес из панели S3)  AWS_ACCESS_KEY_ID  AWS_SECRET_ACCESS_KEY  KEEP_DAYS (по умолчанию 14)
# Нужен aws-cli (apt install awscli) — без него копия остаётся только на сервере (и скрипт сообщает об этом).
set -eu
DIR="${BACKUP_DIR:-/var/backups/plan24}"; KEEP="${KEEP_DAYS:-14}"
FILE=$(sh "$(dirname "$0")/../postgres/backup.sh" "$DIR")
echo "копия создана: $FILE ($(du -h "$FILE" | cut -f1))"
if [ -n "${S3_BUCKET:-}" ] && command -v aws >/dev/null 2>&1; then
  aws s3 cp --endpoint-url "$S3_ENDPOINT" "$FILE" "s3://$S3_BUCKET/plan24/$(basename "$FILE")"
  echo "выгружено в s3://$S3_BUCKET/plan24/"
else
  echo "ВНИМАНИЕ: выгрузка в S3 не выполнена (нет S3_BUCKET или aws-cli) — копия хранится только на этом сервере" >&2
fi
find "$DIR" -name 'plan24-*.dump' -mtime +"$KEEP" -delete
