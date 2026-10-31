#!/usr/bin/env bash
# Установка панели на сервер Ubuntu или Debian с HTTPS. Запускать из папки с кодом:
#   sudo bash scripts/install.sh            — установить или обновить и запустить
#   sudo bash scripts/install.sh --env-only — только подготовить .env, ничего не устанавливая
#
# Скрипт спрашивает домен, ключи TikTok и данные для страниц /terms и /privacy. Значения можно
# передать заранее переменными окружения с теми же именами, что в .env (DOMAIN=... TIKTOK_CLIENT_KEY=...).
# Повторный запуск не меняет уже заданные ключи: SECRET_KEY и TOKEN_ENCRYPTION_KEY создаются один раз.
set -euo pipefail

cd "$(dirname "$0")/.."
ENV_FILE=.env
ENV_ONLY=false
[ "${1:-}" = "--env-only" ] && ENV_ONLY=true

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

if ! $ENV_ONLY && [ "$(id -u)" -ne 0 ]; then
    die "Запустите через sudo: sudo bash scripts/install.sh"
fi
command -v openssl >/dev/null || die "Нужен openssl: apt-get install -y openssl"

# --- .env ---------------------------------------------------------------------------------

[ -f "$ENV_FILE" ] || cp .env.example "$ENV_FILE"
chmod 600 "$ENV_FILE"

get_env() {
    grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true
}

set_env() {
    local key="$1" value="$2" tmp
    tmp=$(mktemp)
    if grep -qE "^$key=" "$ENV_FILE"; then
        # Значение подставляется как есть, без интерпретации sed-спецсимволов.
        KEY="$key" VALUE="$value" awk -F= '
            $1 == ENVIRON["KEY"] && !done { print ENVIRON["KEY"] "=" ENVIRON["VALUE"]; done = 1; next }
            { print }' "$ENV_FILE" >"$tmp"
        cat "$tmp" >"$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
    fi
    rm -f "$tmp"
}

# Значение: из переменной окружения, иначе из .env, иначе спросить (с подсказкой по умолчанию).
ask() {
    local key="$1" prompt="$2" default="${3:-}" secret="${4:-}" current answer
    current="${!key:-$(get_env "$key")}"
    current="${current:-$default}"
    if [ -t 0 ] && [ -z "${!key:-}" ]; then
        if [ -n "$secret" ] && [ -n "$current" ]; then
            read -r -s -p "$prompt [оставить текущий]: " answer
            echo
        elif [ -n "$secret" ]; then
            read -r -s -p "$prompt: " answer
            echo
        else
            read -r -p "$prompt${current:+ [$current]}: " answer
        fi
        current="${answer:-$current}"
    fi
    set_env "$key" "$current"
}

say "Настройки панели"
ask DOMAIN "Домен панели (например, myqueue1.duckdns.org)"
DOMAIN=$(get_env DOMAIN)
DOMAIN=${DOMAIN#https://}
DOMAIN=${DOMAIN#http://}
DOMAIN=${DOMAIN%%/*}
[ -n "$DOMAIN" ] || die "Домен обязателен: TikTok работает только с HTTPS-адресом."
set_env DOMAIN "$DOMAIN"
set_env PUBLIC_BASE_URL "https://$DOMAIN"
set_env SESSION_HTTPS_ONLY true
set_env APP_BIND 127.0.0.1

ask TIKTOK_CLIENT_KEY "TikTok Client key (можно оставить пустым и добавить позже)"
ask TIKTOK_CLIENT_SECRET "TikTok Client secret" "" secret
ask LEGAL_OWNER "Владелец для страниц /terms и /privacy (имя или компания, латиницей)"
ask LEGAL_EMAIL "Email для связи на этих страницах"
ask LEGAL_COUNTRY "Страна по-английски (например, Ukraine)"
ask LEGAL_COUNTRY_RU "Страна по-русски (например, Украина)"
ask ADMIN_USERNAME "Логин первого администратора" admin

# Ключи создаются один раз: новый TOKEN_ENCRYPTION_KEY сделал бы сохранённые токены нечитаемыми.
[ -n "$(get_env SECRET_KEY)" ] || set_env SECRET_KEY "$(openssl rand -base64 48 | tr -d '\n/+=')"
[ -n "$(get_env TOKEN_ENCRYPTION_KEY)" ] || set_env TOKEN_ENCRYPTION_KEY "$(openssl rand -base64 32 | tr '+/' '-_')"
GENERATED_PASSWORD=""
if [ -z "$(get_env ADMIN_PASSWORD)" ]; then
    GENERATED_PASSWORD=$(openssl rand -base64 18 | tr -d '\n/+=')
    set_env ADMIN_PASSWORD "$GENERATED_PASSWORD"
fi

# На маленьком сервере (до 2 ГБ памяти) видео загружаются по одному.
MEM_MB=$(awk '/MemTotal/ { print int($2 / 1024) }' /proc/meminfo 2>/dev/null || echo 4096)
if [ "$MEM_MB" -lt 2000 ] && [ "$(get_env UPLOAD_CONCURRENCY)" = "3" ]; then
    set_env UPLOAD_CONCURRENCY 1
fi

if $ENV_ONLY; then
    say "Файл .env готов. Запуск: sudo bash scripts/install.sh"
    exit 0
fi

# --- система ------------------------------------------------------------------------------

say "Проверка домена"
SERVER_IPS=$(hostname -I 2>/dev/null || true)
PUBLIC_IP=$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)
DOMAIN_IP=$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR == 1 { print $1 }' || true)
if [ -z "$DOMAIN_IP" ]; then
    warn "Домен $DOMAIN пока никуда не указывает. В DuckDNS впишите IP этого сервера: ${PUBLIC_IP:-$SERVER_IPS}"
elif [[ " $SERVER_IPS $PUBLIC_IP " != *" $DOMAIN_IP "* ]]; then
    warn "Домен $DOMAIN указывает на $DOMAIN_IP, а у этого сервера адрес ${PUBLIC_IP:-$SERVER_IPS}."
    warn "Сертификат HTTPS не будет получен, пока домен не укажет на этот сервер."
else
    echo "$DOMAIN → $DOMAIN_IP — это этот сервер."
fi

if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
    say "Установка Docker"
    curl -fsSL https://get.docker.com | sh
fi

SWAP_MB=$(awk '/SwapTotal/ { print int($2 / 1024) }' /proc/meminfo)
if [ "$MEM_MB" -lt 2000 ] && [ "$SWAP_MB" -lt 1000 ] && [ ! -e /swapfile ]; then
    say "Добавление 2 ГБ подкачки (памяти на сервере ${MEM_MB} МБ)"
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
fi

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
    say "Открытие портов 80 и 443 в ufw"
    ufw allow 80/tcp >/dev/null
    ufw allow 443/tcp >/dev/null
fi

# --- запуск -------------------------------------------------------------------------------

say "Сборка и запуск (первый раз — 5–15 минут)"
mkdir -p backups
docker compose -f docker-compose.yml -f docker-compose.https.yml up -d --build

say "Ожидание запуска"
for _ in $(seq 1 60); do
    curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break
    sleep 5
done
curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null 2>&1 ||
    die "Панель не запустилась. Журнал: docker compose logs app"

HTTPS_OK=false
for _ in $(seq 1 24); do
    if curl -fsS --max-time 5 "https://$DOMAIN/api/health" >/dev/null 2>&1; then
        HTTPS_OK=true
        break
    fi
    sleep 5
done

say "Готово"
if $HTTPS_OK; then
    echo "Панель: https://$DOMAIN"
else
    warn "Панель запущена, но https://$DOMAIN пока не открывается. Проверьте, что домен указывает на этот"
    warn "сервер и порты 80 и 443 открыты. Журнал HTTPS: docker compose logs caddy"
fi
echo "Логин: $(get_env ADMIN_USERNAME)"
if [ -n "$GENERATED_PASSWORD" ]; then
    echo "Пароль: $GENERATED_PASSWORD   (он же записан в .env как ADMIN_PASSWORD)"
    echo "Администратор создаётся с этим паролем при первом запуске, пока в панели нет пользователей."
else
    echo "Пароль: в файле .env (ADMIN_PASSWORD), если вы его ещё не меняли в панели"
fi
cat <<EOF

Для настроек приложения TikTok:
  Redirect URI:                 https://$DOMAIN/api/oauth/tiktok/callback
  Условия использования:        https://$DOMAIN/terms
  Политика конфиденциальности:  https://$DOMAIN/privacy

Сохраните копию файла .env в надёжном месте: без TOKEN_ENCRYPTION_KEY резервные копии бесполезны.
Обновление: git pull && sudo bash scripts/install.sh
EOF
if [ -z "$(get_env TIKTOK_CLIENT_KEY)" ]; then
    warn "Ключи TikTok не заданы. Когда получите их, снова запустите sudo bash scripts/install.sh и введите их."
fi
