FROM python:3.12-alpine
WORKDIR /app
COPY server.py .
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8080/status || exit 1
CMD ["python", "-u", "server.py"]
