#!/bin/sh
# Восстановление из резервной копии (запускать в папке проекта на сервере):
#   ./scripts/restore.sh backups/db-20261030-020000.dump
# Видео возвращаются из backups/media. Перед восстановлением панель и воркеры останавливаются.
set -eu

DUMP="${1:-}"
if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
    echo "Укажите файл копии базы, например: ./scripts/restore.sh backups/db-20261030-020000.dump" >&2
    exit 1
fi

COMPOSE="docker compose"
printf "База будет заменена данными из %s. Продолжить? [y/N] " "$DUMP"
read -r answer
[ "$answer" = "y" ] || [ "$answer" = "Y" ] || exit 1

$COMPOSE stop app worker worker-uploads
$COMPOSE exec -T postgres pg_restore --clean --if-exists --no-owner -U tiktok -d tiktok < "$DUMP"
if [ -d backups/media ]; then
    $COMPOSE run --rm --no-deps -v "$PWD/backups/media:/restore:ro" --entrypoint sh app -c \
        'for f in /restore/*; do if [ -f "$f" ] && [ ! -e "/data/media/${f##*/}" ]; then cp "$f" /data/media/; fi; done'
fi
$COMPOSE start app worker worker-uploads
echo "Готово. Не забудьте: TOKEN_ENCRYPTION_KEY в .env должен быть тем же, что и при создании копии."
