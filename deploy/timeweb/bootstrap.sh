#!/bin/sh
# Подготовка НОВОГО облачного сервера Ubuntu 24.04 (Timeweb Cloud). Запускать от root один раз:
#   ADMIN_IP=203.0.113.7 sh bootstrap.sh        # ADMIN_IP — с какого адреса разрешён SSH (необязательно)
# Делает: обновления, Docker, клиент PostgreSQL 16, файрвол (22/80/443), fail2ban, автообновления безопасности, пользователь plan24.
set -eu
[ "$(id -u)" = 0 ] || { echo "нужен root"; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update -y && apt-get upgrade -y
apt-get install -y ca-certificates curl gnupg ufw fail2ban unattended-upgrades docker.io docker-compose-v2 postgresql-client
systemctl enable --now docker fail2ban unattended-upgrades

# файрвол: наружу только SSH, HTTP, HTTPS (порт БД наружу не публикуется — БД в приватной сети)
ufw default deny incoming; ufw default allow outgoing
if [ -n "${ADMIN_IP:-}" ]; then ufw allow from "$ADMIN_IP" to any port 22 proto tcp; else ufw allow 22/tcp; fi
ufw allow 80/tcp; ufw allow 443/tcp
ufw --force enable

# отдельный пользователь без пароля для эксплуатации; вход по SSH-ключу root-пользователя копируется
id plan24 >/dev/null 2>&1 || useradd -m -s /bin/bash -G docker plan24
if [ -f /root/.ssh/authorized_keys ]; then
  install -d -m 700 -o plan24 -g plan24 /home/plan24/.ssh
  install -m 600 -o plan24 -g plan24 /root/.ssh/authorized_keys /home/plan24/.ssh/authorized_keys
fi
install -d -o plan24 -g plan24 /opt/plan24 /var/backups/plan24
echo "Готово. Дальше: скопируйте релиз в /opt/plan24 и следуйте deploy/timeweb/README.md (шаг 6)."
echo "Рекомендуется после проверки входа под plan24 отключить вход root по SSH и пароль (PermitRootLogin no, PasswordAuthentication no)."
