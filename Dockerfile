FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .

EXPOSE 8802
CMD ["python", "server.py", "--transport", "http", "--port", "8802"]
