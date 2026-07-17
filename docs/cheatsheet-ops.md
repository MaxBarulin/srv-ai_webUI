# Шпаргалка по эксплуатации srv-ai webUI

Все команды выполняются на сервере под своим пользователем (без root).
Код — в `~/srv-ai-ui`, сервис — `systemctl --user srv-ai-ui`.

## Ежедневное

```bash
# Обновление приложения
cd ~/srv-ai-ui
git pull                                  # забрать свежий код
systemctl --user restart srv-ai-ui        # перезапустить сервис

# Быстрая проверка «всё ли живо»
systemctl --user status srv-ai-ui --no-pager    # Active: active (running)?
curl -s http://127.0.0.1:8001/api/health        # {"status":"ok"}
journalctl --user -u srv-ai-ui -n 20 --no-pager # хвост лога: нет ли traceback
```

## Сервисы

```bash
systemctl --user restart srv-ai-ui     # перезапуск UI (после git pull / правки .env)
systemctl --user stop srv-ai-ui        # остановить (обязательно перед миграцией БД!)
systemctl --user start srv-ai-ui       # запустить
systemctl --user list-units 'llama*' 'srv*' --no-pager  # что вообще крутится
systemctl --user restart llama-embed   # эмбеддинги (для LightRAG)
loginctl show-user $USER | grep Linger # Linger=yes → сервисы живут после ребута
```

## Логи и диагностика

```bash
journalctl --user -u srv-ai-ui -f                 # лог в реальном времени (Ctrl+C — выйти)
journalctl --user -u srv-ai-ui -n 100 --no-pager  # последние 100 строк
journalctl --user -u srv-ai-ui --since "1 hour ago"  # за последний час
curl -s http://127.0.0.1:8000/health              # жив ли llama.cpp
curl -s http://127.0.0.1:8000/props | head -c 300 # параметры модели (n_ctx и др.)
ss -tlnp | grep -E '8000|8001'                    # кто слушает порты
df -h ~ && du -sh ~/srv-ai-ui/data                # место на диске / размер БД
```

## Конфигурация (.env)

```bash
nano ~/srv-ai-ui/.env      # после правки — restart; daemon-reload НЕ нужен
chmod 600 ~/srv-ai-ui/.env # права: только владелец (там DB_KEY!)
```

Ключевые переменные:

| Переменная | Что делает |
|---|---|
| `LLM_BASE_URL` / `LLM_API_KEY` | куда ходить за моделью |
| `LLM_CONTEXT_SIZE` | запасной n_ctx (обычно берётся из /props сам) |
| `RAG_ENABLED` / `RAG_BASE_URL` | база знаний LightRAG |
| `PII_FILTER=true` | маскирование персональных данных |
| `TOOLS_CONFIRM_DESTRUCTIVE` | подтверждение удалений от LLM |
| `DB_KEY` | шифрование БД (менять НЕЛЬЗЯ без rekey!) |
| `CHAT_RETENTION_DAYS` | автоочистка истории (0 = хранить вечно) |

Юнит-файл менял? — тогда нужен daemon-reload:

```bash
nano ~/.config/systemd/user/srv-ai-ui.service
systemctl --user daemon-reload && systemctl --user restart srv-ai-ui
```

## Пользователи

```bash
cd ~/srv-ai-ui && source venv/bin/activate
python -m app.create_admin             # создать администратора (интерактивно)
deactivate
```

Остальное (создание, блокировка, сброс пароля, удаление) — в UI,
раздел «Администрирование».

## Бэкап и восстановление

```bash
# Бэкап = файл БД + .env (надёжнее всего при остановленном сервисе)
systemctl --user stop srv-ai-ui
cp ~/srv-ai-ui/data/srv-ai-ui.db ~/backup/srv-ai-ui.db.$(date +%F)
cp ~/srv-ai-ui/.env ~/backup/env.$(date +%F)   # без него шифрованная БД бесполезна!
systemctl --user start srv-ai-ui

# Восстановление: стоп → вернуть файл на место → старт
```

## База данных

```bash
head -c 16 ~/srv-ai-ui/data/srv-ai-ui.db
# «SQLite format 3» = нешифрованная; бинарный мусор = зашифрована (SQLCipher)

# Заглянуть в НЕшифрованную БД (только чтение):
sqlite3 ~/srv-ai-ui/data/srv-ai-ui.db "SELECT login, role, is_active FROM users;"

# В шифрованную — через python + ключ:
cd ~/srv-ai-ui && source venv/bin/activate
DB_KEY='ключ' python -c "
import os, sqlcipher3
db = sqlcipher3.connect('data/srv-ai-ui.db')
db.execute(\"PRAGMA key = '%s'\" % os.environ['DB_KEY'].replace(\"'\",\"''\"))
print(db.execute('SELECT login, role FROM users').fetchall())"
```

## Откат, если обновление сломало

```bash
cd ~/srv-ai-ui
git log --oneline -5                   # посмотреть, что приехало
git checkout <хеш_рабочего_коммита>    # откатить код на рабочую версию
systemctl --user restart srv-ai-ui
# когда починили: git checkout master && git pull && restart
```

## Может пригодиться

```bash
cd ~/srv-ai-ui && source venv/bin/activate
python -m pytest tests/ -q            # прогнать все тесты на сервере (~20 сек)
pip install -r requirements.txt       # если в обновлении новые зависимости
history -c && history -w              # почистить историю shell (после ввода ключей!)
openssl rand -base64 32               # сгенерировать ключ/пароль

# Сервер ребутнулся и ничего не работает?
systemctl --user status srv-ai-ui     # юниты не найдены? — проверь linger:
loginctl enable-linger $USER          # автозапуск user-сервисов без входа
```

## Три золотых правила

1. `.env` с `DB_KEY` — права `600`, копия ключа в сейфе паролей.
2. Перед любыми манипуляциями с БД — `systemctl --user stop srv-ai-ui`.
3. Бэкап перед обновлением, если в нём есть миграции БД.
