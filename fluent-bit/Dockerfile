FROM fluent/fluent-bit:5.2.0

COPY fluent-bit.yaml parsers.yaml /fluent-bit/etc/

CMD ["--config=/fluent-bit/etc/fluent-bit.yaml"]
