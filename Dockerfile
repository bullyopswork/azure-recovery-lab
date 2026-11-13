FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/bullyopswork/azure-recovery-lab"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LAB_STATE_DIR=/tmp/recovery-lab
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes -r requirements.txt && useradd --uid 10001 --create-home lab
COPY --chown=10001:10001 recovery_lab ./recovery_lab
USER 10001:10001
EXPOSE 8080
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--access-logfile", "-", "recovery_lab.app:application"]
