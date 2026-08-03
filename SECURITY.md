# Security policy

## Supported versions

The latest released minor version receives security fixes. Older versions may be
asked to upgrade before a report is investigated.

## Reporting a vulnerability

Use GitHub's **Report a vulnerability** button in the Security tab of this
repository. Please do not open a public issue for an undisclosed vulnerability.
Include the affected version, terminal layer, reproduction steps, impact, and any
suggested mitigation. You should receive an acknowledgement within seven days.

## Security boundaries

`knsole-pal` stores only short agent labels and terminal routing targets in a
mode-0700 runtime directory with mode-0600 files. It never stores prompts,
responses, transcripts, or command output and performs no network requests.

Hook installation preserves unrelated entries, creates a private backup before
changes, and refuses malformed configuration unless `--force` is explicitly
supplied. The `run` subcommand executes the local command explicitly supplied
after `--`; that behavior is intentional and does not use a shell.

See [docs/SECURITY-REVIEW.md](docs/SECURITY-REVIEW.md) for the current threat
model and release audit.
