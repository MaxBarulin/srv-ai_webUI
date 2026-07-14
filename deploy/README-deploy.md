# Развёртывание srv-ai webUI (инструкция для системного администратора)

Приложение — один процесс Python (FastAPI + uvicorn), БД — файл SQLite. GPU не
требуется. Порт по умолчанию — **8080** (меняется в `.env`).

Все команды предполагают, что репозиторий разворачивается в `/opt/srv-ai-ui`.

---

## Вариант A. Онлайн-установка (пока на сервере есть интернет)

```bash
sudo useradd --system --home-dir /opt/srv-ai-ui --shell /usr/sbin/nologin srv-ai-ui
sudo mkdir -p /opt/srv-ai-ui
sudo chown srv-ai-ui:srv-ai-ui /opt/srv-ai-ui

# Скопировать/склонировать проект в /opt/srv-ai-ui, затем:
cd /opt/srv-ai-ui
sudo -u srv-ai-ui python3 -m venv venv
sudo -u srv-ai-ui ./venv/bin/pip install --upgrade pip
sudo -u srv-ai-ui ./venv/bin/pip install -r requirements.txt

# Системный пакет для парсинга PDF-сканов (§16):
sudo apt-get install -y poppler-utils
```

Изображения и PDF-сканы распознаёт сама мультимодальная модель (llama.cpp с
mmproj) — растеризованные страницы передаются ей напрямую. `poppler-utils`
(`pdftoppm`) нужен только чтобы превратить PDF-скан в изображения для этой
передачи.

## Вариант B. Офлайн-установка (закрытый контур, air-gap) — обязательна

На машине **с интернетом** (той же ОС/архитектуры, Python той же минорной версии)
заранее собрать колёса:

```bash
pip download -r requirements.txt -d wheels/
```

Каталог `wheels/` вместе с проектом перенести на сервер. Шрифты и JS-вендоры уже
лежат в `static/` — из сети ничего не тянется. NER-модель фильтра ПДн
поставляется внутри пакетов `natasha`/`slovnet`/`navec` (в колёсах), отдельной
загрузки не требует.

На сервере:

```bash
cd /opt/srv-ai-ui
sudo -u srv-ai-ui python3 -m venv venv
sudo -u srv-ai-ui ./venv/bin/pip install --no-index --find-links wheels/ -r requirements.txt
```

Системный пакет `poppler-utils` заранее скачать в виде `.deb` и установить
`dpkg -i`.

---

## Настройка

```bash
cd /opt/srv-ai-ui
sudo -u srv-ai-ui cp env.example .env
sudo -u srv-ai-ui nano .env        # выставить APP_PORT, LLM_BASE_URL, RAG_BASE_URL, PII_FILTER и т.д.
```

Ключевые параметры `.env` описаны в `env.example`. Каталог данных (`DATA_DIR`)
должен быть доступен на запись пользователю `srv-ai-ui`.

## Первый администратор

```bash
cd /opt/srv-ai-ui
sudo -u srv-ai-ui ./venv/bin/python -m app.create_admin
```

## systemd-сервис

```bash
sudo cp deploy/srv-ai-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now srv-ai-ui
sudo systemctl status srv-ai-ui
journalctl -u srv-ai-ui -f          # логи (уровень — в конфиге приложения)
```

---

## ОБЯЗАТЕЛЬНО: закрытие прямого доступа к моделям (§13, п. 3.6 Положения)

После ввода UI в эксплуатацию прямой доступ пользователей к API моделей в обход
аутентификации должен стать технически невозможным. Привяжите llama.cpp (**:8000**)
и сервис эмбеддингов (**:8001**) к `127.0.0.1` **либо** закройте порты firewall
для всех адресов, кроме localhost и хоста LightRAG.

Пример через ufw (разрешить только локальный доступ):

```bash
sudo ufw default deny incoming
sudo ufw allow 8080/tcp                     # веб-интерфейс (при необходимости сузьте до подсети отдела)
sudo ufw deny 8000/tcp                      # llama.cpp — извне запрещён
sudo ufw deny 8001/tcp                      # эмбеддинги — извне запрещён
# Если LightRAG на отдельной машине — разрешить только её IP к нужному порту:
# sudo ufw allow from <IP-LightRAG> to any port 8001 proto tcp
sudo ufw enable
```

Предпочтительный вариант — запускать llama.cpp с привязкой к `127.0.0.1` и ключом
`--no-webui` (штатный web UI отключается — см. ТЗ §1).

---

## Резервное копирование (§12)

```bash
sudo -u srv-ai-ui /opt/srv-ai-ui/deploy/backup.sh
# создаст архив в $DATA_DIR/backups (SQLite .backup + .env), ротация — 14 копий
```

Для регулярного бэкапа добавьте в cron пользователя `srv-ai-ui`:

```
0 2 * * * /opt/srv-ai-ui/deploy/backup.sh >/dev/null 2>&1
```

## Обновление

```bash
cd /opt/srv-ai-ui
sudo -u srv-ai-ui git pull            # или скопировать новую версию
sudo -u srv-ai-ui ./venv/bin/pip install -r requirements.txt   # офлайн: --no-index --find-links wheels/
sudo systemctl restart srv-ai-ui
```

Схема БД мигрируется автоматически при старте (добавление таблиц/колонок безопасно
для существующих данных).
