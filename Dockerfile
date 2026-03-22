FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir flask gunicorn

COPY . .

RUN mkdir -p logs

CMD ["sh" , "-c" ,"guicorn --bind 0.0.0.0:$PORT --timeout 120 --worker 1 wsgi:app"]
