# Как запустить и задеплоить

## 1. Локально (без Docker)
```bash
cd Novaroll
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt      # для разработки: pip install -r requirements-dev.txt
cp .env.example .env                 # впишите DEEPSEEK_API_KEY
streamlit run app/streamlit_app.py   # откроется http://localhost:8501
```
Ключи берутся из `Novaroll/.env` (в git не попадает). Переменные окружения имеют приоритет над файлом.

## 2. Docker
```bash
cd Novaroll
docker compose up --build            # http://localhost:8501
```
`docker-compose.yml` подхватывает `env_file: .env` — ключи в образ не «запекаются».
Проверка живости контейнера: `curl -f http://localhost:8501/_stcore/health`.

Собрать и запустить вручную:
```bash
docker build -t novaroll-crm-card .
docker run --rm -p 8501:8501 --env-file .env novaroll-crm-card
```

## 3. Streamlit Community Cloud (публикация)
Репозиторий `git@github.com:petrobond/test-jobs.git` — **публичный**, поэтому:

1. Зайти на https://share.streamlit.io под **владельцем репозитория** (аккаунт `petrobond`),
   чтобы авто-пере-деплой на `git push` работал.
2. «New app» → репозиторий `petrobond/test-jobs`, ветка `main`, **Main file path:** `Novaroll/app/streamlit_app.py`.
3. В `Advanced settings → Secrets` вставить содержимое `.streamlit/secrets.toml.example`
   с реальным `DEEPSEEK_API_KEY` (приложение само переносит `st.secrets` в окружение).
4. Deploy. После этого приложение доступно по ссылке `https://<app>.streamlit.app`.

> Почему именно так: Docker нужен для воспроизводимого локального запуска, а Streamlit Cloud —
> нативная площадка для Streamlit-приложения (логин по GitHub, секреты в UI, авто-деплой).
> Обе площадки независимы и не требуют Яндекс Облака.

## Как объяснить менеджеру за 1 минуту
«Вы получаете письмо — вставляете в окошко, нажимаете кнопку и получаете готовую карточку:
компания, контакт, суть, продукт, срочность. Карточка сразу показывает, насколько система уверена.
Если уверенность низкая или чего-то в письме не хватило — загорается жёлтый флажок «проверьте»,
и вы тратите секунды на проверку, а не на ручной перенос. Меньше опечаток, ничего не теряется».

## Эксплуатация (черновик)
- Наблюдаемость: в UI показаны модель, время ответа, токены, «замечания» карточки.
- Стоимость: ~$0.0003 на письмо (DeepSeek flash, off-peak) — сотни писем в день стоят центы.
- Секреты: только в `.env`/Streamlit Secrets, в git не попадают (см. корневой `.gitignore`).
