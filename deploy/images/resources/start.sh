#!/bin/bash
set -e

GUNICORN_THREADS=${GUNICORN_THREADS:-4}
GUNICORN_WORKERS=${GUNICORN_WORKERS:-2}
GUNICORN_TIMEOUT=${GUNICORN_TIMEOUT:-120}
GUNICORN_WORKER_CLASS=${GUNICORN_WORKER_CLASS:-crm_cskh.gunicorn_worker.FairGthreadWorker}
GUNICORN_KEEPALIVE=${GUNICORN_KEEPALIVE:-0}

echo "Booting Gunicorn with $GUNICORN_WORKERS workers, $GUNICORN_THREADS threads, worker_class=$GUNICORN_WORKER_CLASS..."

exec /home/frappe/frappe-bench/env/bin/gunicorn \
  --chdir=/home/frappe/frappe-bench/sites \
  --bind=0.0.0.0:8000 \
  --threads="$GUNICORN_THREADS" \
  --workers="$GUNICORN_WORKERS" \
  --worker-class="$GUNICORN_WORKER_CLASS" \
  --keep-alive="$GUNICORN_KEEPALIVE" \
  --worker-tmp-dir=/dev/shm \
  --timeout="$GUNICORN_TIMEOUT" \
  --preload \
  frappe.app:application
