# Security policy

Please report security issues privately through the repository host's security-advisory feature rather than a public issue.

`knsole-pal` stores only agent labels and terminal targets in a mode-0700 runtime directory with mode-0600 files. It never stores prompts or transcripts. Hook installation preserves unrelated entries, creates a private backup before changes, and refuses malformed configuration unless `--force` is explicitly supplied.
