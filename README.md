# srv-ai webUI

Корпоративный веб-интерфейс для локальной LLM (llama.cpp, Qwen) — чат, заметки, календарь,
поиск по базе знаний (LightRAG). Разрабатывается по [TZ_srv-ai_webui.md](TZ_srv-ai_webui.md).

Статус: этап 1 — каркас (аутентификация, сессии, админка пользователей, пустой UI).

## Запуск для разработки

```
python -m venv venv
venv\Scripts\activate           # Windows
source venv/bin/activate        # Linux/macOS
pip install -r requirements.txt
copy env.example .env           # Windows: copy, Linux/macOS: cp
python -m app.create_admin
uvicorn app.main:app --reload --port 8080
```

Открыть http://127.0.0.1:8080/login

## Тесты

```
pytest
```
