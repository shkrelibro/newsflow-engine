FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
VOLUME ["/app/data", "/app/docs"]
# run forever, one fetch cycle every 15 minutes; exports land in /app/docs
CMD ["python", "-m", "newsflow", "loop", "--every", "15"]
