FROM fluent/fluent-bit:5.1.5

COPY fluent-bit.yaml parsers.yaml /fluent-bit/etc/

CMD ["--config=/fluent-bit/etc/fluent-bit.yaml"]
