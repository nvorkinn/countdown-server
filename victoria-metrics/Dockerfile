FROM victoriametrics/victoria-metrics:v1.156.0

# The vmui dashboards (its Dashboards tab), read from here each time the page loads.
COPY dashboards/ /vmui-dashboards/

# It scrapes nothing: metrics are pushed to it. Otherwise upstream's defaults,
# including the one-month retention. On the container's own address: compose publishes it
# on the host's loopback only and puts it on the edge network, where Caddy is the one way in.
CMD ["-httpListenAddr=0.0.0.0:8428", \
     "-storageDataPath=/victoria-metrics-data", \
     "-vmui.customDashboardsPath=/vmui-dashboards", \
     "-loggerFormat=json"]
