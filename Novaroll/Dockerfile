# Dockerfile — прототип "Письмо -> карточка CRM" (Streamlit + LLM)
#
# Один образ: приложение Streamlit, которое тянет ключи из окружения (docker-compose env_file).
# Многостадийность не нужна: dev-зависимости (pytest) в образ не ставим, ставим только рантайм.

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl — для healthcheck Streamlit
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Код и данные (ключи сюда НЕ копируем — только через env)
COPY llm/ ./llm/
COPY pipeline/ ./pipeline/
COPY config/ ./config/
COPY eval/ ./eval/
COPY scripts/ ./scripts/
COPY app/ ./app/
COPY data/ ./data/
COPY .streamlit/ ./.streamlit/

# непривилегированный пользователь
RUN useradd -m -u 2100 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8501/_stcore/health || exit 1

CMD ["streamlit", "run", "app/streamlit_app.py", \
     "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
