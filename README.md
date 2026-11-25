# Temporary ANS v0.4.0 source import

This branch transports the user-approved source archive in small requests.
The archive and all 200 files are verified before an import commit is created.
The workflow only runs for this exact branch and the commit message
`ANS_IMPORT_READY_V040`; it refuses to publish if main is no longer the
original reviewed commit. The final main tree contains only the original
200 source and evidence files. This branch is not merged into main.

No source code from the archive is executed. No repository settings are
changed, and no force push is used. The job-scoped token is used only for
this repository and is not saved by the importer.
