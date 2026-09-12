# Security policy

Wenveil（文隐） is intended for local processing of authorized documents. The public repository must never
contain source documents, OCR output, masked or restored documents, filenames that identify a client or
deal, encrypted mappings, passwords, model checkpoints, or local machine paths.

## Reporting a problem

Do not open a public issue with the affected document, filename, mapping, password, or a raw log. Remove
the material from any unpushed local history and contact the repository maintainer through a private channel.
If the material has already been pushed, treat it as exposed: rotate credentials, revoke mappings where
possible, and request history removal before continuing publication.

Safe reports should contain only a repository-relative path, commit identifier, line number, category, and a
minimal synthetic reproduction. Never include the original surface or a recoverable mapping.

## Release checks

Before publishing, follow [the public-release checklist](docs/PUBLIC-RELEASE-CHECKLIST.md) and verify both
the current tree and all reachable Git history. A file that is merely deleted in the latest commit can still
be recovered from an earlier commit.
