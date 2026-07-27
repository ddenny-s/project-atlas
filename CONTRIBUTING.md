# Contributing to Project Atlas

Thank you for helping make project mapping more accurate, portable, and safe. Contributions can improve the protocol, an adapter, scripts, tests, fixtures, or documentation.

## Before you start

- Read the [methodology](docs/methodology.md), [depth levels](docs/depth-levels.md), [output contract](docs/outputs.md), and [adapter architecture](docs/adapters.md).
- Search existing issues and pull requests before opening overlapping work.
- Use an issue to discuss changes that alter evidence semantics, depth selection, output compatibility, or adapter support.
- Report vulnerabilities privately according to [SECURITY.md](SECURITY.md).

## Architecture boundaries

The protocol is independent of any AI tool. Keep these responsibilities separate:

- `core/` defines the evidence model, workflow, safety rules, and output contract.
- `adapters/codex/` packages the primary, full Codex integration.
- `adapters/claude-code/` packages the secondary Claude Code integration.
- `.agents/plugins/marketplace.json` and `.claude-plugin/marketplace.json` expose the corresponding packages.
- `scripts/` contains safe, repeatable mechanics; it must not encode host-specific protocol semantics.
- `tests/` checks the core contract, packaging, installers, and representative project scenarios.

An adapter may translate invocation, discovery, manifest, and host-permission details. It must not change evidence labels, depth meaning, output semantics, or safety guarantees. Adapter packages must be self-contained because plugin hosts can copy them into isolated caches.

## Development workflow

1. Create a focused branch from `main`.
2. Add or update a failing test for behavior changes when the existing test framework can express the case.
3. Make the smallest coherent change in the owning layer.
4. If core behavior changed, update both adapters and their conformance coverage.
5. Update and review the primary Russian documentation first. Publish or refresh
   an English translation only after the corresponding Russian copy is approved.
6. Run the relevant checks and inspect the generated artifacts.
7. Submit a pull request that explains the behavior, evidence, risk, and validation.

Do not include repository secrets, personal data, customer material, production exports, or proprietary source in fixtures, issue descriptions, screenshots, or test output. Synthetic fixtures should be small enough to audit and rich enough to exercise the intended boundary.

## Validation

Development and bundled scripts require Python 3.10 or newer. The CI matrix runs the minimum supported Python and a current Python release.

Run the complete local test suite:

```bash
python3 scripts/sync_adapters.py --check
python3 -m unittest discover -s tests -v
```

Also run whitespace and patch checks:

```bash
git diff --check
```

Changes to installation or packaging should be exercised in a temporary profile, not against a maintainer's existing plugin or skill directories. Changes to path handling should include a directory containing spaces. Changes to incremental refresh must verify that existing user additions are preserved.

For adapter work, validate the host manifests with the current host tooling when available. Do not claim an end-to-end install is verified unless it was tested from the same source and release form described to users.

## Documentation style

- Use direct, testable language.
- Distinguish requirements from recommendations and observed behavior from proposals.
- Keep commands copyable and use repository-relative paths.
- Do not publish machine-specific paths, credentials, private project names, or fixture data copied from real systems.
- Keep internal links valid and update `docs/README.ru.md` when the primary usage contract changes.

## Pull request checklist

- The change belongs in the layer being edited.
- Existing user files and unrelated behavior remain intact.
- Tests cover the new or changed contract.
- Core and adapter behavior do not drift.
- Documentation and examples match the implementation.
- Security and privacy implications are described.
- All reported checks include exact commands and outcomes.

By contributing, you agree that your contribution is licensed under the repository's [MIT License](LICENSE).
