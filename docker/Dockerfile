FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ src/
COPY data/ data/
COPY demo/ demo/

# Переменные окружения приходят из docker-compose `environment:` (или -e при
# docker run) — pydantic-settings читает их из os.environ независимо от .env.
# Файла .env в образе нет и не нужен; не копируем несуществующий .env.example.

# Expose port
EXPOSE 8000

# Run app
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
