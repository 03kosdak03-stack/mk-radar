FROM python:3.12-slim
WORKDIR /app
COPY app.py replies.py ./
COPY web ./web
COPY seed ./seed
ENV PYTHONUNBUFFERED=1 DATA_DIR=/var/data
EXPOSE 8080
CMD ["python", "app.py"]
