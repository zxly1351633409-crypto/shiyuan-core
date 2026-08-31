# Public release audit

This repository was built as a clean, allow-listed export rather than by
publishing the private development repository.

Release checks:

- new Git history with no private parent commits;
- empty default profile and confirmed-memory snapshot;
- no runtime databases, Vault, conversation archives, logs or backups;
- no home-directory, drive-letter, NAS, account or private-network values;
- no committed tokens, API keys or password files;
- `python scripts/public_release_audit.py .` checks common identity and secret markers;
- maintainers can repeat `--deny-term` for source-specific names, project codenames or device labels;
- automated tests run against a temporary local database;
- remote repository contents rechecked after push.

The audit proves the checked repository does not contain the searched classes
of private data. It cannot prove that future contributors will never commit
sensitive data; keep the allow-list and scanning discipline for every release.
