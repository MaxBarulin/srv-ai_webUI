# Развёртывание на сервере СВОИМИ СИЛАМИ (без sudo/root)

**Для кого:** у тебя есть SSH-доступ на сервер (где крутится llama.cpp), ты
управляешь службой модели (запускаешь/останавливаешь), но **прав `sudo` нет**.

**Что получится:** веб-интерфейс на порту **8080**, работающий под твоим
пользователем, с автозапуском после перезагрузки сервера — без прав root.

Ключевая идея: приложение — обычный Python-процесс, ему root не нужен. А
безопасность (закрытие прямого доступа к модели) обеспечиваем не firewall'ом, а
привязкой llama.cpp к `127.0.0.1` — этим ты и так управляешь.

---

## Что НЕ можем без sudo и чем заменяем

| Обычно требует sudo | Замена без прав |
|---------------------|-----------------|
| установка в `/opt`, системный юзер | ставим в свою домашнюю папку под своим логином |
| `apt install python3` | Miniconda (ставится в домашнюю папку, без root) |
| `apt install poppler-utils` | не нужен: PDF растеризует `pypdfium2` (pip-wheel, ставится из `requirements.txt`) |
| systemd в `/etc/systemd/system` | `tmux`/`nohup` + `@reboot` в пользовательском crontab |
| firewall для портов 8000/8001 | запустить llama.cpp с `--host 127.0.0.1` |

---

## Шаг 1. Python без root (если системного нет)

Проверь, есть ли уже подходящий Python:
```
python3 --version
```
Если 3.10+ — переходи к шагу 2. Если нет или версия старая — поставь **Miniconda**
(без root, в домашнюю папку):
```
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b -p $HOME/miniconda3
$HOME/miniconda3/bin/conda init bash
# перелогинься или: source ~/.bashrc
```

## Шаг 2. Забрать проект и окружение
```
cd ~
git clone <URL_репозитория> srv-ai-ui
cd srv-ai-ui
python3 -m venv venv          # или: conda create -n srvai python=3.11 -y && conda activate srvai
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Шаг 3. Растеризация PDF — уже включена, ничего ставить не надо

Режим «PDF как картинку» (страницы → изображения для vision-модели) работает
из коробки: пакет **`pypdfium2`** из `requirements.txt` содержит движок PDFium
прямо в wheel — **системный poppler не нужен**, ставится офлайн и без root.

Резервный путь на `pdftoppm` (poppler-utils) остаётся в коде, но нужен, только
если `pypdfium2` по какой-то причине не установлен:
- через **conda**: `conda install -c conda-forge poppler`;
- проверка: `pdftoppm -h` покажет справку.

> ⚠️ `pip install poppler-utils` — **НЕ то**: это пустышка (пакет-заглушка),
> `pdftoppm` она не даёт. Но при установленном `pypdfium2` poppler и не требуется.

## Шаг 4. Настроить `.env`
```
cp env.example .env
nano .env    # или отредактируй любым редактором
```
Поскольку UI и llama.cpp на **одном сервере**, модель адресуем по localhost:
```
APP_HOST=0.0.0.0
APP_PORT=8080
DATA_DIR=./data
LLM_BASE_URL=http://127.0.0.1:8000/v1
LLM_MODEL=имя_твоей_модели
RAG_ENABLED=true
RAG_BASE_URL=http://IP_МАШИНЫ_С_LIGHTRAG:7860
PII_FILTER=true
PII_WHITELIST_FILE=./pii_whitelist.txt
```

## Шаг 5. Создать администратора
```
python -m app.create_admin
```

## Шаг 6. Запуск как systemd-сервис ПОЛЬЗОВАТЕЛЯ (без root)

Раз у тебя llama.cpp и эмбеддинги уже работают через `systemctl --user`, ставим
UI тем же способом. Используй готовый **пользовательский** юнит из репозитория
(`deploy/srv-ai-ui.user.service`) — НЕ путать с `srv-ai-ui.service` (тот для
root/sudo).

```
mkdir -p ~/.config/systemd/user
cp ~/srv-ai-ui/deploy/srv-ai-ui.user.service ~/.config/systemd/user/srv-ai-ui.service
systemctl --user daemon-reload
systemctl --user enable --now srv-ai-ui
systemctl --user status srv-ai-ui
```
Проверить, что поднялось:
```
curl -s http://127.0.0.1:8080/api/health     # ожидаем {"status":"ok"}
journalctl --user -u srv-ai-ui -f            # логи (Ctrl+C — выйти из просмотра)
```

### Частые ошибки (именно они ломают запуск)

- **`status=217/USER`** — в юните указаны `User=`/`Group=`. В пользовательском
  юните их быть НЕ должно (сервис и так работает от тебя). В нашем
  `srv-ai-ui.user.service` их нет — используй именно его.
- **`added as a dependency to a non-existent unit multi-user.target`** — в
  `[Install]` стоит `WantedBy=multi-user.target`. В user-режиме нужно
  `WantedBy=default.target` (в нашем файле уже так).
- **Сервис не видит свои файлы / падает** — из-за `ProtectHome=true` (прячет
  домашний каталог, где лежит приложение). В user-юните эту строку не ставим.
- **`Failed to enable ... Access denied` / запрос пароля polkit** — это ты
  случайно вызвал БЕЗ `--user` (системный systemd, куда прав нет). Всегда
  добавляй `--user`.

## Шаг 7. Автозапуск после перезагрузки сервера

Чтобы user-сервисы стартовали до входа по SSH, нужен «linger». Проверь:
```
loginctl show-user "$USER" | grep Linger
```
Если `Linger=yes` — всё, `enable --now` уже обеспечил автозапуск. Если `no` и
включить не даёт (нужен админ) — сервис поднимется при первом входе по SSH; либо
попроси админа один раз выполнить `loginctl enable-linger <твой_логин>`.

> Запасной вариант без systemd вообще: `nohup ~/srv-ai-ui/venv/bin/python -m app
> > ~/srv-ai-ui/app.log 2>&1 &` и строка `@reboot ~/srv-ai-ui/venv/bin/python -m
> app` в `crontab -e`.

---

## ГЛАВНОЕ по безопасности (вместо firewall)

Прямой доступ пользователей к модели в обход UI должен быть закрыт. Раз ты
управляешь запуском llama.cpp — **привяжи её к localhost** при старте:
```
llama-server --host 127.0.0.1 --port 8000 --no-webui ...   # + --jinja, --mmproj и т.д.
```
- `--host 127.0.0.1` — модель доступна только процессам на самом сервере (нашему
  UI), но не из сети. Снаружи порт 8000 закрыт — то же, что дал бы firewall.
- `--no-webui` — отключает штатный веб-интерфейс llama.cpp (требование ТЗ §1).

Сервис эмбеддингов (**8001**): если LightRAG пока на другой машине — ему нужен
доступ к 8001, поэтому localhost не подойдёт. Вариант без прав: держать LightRAG
тоже на этом сервере (тогда и 8001 привязываем к 127.0.0.1). Когда LightRAG
переедет на сервер — закрывается так же, как модель. До тех пор доступ к 8001
извне — известный временный компромисс (зафиксируй в Положении).

---

## Обслуживание

**Обновление:**
```
cd ~/srv-ai-ui
git pull
source venv/bin/activate
pip install -r requirements.txt
pkill -f "uvicorn app.main:app"
nohup venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 > app.log 2>&1 &
```

**Бэкап** (root не нужен):
```
~/srv-ai-ui/deploy/backup.sh
```
Создаст архив (копия базы + `.env`) в `data/backups`. Для регулярного — добавь в
свой crontab: `0 2 * * * ~/srv-ai-ui/deploy/backup.sh >/dev/null 2>&1`

---

## Чего этот способ НЕ даёт (и когда всё же нужен сисадмин)
- Порт **80/443** (красивый адрес без `:8080`) и HTTPS-сертификат — это уже reverse
  proxy (nginx), ставится с root. Для внутреннего доступа по `http://СЕРВЕР:8080`
  не обязательно.
- Жёсткие правила firewall на уровне ОС. Мы заменяем их привязкой служб к
  localhost — для одного сервера этого достаточно.
- Если политика ИБ требует именно системный сервис и firewall — эти два пункта
  попроси сделать сисадмина разово (см. `deploy/README-deploy.md`), всё остальное
  ты разворачиваешь и обновляешь сам.
