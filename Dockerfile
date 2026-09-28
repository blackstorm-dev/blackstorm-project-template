FROM python:3.13-alpine

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .

USER 10001:10001
EXPOSE 8080 9090
CMD ["python", "app.py"]
