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
| `apt install poppler-utils` | `conda install poppler` (без root) или пропустить |
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
Если на установке `natasha` падает `Failed building wheel for docopt`:
```
pip install --use-pep517 docopt
pip install -r requirements.txt
```

## Шаг 3. poppler без root (для PDF-сканов, опционально)

Только если нужны PDF-сканы (картинки). Обычные PDF/docx/xlsx работают без него.
- Если используешь **conda**: `conda install -c conda-forge poppler`
- Иначе можно пропустить — при загрузке скана будет понятная ошибка, остальное
  работает.

Проверка: `pdftoppm -h` должна показать справку.

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

## Шаг 6. Запуск, который переживёт выход из SSH

Через `nohup` (просто и всегда работает):
```
nohup venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 > ~/srv-ai-ui/app.log 2>&1 &
```
Проверить, что поднялось:
```
curl -s http://127.0.0.1:8080/api/health      # ожидаем {"status":"ok"}
tail -f ~/srv-ai-ui/app.log                    # логи (Ctrl+C чтобы выйти из просмотра)
```
Остановить:
```
pkill -f "uvicorn app.main:app"
```

> Альтернатива — `tmux`: `tmux new -s srvai`, внутри запусти uvicorn обычной
> командой, отцепись `Ctrl+B` затем `D`. Вернуться: `tmux attach -t srvai`.

## Шаг 7. Автозапуск после перезагрузки сервера (без root)

Сделай скрипт запуска `~/srv-ai-ui/run.sh`:
```
#!/usr/bin/env bash
cd "$HOME/srv-ai-ui"
exec venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8080 >> app.log 2>&1
```
```
chmod +x ~/srv-ai-ui/run.sh
```
Добавь в **пользовательский** crontab (root не нужен):
```
crontab -e
```
и впиши строку:
```
@reboot ~/srv-ai-ui/run.sh
```
Теперь после ребута сервера интерфейс поднимется сам под твоим пользователем.

> Если на сервере разрешён «linger», можно вместо crontab использовать
> `systemctl --user` — но crontab-вариант работает везде и не требует прав.

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
