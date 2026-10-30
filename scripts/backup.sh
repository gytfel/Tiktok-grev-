#!/bin/sh
# Резервные копии базы и видео. Запускается сервисом backup из docker-compose.yml:
#   по расписанию:   сервис работает постоянно и делает копию раз в BACKUP_INTERVAL_HOURS часов;
#   один раз сейчас: docker compose run --rm backup once
set -eu

DIR=/backups
INTERVAL_HOURS="${BACKUP_INTERVAL_HOURS:-24}"
KEEP="${BACKUP_KEEP:-14}"

backup_once() {
    ts=$(date -u +%Y%m%d-%H%M%S)
    mkdir -p "$DIR/media"
    # База: формат pg_dump -Fc, восстанавливается через scripts/restore.sh.
    pg_dump -Fc -f "$DIR/db-$ts.dump.part"
    mv "$DIR/db-$ts.dump.part" "$DIR/db-$ts.dump"
    # Видео не меняются после загрузки, поэтому копируются только новые файлы.
    for f in /data/media/*; do
        case "$f" in *.part) continue ;; esac
        if [ -f "$f" ] && [ ! -e "$DIR/media/${f##*/}" ]; then
            cp "$f" "$DIR/media/"
        fi
    done
    # Храним KEEP последних копий базы.
    ls -1t "$DIR"/db-*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f
    echo "$(date -u +%FT%TZ) резервная копия готова: db-$ts.dump"
}

if [ "${1:-}" = "once" ]; then
    backup_once
    exit 0
fi

while true; do
    backup_once || echo "$(date -u +%FT%TZ) ошибка резервного копирования" >&2
    sleep "$((INTERVAL_HOURS * 3600))"
done
