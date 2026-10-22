FROM python:3.13-slim
WORKDIR /app
COPY packages/finance_engine /app/packages/finance_engine
COPY services/api /app/services/api
RUN pip install --no-cache-dir /app/packages/finance_engine /app/services/api
WORKDIR /app/services/api
RUN useradd -r app && chown -R app /app
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
