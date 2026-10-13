FROM python:3.11-slim
WORKDIR /app
COPY requirements-vps.txt ./
RUN pip install --no-cache-dir -r requirements-vps.txt
COPY local_run.py init_config.py turnstile_solver.py ./
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/local_run.py"]
