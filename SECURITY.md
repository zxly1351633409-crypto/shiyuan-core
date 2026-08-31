# Security

Shiyuan Core stores sensitive personal context. Treat it like a password
manager or private notebook, not like a public web service.

- Keep `SHIYUAN_CORE_TOKEN` secret and use at least 32 random characters.
- Do not commit `core.env`, SQLite databases, Vault files, history archives,
  semantic indexes, logs, offline queues or backups.
- Bind to localhost unless you intentionally place the service behind an
  authenticated private network or a TLS reverse proxy.
- Review candidate memories before confirming them.
- Do not import workplace or third-party data unless policy and consent allow it.
- Backups should be encrypted before leaving the device.

For a vulnerability, use GitHub's private security advisory flow instead of a
public issue. Do not include real memories, tokens or database files in reports.
