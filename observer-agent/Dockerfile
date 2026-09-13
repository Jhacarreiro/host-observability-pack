FROM python:3.12-alpine
RUN apk add --no-cache iproute2 iputils bind-tools curl
WORKDIR /app
COPY app.py /app/app.py
CMD ["python", "/app/app.py"]
