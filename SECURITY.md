# Security Policy

Project Atlas investigates repositories and writes durable summaries of what it finds. That combination makes path boundaries, prompt injection, command execution, and accidental disclosure part of the security model.

## Reporting a vulnerability

Do not open a public issue containing exploit details, secrets, private paths, or affected repository data.

Use the repository's private [GitHub Security Advisory form](https://github.com/ddenny-s/project-atlas/security/advisories/new). Include:

- the affected version or commit;
- the adapter and installation method;
- the smallest synthetic reproduction;
- expected and observed behavior;
- security impact and any known workaround.

If private advisory reporting is unavailable, open a public issue that contains no vulnerability details and asks the maintainer to establish a private channel.

## Security-relevant behavior

Reports are especially useful for:

- reading an excluded or explicitly forbidden path;
- exposing secrets in generated artifacts, logs, or test output;
- following repository content as instructions when that content conflicts with user or project policy;
- modifying product code, data, infrastructure, or production systems during a map-only task;
- unsafe installer overwrite, path traversal, symlink handling, or deletion;
- executing network access or commands without the required authorization;
- falsely claiming complete coverage or verified runtime behavior;
- losing user-authored atlas content during incremental refresh;
- adapter packaging that loads files outside the installed plugin boundary.

## Local process boundary

Install and adapter-sync transactions defend their public source and destination names against accidental concurrent replacement, use no-follow descriptors, and restore a raced object when an atomic move reveals the wrong identity. They do not treat another malicious process already running as the same operating-system user as an isolation boundary: that process can directly enumerate, replace, or delete files the user owns. Internal cleanup names are random and rechecked to make ordinary writer races fail closed, not to sandbox a compromised local account. Run installation and write-mode synchronization only in a trusted local session, and investigate unexpected preserved quarantine paths before retrying.

Each standalone installer binds its lock, staging root, and promoted target to one run-specific nonce through private ownership markers. An existing installation is instead bound to an exact descriptor-relative payload manifest before it moves to backup, so the installer need not modify the user's prior bytes to claim ownership. Identity and the applicable marker or manifest are checked around each no-replace atomic move and cleanup quarantine. The promoted target keeps its marker through verification; removing that marker is the commit cutpoint. Missing or changed proof makes cleanup or restore fail closed and preserves the ambiguous target, backup, staging tree, lock, or quarantine for manual inspection.

Standalone installer rollback covers ordinary errors and trappable signals, not guaranteed power-loss durability. A crash can leave the public target missing while the prior copy is preserved under `.skill-backups/project-atlas/`, or leave ambiguous staging and lock state. Adapter synchronization has a v2 recovery journal with journal-bound tree digests and a separate commit receipt. Before it cleans or quarantines a prepared tree, synchronization requires the journal-bound role marker and exact `prepared_tree_sha256` proof; the same boundary uses the recorded original-tree digest for a prior adapter. It rechecks proof after quarantine and preserves a markerless or changed state for manual inspection. Damaged metadata, missing metadata, a v1 journal, or an orphan receipt likewise requires manual inspection. Do not delete a preserved target, backup, staging tree, lock, quarantine, tombstone, receipt, or journal until its identity and ownership are understood.

## Excluded-scope and host-tool boundaries

Repository ignore files are scope metadata, not proof that ignored content is harmless. A repository owner can hide runtime code or high-impact configuration behind an ignore rule. Project Atlas intentionally reports only aggregate exclusion counts because ignored names and types can themselves disclose private information; it does not read ignored contents or claim they are covered. Exact `.gitignore` evaluation runs in an isolated temporary worktree made from stable in-scope copies; source `.git` metadata, `info/exclude`, repository/worktree/global/system Git config, and external excludes files are not inputs. When the repository or its ignore policy is untrusted, treat automatic depth selection and completeness as limited until a human reviews the ignore policy and either approves the boundary or separately authorizes a privacy-safe inspection.

The helper and standalone installers trust the invoking account and its host environment, but reject `git`, `rg`, `python3`, or `diff` executables resolved inside the inspected project, installer clone, or an enclosing Git repository and reject executables with unsafe ownership or write permissions. Installer entrypoints use fixed `/bin/bash -p`; their bootstrap resolves Python symlinks and validates the resolved file and parent-directory modes before execution. External `python3` and `diff` paths are rejected when an ancestor directory is group-writable, or world-writable without the sticky bit, with one narrow package-manager exception: the standard Homebrew Cellar directories are accepted when owned by root or the invoking user, not world-writable, and assigned to one of the process's groups. A malicious peer who can write through that Homebrew group remains inside the trusted host-account boundary. Keep repository directories out of `PATH` as defense in depth; compromise of the invoking account or protected system executables remains outside this boundary.

## User safety responsibilities

Project Atlas cannot determine whether every file is safe to disclose to an AI provider. Before running it on a sensitive repository:

1. Review the selected host's data controls and organizational policy.
2. Define excluded paths and prohibited operations in project instructions.
3. Keep secrets, production dumps, and regulated data outside the inspection scope.
4. Review generated artifacts before committing, sharing, or publishing them.
5. Independently verify high-impact claims and maintain normal backups, monitoring, and recovery procedures.

Treat automated leakage checks as advisory defense in depth, never as proof that sensitive material is absent. A human privacy and security review is mandatory before generated artifacts are committed, shared, or published.

The protocol is designed to minimize and label uncertainty, not to eliminate all security or operational risk.
