# Deploy Guide — Hetzner + Nginx + Subdomain

> Bilingual guide. Russian first, English below.
> [Перейти к русской версии](#russian) · [Jump to English version](#english)

---

<a id="russian"></a>

# Русская версия

Полная инструкция: от покупки хоста до работающего сайта на своём сабдомене с паролем, HTTPS и автозапуском. Написано как универсальный шаблон — подходит и для деплоя нового приложения, и для добавления ещё одного сабдомена к уже работающему серверу.

## Условные обозначения

В командах ниже встречаются переменные, которые надо заменить на свои значения:

| Переменная | Что означает | Пример |
|---|---|---|
| `APPNAME` | короткое имя приложения (латиница, без пробелов) | `myapp` |
| `SUBDOMAIN` | поддомен на твоём домене | `myapp.example.com` |
| `DOMAIN` | основной домен | `example.com` |
| `PORT` | порт, на котором слушает приложение внутри сервера | `8552` |
| `SERVER_IP` | IP-адрес VPS | `89.167.51.254` |
| `USERNAME` | логин для basic auth | `admin` |

---

## Часть 1. Покупка инфраструктуры

### 1.1 Хостинг (Hetzner Cloud)

Если у тебя ЕЩЁ НЕТ сервера — покупаешь VPS. Если уже есть один работающий — пропускай этот шаг и переходи к [Части 3](#часть-3-приложение).

1. Регистрируйся на https://console.hetzner.cloud (нужна банковская карта).
2. Создай новый проект → **Add Server**.
3. Параметры:
   - **Location**: ближайшая к твоей аудитории (Helsinki / Nuremberg / Falkenstein).
   - **Image**: **Ubuntu 24.04** (LTS).
   - **Type**: **CX22** (Shared CPU, 2 vCPU, 4 GB RAM, ~€4/мес) — этого хватает на 3–5 небольших приложений на одном VPS.
   - **SSH Keys**: если есть — добавь свой публичный ключ (`~/.ssh/id_ed25519.pub`). Если нет — оставь пустым, Hetzner пришлёт пароль root на email.
   - **Name**: любое осмысленное.
4. Жми **Create & Buy now**.
5. Через ~30 секунд сервер готов. На email придёт IPv4 и (если без SSH-ключа) пароль root.

### 1.2 Домен (Namecheap или любой регистратор)

Если домен уже есть — пропускай.

1. Регистрируйся на https://namecheap.com (или Cloudflare / Google Domains / Reg.ru — любой).
2. Купи домен (обычно $8–12/год для `.com`).
3. После покупки заходи в управление DNS-записями.

### 1.3 DNS — добавить A-запись на сабдомен

Эта часть нужна **ВСЕГДА**, когда поднимаешь новый сабдомен (хоть на старом, хоть на новом сервере).

В панели регистратора (для Namecheap: **Domain List → Manage → Advanced DNS**) добавь новую запись:

| Type | Host | Value | TTL |
|---|---|---|---|
| `A Record` | `myapp` *(только субдомен, без основного)* | `SERVER_IP` | Automatic |

Сохрани. Прорастание DNS — обычно 1–10 минут, иногда до часа. Проверить:

```bash
dig myapp.example.com +short
# должен ответить SERVER_IP
```

---

## Часть 2. Первичная настройка сервера

**Делать ОДИН РАЗ** при покупке VPS. Если ты добавляешь второй сабдомен на уже работающий сервер — пропускай всю эту часть и переходи к [Части 3](#часть-3-приложение).

### 2.1 Первый вход и смена пароля

```bash
ssh root@SERVER_IP
# при первом входе попросит сменить пароль — придумай надёжный
```

### 2.2 Безопасность (минимум)

```bash
# обновить систему
apt update && apt upgrade -y

# создать обычного юзера (не работать постоянно под root)
adduser deploy            # придумай пароль
usermod -aG sudo deploy

# если ты добавил SSH-ключ при создании сервера — скопировать его новому юзеру
mkdir -p /home/deploy/.ssh
cp ~/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys

# базовый firewall
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

### 2.3 Установить базовое ПО

```bash
apt install -y python3 python3-venv python3-pip nginx apache2-utils certbot python3-certbot-nginx git curl
```

Что зачем:
- `python3*` — для Python-приложений.
- `nginx` — reverse proxy и HTTPS-терминатор.
- `apache2-utils` — даёт утилиту `htpasswd` для basic auth.
- `certbot` + `python3-certbot-nginx` — бесплатные SSL-сертификаты Let's Encrypt.
- `git`, `curl` — на каждый день.

Если приложение использует Playwright (как этот проект):

```bash
# заранее установим системные зависимости браузеров
apt install -y libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
               libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
               libgbm1 libpango-1.0-0 libcairo2 libasound2t64
# playwright install chromium запустится позже из venv приложения
```

---

## Часть 3. Приложение

С этого момента всё повторяется для **каждого нового сабдомена/приложения** на сервере. Главное: давай каждому приложению уникальные `APPNAME`, `PORT`, и свою папку в `/opt/`.

### 3.1 Залить код приложения на сервер

**Со своей рабочей машины** (Mac/Windows/Linux):

```bash
# на сервере подготовить целевую папку
ssh root@SERVER_IP 'mkdir -p /opt/APPNAME'

# залить код (исключая мусор)
rsync -avz --delete \
  --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.venv*' --exclude='.git' \
  /путь/к/проекту/   root@SERVER_IP:/opt/APPNAME/
```

### 3.2 Создать venv и поставить зависимости

```bash
ssh root@SERVER_IP

cd /opt/APPNAME
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt   # имя файла подставь своё

# если приложение использует Playwright
.venv/bin/playwright install chromium
```

### 3.3 Файл конфигурации `.env`

```bash
cat > /opt/APPNAME/.env <<'EOF'
# ключи API, режимы работы, и т.д.
MY_API_KEY=...
APP_MODE=production
EOF
chmod 600 /opt/APPNAME/.env   # никто кроме root не должен это читать
```

### 3.4 Проверить, что приложение вообще запускается

```bash
cd /opt/APPNAME
.venv/bin/python -m app.ui.app   # команда запуска зависит от твоего приложения
# должно сказать что-то вроде "running on 0.0.0.0:PORT"
# нажми Ctrl+C
```

### 3.5 Создать systemd-сервис (автозапуск + рестарт при падении)

```bash
cat > /etc/systemd/system/APPNAME.service <<'EOF'
[Unit]
Description=APPNAME
After=network.target

[Service]
WorkingDirectory=/opt/APPNAME
EnvironmentFile=/opt/APPNAME/.env
ExecStart=/opt/APPNAME/.venv/bin/python3 -m app.ui.app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now APPNAME       # запустить и включить автостарт
systemctl status APPNAME --no-pager  # должен быть "active (running)"
journalctl -u APPNAME -f             # хвост логов, Ctrl+C когда наглядишься
```

Управление сервисом:
```bash
systemctl start APPNAME      # запустить
systemctl stop APPNAME       # остановить
systemctl restart APPNAME    # перезапустить
systemctl status APPNAME     # статус
journalctl -u APPNAME -n 100 # последние 100 строк логов
```

---

## Часть 4. Nginx — сабдомен, basic auth, HTTPS

### 4.1 Создать пароль для basic auth

Если файла `/etc/nginx/.htpasswd` ещё нет (первое приложение на сервере) — создаём:
```bash
htpasswd -c /etc/nginx/.htpasswd USERNAME   # -c создаёт новый файл
```

Если он уже есть и ты просто добавляешь юзера:
```bash
htpasswd /etc/nginx/.htpasswd USERNAME      # БЕЗ -c, иначе сотрёт старых
```

Каждое приложение можно защищать своим файлом (например, `/etc/nginx/.htpasswd-APPNAME`) если хочешь раздельные пароли для разных сабдоменов.

### 4.2 Конфиг nginx для сабдомена

```bash
cat > /etc/nginx/sites-available/APPNAME <<'EOF'
server {
    listen 80;
    server_name SUBDOMAIN;

    auth_basic "Restricted";
    auth_basic_user_file /etc/nginx/.htpasswd;

    # Если приложение раздаёт скачиваемые файлы — алиас на их папку
    location /downloads/ {
        alias /opt/APPNAME/output/;
        autoindex on;
    }

    location / {
        proxy_pass http://127.0.0.1:PORT;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket-приложениям (Flet/FastAPI live updates) нужны длинные таймауты
        proxy_read_timeout 86400;
    }
}
EOF

# включить сайт
ln -s /etc/nginx/sites-available/APPNAME /etc/nginx/sites-enabled/

# на первом приложении: удалить дефолтный сайт, который занимает 80 порт
rm -f /etc/nginx/sites-enabled/default

# проверить конфиг и применить
nginx -t
systemctl reload nginx
```

Замени `APPNAME`, `SUBDOMAIN`, `PORT` на свои значения **до** записи файла. Удобно:

```bash
APPNAME=myapp SUBDOMAIN=myapp.example.com PORT=8552
sed -i "s/APPNAME/$APPNAME/g; s/SUBDOMAIN/$SUBDOMAIN/g; s/PORT/$PORT/g" \
  /etc/nginx/sites-available/$APPNAME
```

### 4.3 Проверить, что HTTP работает

Открой в браузере `http://SUBDOMAIN`. Должен появиться диалог логина/пароля, дальше — твоё приложение.

Если **не открывается** — проверь по порядку:
```bash
dig SUBDOMAIN +short              # должен вернуть SERVER_IP
systemctl status APPNAME          # приложение работает?
ss -tlnp | grep PORT              # слушает ли порт?
nginx -t                          # конфиг валиден?
journalctl -u nginx -n 50         # ошибки nginx
```

### 4.4 HTTPS через Let's Encrypt

После того как HTTP-версия открывается, делаем HTTPS:

```bash
certbot --nginx -d SUBDOMAIN
# certbot задаст несколько вопросов — email для уведомлений, согласие с TOS,
# и спросит, делать ли redirect HTTP → HTTPS — отвечай "2" (да, делать).
```

Certbot сам отредактирует твой конфиг nginx, добавит блок `listen 443 ssl`, и поставит cron на автообновление сертификата (он валиден 90 дней, обновляется автоматически).

Проверь: `https://SUBDOMAIN` — должен открыться с зелёным замком.

---

## Часть 5. Обслуживание

### 5.1 Бэкап базы данных и важных данных

Если приложение хранит SQLite-базу или генерирует файлы, поставь автобэкап. Пример: бэкап `jobs.db` раз в день в `/opt/APPNAME/backups/`:

```bash
mkdir -p /opt/APPNAME/backups
crontab -e
# добавь строку:
0 4 * * * cp /opt/APPNAME/data/jobs.db /opt/APPNAME/backups/jobs.db.$(date +\%F) && find /opt/APPNAME/backups/ -name "jobs.db.*" -mtime +14 -delete
```

### 5.2 Автоудаление старых файлов (опционально)

Если приложение копит большие файлы (видео, картинки) и их можно потом удалять:

```bash
crontab -e
# удалять .mp4 старше 7 дней каждый день в 3:00 UTC
0 3 * * * find /opt/APPNAME/output/ -name "*.mp4" -mtime +7 -delete
```

### 5.3 Обновление приложения

Когда выкатываешь новую версию с локальной машины:

```bash
# 1. (опционально, но рекомендую) бэкап
ssh root@SERVER_IP 'cp /opt/APPNAME/data/jobs.db /opt/APPNAME/data/jobs.db.bak-$(date +%F-%H%M) 2>/dev/null'

# 2. синхронизация кода
rsync -avz --delete --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.venv*' --exclude='.git' --exclude='data' --exclude='output' --exclude='.env' \
  /путь/к/проекту/  root@SERVER_IP:/opt/APPNAME/

# 3. если менялись зависимости
ssh root@SERVER_IP '/opt/APPNAME/.venv/bin/pip install -r /opt/APPNAME/requirements.txt'

# 4. рестарт
ssh root@SERVER_IP 'systemctl restart APPNAME && sleep 3 && systemctl status APPNAME --no-pager -l | head -20'
```

> Важно: исключения `--exclude='data'`, `--exclude='output'`, `--exclude='.env'` спасают тебя от затирания базы данных, накопленных файлов и конфига при rsync с `--delete`.

### 5.4 Полезные команды

```bash
# логи приложения за последний час
journalctl -u APPNAME --since "1 hour ago"

# логи nginx
tail -f /var/log/nginx/access.log
tail -f /var/log/nginx/error.log

# использование диска
df -h
du -sh /opt/*

# использование памяти
free -h

# что слушает на портах
ss -tlnp
```

---

## Часть 6. Если что-то пошло не так

| Симптом | Что проверить |
|---|---|
| Браузер не открывает сайт | DNS прорастился? (`dig SUBDOMAIN +short`) |
| 502 Bad Gateway | Приложение упало. `systemctl status APPNAME` и `journalctl -u APPNAME -n 50` |
| 404 / страница nginx по умолчанию | `/etc/nginx/sites-enabled/default` не удалён, либо `server_name` не совпадает |
| Certbot не выдаёт сертификат | DNS должен указывать на этот же сервер; 80 порт должен быть открыт; `ufw status` |
| Basic auth не пускает | Проверь файл `.htpasswd` и путь к нему в конфиге nginx |
| После рестарта приложение падает | Несовместимая БД или новый pip-пакет. `journalctl -u APPNAME -n 100` |

---

<a id="english"></a>

# English version

Complete instructions: from buying a host to a working subdomain with password protection, HTTPS, and autostart. Written as a generic template — works both for deploying a brand-new app and for adding another subdomain to an existing server.

## Placeholders

The commands below use variables — replace with your own values:

| Variable | Meaning | Example |
|---|---|---|
| `APPNAME` | short app identifier (lowercase, no spaces) | `myapp` |
| `SUBDOMAIN` | the subdomain on your domain | `myapp.example.com` |
| `DOMAIN` | your root domain | `example.com` |
| `PORT` | port the app listens on inside the server | `8552` |
| `SERVER_IP` | VPS IP address | `89.167.51.254` |
| `USERNAME` | login for basic auth | `admin` |

---

## Part 1. Infrastructure

### 1.1 Hosting (Hetzner Cloud)

If you **don't** have a server yet — buy a VPS. If you already have a working one — skip to [Part 3](#part-3-the-app).

1. Sign up at https://console.hetzner.cloud (credit card required).
2. Create a project → **Add Server**.
3. Settings:
   - **Location**: closest to your audience (Helsinki / Nuremberg / Falkenstein).
   - **Image**: **Ubuntu 24.04** (LTS).
   - **Type**: **CX22** (Shared CPU, 2 vCPU, 4 GB RAM, ~€4/mo) — enough for 3–5 small apps on one box.
   - **SSH Keys**: paste your public key (`~/.ssh/id_ed25519.pub`) if you have one. Otherwise leave blank — Hetzner emails you the root password.
   - **Name**: anything meaningful.
4. Hit **Create & Buy now**.
5. Server is ready in ~30 seconds. IPv4 (and root password, if no SSH key) come via email.

### 1.2 Domain (Namecheap or any registrar)

Skip if you already own one.

1. Sign up at https://namecheap.com (or Cloudflare / Google Domains).
2. Buy a domain ($8–12/year for a `.com`).
3. Once purchased, open the DNS management panel.

### 1.3 DNS — A record for the subdomain

This step is **always required** when launching a new subdomain (whether on an old or new server).

In your registrar (Namecheap: **Domain List → Manage → Advanced DNS**) add:

| Type | Host | Value | TTL |
|---|---|---|---|
| `A Record` | `myapp` *(subdomain only, no root domain)* | `SERVER_IP` | Automatic |

Save. Propagation is usually 1–10 minutes, sometimes up to an hour. Verify:

```bash
dig myapp.example.com +short
# should return SERVER_IP
```

---

## Part 2. Initial server setup

Do this **once** when you buy a fresh VPS. If you're adding a second subdomain to an existing server — skip to [Part 3](#part-3-the-app).

### 2.1 First login + password change

```bash
ssh root@SERVER_IP
# on first login you'll be forced to change the root password — pick a strong one
```

### 2.2 Basic hardening

```bash
# update system
apt update && apt upgrade -y

# create a regular user (don't live as root forever)
adduser deploy
usermod -aG sudo deploy

# if you added an SSH key at server creation — copy it to the new user
mkdir -p /home/deploy/.ssh
cp ~/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys

# basic firewall
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

### 2.3 Install baseline software

```bash
apt install -y python3 python3-venv python3-pip nginx apache2-utils certbot python3-certbot-nginx git curl
```

What each is for:
- `python3*` — to run Python apps.
- `nginx` — reverse proxy and HTTPS terminator.
- `apache2-utils` — provides the `htpasswd` utility for basic auth.
- `certbot` + `python3-certbot-nginx` — free Let's Encrypt SSL certs.
- `git`, `curl` — daily essentials.

If your app uses Playwright (this project does):

```bash
# pre-install browser system libs
apt install -y libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
               libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
               libgbm1 libpango-1.0-0 libcairo2 libasound2t64
# `playwright install chromium` runs later from the app's venv
```

---

## Part 3. The app

Everything from here repeats for **each new subdomain/app** on the server. The key: give every app a unique `APPNAME`, `PORT`, and its own folder under `/opt/`.

### 3.1 Push the code to the server

**From your dev machine** (Mac/Windows/Linux):

```bash
# prepare target dir on the server
ssh root@SERVER_IP 'mkdir -p /opt/APPNAME'

# upload code (skipping junk)
rsync -avz --delete \
  --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.venv*' --exclude='.git' \
  /path/to/project/   root@SERVER_IP:/opt/APPNAME/
```

### 3.2 Create venv + install deps

```bash
ssh root@SERVER_IP

cd /opt/APPNAME
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt   # use your actual filename

# only if your app uses Playwright
.venv/bin/playwright install chromium
```

### 3.3 The `.env` config file

```bash
cat > /opt/APPNAME/.env <<'EOF'
# API keys, modes, etc.
MY_API_KEY=...
APP_MODE=production
EOF
chmod 600 /opt/APPNAME/.env   # only root can read
```

### 3.4 Sanity-check that the app starts

```bash
cd /opt/APPNAME
.venv/bin/python -m app.ui.app   # the launch command depends on your app
# should say something like "running on 0.0.0.0:PORT"
# hit Ctrl+C
```

### 3.5 Create the systemd service (autostart + auto-restart on crash)

```bash
cat > /etc/systemd/system/APPNAME.service <<'EOF'
[Unit]
Description=APPNAME
After=network.target

[Service]
WorkingDirectory=/opt/APPNAME
EnvironmentFile=/opt/APPNAME/.env
ExecStart=/opt/APPNAME/.venv/bin/python3 -m app.ui.app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now APPNAME       # start it and enable autostart
systemctl status APPNAME --no-pager  # expect "active (running)"
journalctl -u APPNAME -f             # tail logs, Ctrl+C when done
```

Day-to-day commands:
```bash
systemctl start APPNAME      # start
systemctl stop APPNAME       # stop
systemctl restart APPNAME    # restart
systemctl status APPNAME     # status
journalctl -u APPNAME -n 100 # last 100 log lines
```

---

## Part 4. Nginx — subdomain, basic auth, HTTPS

### 4.1 Create basic-auth password

If `/etc/nginx/.htpasswd` doesn't exist yet (first app on the server) — create it:
```bash
htpasswd -c /etc/nginx/.htpasswd USERNAME   # -c creates a new file
```

If it already exists and you're just adding a user:
```bash
htpasswd /etc/nginx/.htpasswd USERNAME      # NO -c, or you'll wipe existing users
```

You can give each app its own password file (e.g. `/etc/nginx/.htpasswd-APPNAME`) if you want separate credentials per subdomain.

### 4.2 Nginx config for the subdomain

```bash
cat > /etc/nginx/sites-available/APPNAME <<'EOF'
server {
    listen 80;
    server_name SUBDOMAIN;

    auth_basic "Restricted";
    auth_basic_user_file /etc/nginx/.htpasswd;

    # If the app serves downloadable files — alias to that folder
    location /downloads/ {
        alias /opt/APPNAME/output/;
        autoindex on;
    }

    location / {
        proxy_pass http://127.0.0.1:PORT;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket apps (Flet, FastAPI live updates) need long timeouts
        proxy_read_timeout 86400;
    }
}
EOF

# enable the site
ln -s /etc/nginx/sites-available/APPNAME /etc/nginx/sites-enabled/

# on the first app: remove the default site that occupies port 80
rm -f /etc/nginx/sites-enabled/default

# validate config and reload
nginx -t
systemctl reload nginx
```

Substitute `APPNAME`, `SUBDOMAIN`, `PORT` **before** writing the file. Handy:

```bash
APPNAME=myapp SUBDOMAIN=myapp.example.com PORT=8552
sed -i "s/APPNAME/$APPNAME/g; s/SUBDOMAIN/$SUBDOMAIN/g; s/PORT/$PORT/g" \
  /etc/nginx/sites-available/$APPNAME
```

### 4.3 Verify HTTP works

Open `http://SUBDOMAIN` in a browser. You should see a login prompt, then your app.

If it **doesn't open**, check in order:
```bash
dig SUBDOMAIN +short              # should return SERVER_IP
systemctl status APPNAME          # is the app running?
ss -tlnp | grep PORT              # is the port being listened on?
nginx -t                          # is the config valid?
journalctl -u nginx -n 50         # nginx errors
```

### 4.4 HTTPS via Let's Encrypt

Once HTTP works, add HTTPS:

```bash
certbot --nginx -d SUBDOMAIN
# certbot will ask a few questions — email for notifications, TOS agreement,
# and whether to redirect HTTP → HTTPS — answer "2" (yes, redirect).
```

Certbot edits your nginx config in place, adds a `listen 443 ssl` block, and sets up a cron job to auto-renew the cert (valid 90 days, renews automatically).

Verify: `https://SUBDOMAIN` opens with a green padlock.

---

## Part 5. Maintenance

### 5.1 Back up your database / important data

If the app stores a SQLite DB or generates files, set up automatic backups. Example: daily backup of `jobs.db` into `/opt/APPNAME/backups/`:

```bash
mkdir -p /opt/APPNAME/backups
crontab -e
# add this line:
0 4 * * * cp /opt/APPNAME/data/jobs.db /opt/APPNAME/backups/jobs.db.$(date +\%F) && find /opt/APPNAME/backups/ -name "jobs.db.*" -mtime +14 -delete
```

### 5.2 Auto-delete old files (optional)

If the app accumulates big files (videos, images) that are safe to remove later:

```bash
crontab -e
# delete .mp4 files older than 7 days, every day at 3 AM UTC
0 3 * * * find /opt/APPNAME/output/ -name "*.mp4" -mtime +7 -delete
```

### 5.3 Deploying a new version

When you ship a new version from your dev machine:

```bash
# 1. (optional but recommended) backup
ssh root@SERVER_IP 'cp /opt/APPNAME/data/jobs.db /opt/APPNAME/data/jobs.db.bak-$(date +%F-%H%M) 2>/dev/null'

# 2. sync code
rsync -avz --delete --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.venv*' --exclude='.git' --exclude='data' --exclude='output' --exclude='.env' \
  /path/to/project/  root@SERVER_IP:/opt/APPNAME/

# 3. if deps changed
ssh root@SERVER_IP '/opt/APPNAME/.venv/bin/pip install -r /opt/APPNAME/requirements.txt'

# 4. restart
ssh root@SERVER_IP 'systemctl restart APPNAME && sleep 3 && systemctl status APPNAME --no-pager -l | head -20'
```

> Important: the `--exclude='data'`, `--exclude='output'`, `--exclude='.env'` flags prevent rsync from wiping your database, generated files, and config when used with `--delete`.

### 5.4 Useful commands

```bash
# app logs from the last hour
journalctl -u APPNAME --since "1 hour ago"

# nginx logs
tail -f /var/log/nginx/access.log
tail -f /var/log/nginx/error.log

# disk usage
df -h
du -sh /opt/*

# memory usage
free -h

# what's listening on ports
ss -tlnp
```

---

## Part 6. Troubleshooting

| Symptom | Where to look |
|---|---|
| Site won't open in browser | DNS propagated? (`dig SUBDOMAIN +short`) |
| 502 Bad Gateway | App crashed. `systemctl status APPNAME` + `journalctl -u APPNAME -n 50` |
| 404 / default nginx page | `/etc/nginx/sites-enabled/default` not removed, or `server_name` mismatch |
| Certbot won't issue cert | DNS must point to this server; port 80 must be open; check `ufw status` |
| Basic auth rejects you | Check the `.htpasswd` file and the path referenced in the nginx config |
| App crashes on restart | Incompatible DB or new pip package. `journalctl -u APPNAME -n 100` |
