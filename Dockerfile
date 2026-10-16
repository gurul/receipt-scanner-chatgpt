FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml cost_engine.py ./
COPY receipt_plugin ./receipt_plugin
COPY requirements-plugin.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements-plugin.lock && pip install --no-cache-dir --no-deps . && useradd --create-home --uid 10001 scanner && mkdir /data && chown scanner /data
USER scanner
ENV HOST=0.0.0.0 PORT=8000 DATABASE_PATH=/data/receipts.sqlite3
EXPOSE 8000
CMD ["receipt-scanner-server"]
