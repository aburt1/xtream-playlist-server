FROM python:3.12-alpine
WORKDIR /app
COPY server.py .
EXPOSE 8080
HEALTHCHECK --interval=5m --timeout=10s \
  CMD wget -qO- http://127.0.0.1:8080/status || exit 1
CMD ["python", "-u", "server.py"]
