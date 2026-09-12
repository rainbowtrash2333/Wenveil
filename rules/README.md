# Public rules boundary

The dictionaries in this directory are public, reviewable defaults. They must not contain client names,
deal names, project titles, document filenames, private contacts, or values copied from an authorized source
document.

`projects.txt` is intentionally empty. Keep project-specific values in a local ignored file or a private
configuration passed with `python -m desensitize -c <local-config.yaml>`.

Before publishing, inspect both the working tree and reachable Git history. Removing a line only from the
latest revision is not sufficient if an earlier commit still contains it.
