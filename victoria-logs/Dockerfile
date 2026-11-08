FROM victoriametrics/victoria-logs:v1.53.0

# Upstream's defaults, including the one-week retention. On the
# container's own address: compose publishes it on the host's loopback only and puts it on the edge network, where
# Caddy is the one way in.
CMD ["-httpListenAddr=0.0.0.0:9428", \
     "-storageDataPath=/victoria-logs-data", \
     "-loggerFormat=json"]
