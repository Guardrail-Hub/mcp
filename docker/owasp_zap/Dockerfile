FROM zaproxy/zap-stable

USER root
RUN mkdir -p /zap/sessions \
 && chown -R 1000:1000 /zap/sessions \
 && chmod -R 755 /zap/sessions

# curl is required by register-worker.sh (self-registration / heartbeat).
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY docker/owasp_zap/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/owasp_zap/register-worker.sh /usr/local/bin/register-worker.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/register-worker.sh

# The entrypoint starts ZAP (given as the container command) alongside the
# registration/heartbeat sidecar — this is what lets a worker announce itself
# on startup with no server-side prefix/index configuration.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
