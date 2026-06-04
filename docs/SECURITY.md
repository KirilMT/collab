# Collab Runtime — Security

Security practices for developing, testing, and operating the `collab-runtime` package.

---

## Threat model (summary)

| Surface                            | Risk                                   | Mitigation                                                       |
| ---------------------------------- | -------------------------------------- | ---------------------------------------------------------------- |
| Subprocess spawn (`git`, OS tools) | Command injection, arbitrary execution | `collab/safe_subprocess.py`, `collab/platform_probe.py`          |
| Supabase credentials               | Data exfiltration, privilege abuse     | `.env` only; never commit secrets                                |
| Force-release admin paths          | Unauthorized lock removal              | Requires `SUPABASE_SERVICE_ROLE_KEY`                             |
| Watcher / daemon                   | Orphan or hostile child processes      | Validated argv (`python -m collab.lock_client watch`), PID files |

---

## Subprocess hardening (Phase 5)

Production code must not call `subprocess.run`, `check_output`, or `Popen` directly except inside:

- `collab/safe_subprocess.py`
- `collab/platform_probe.py`

Enforced by `tests/backend/security/test_no_raw_subprocess.py` and `test_subprocess_invariants.py`.

### `safe_subprocess` rules

- Resolve executables to absolute paths where possible.
- Watcher spawn must use `python -m collab.lock_client watch` (validated tuple).
- Timeouts and typed errors (`collab/errors.py`) at CLI boundaries.

### Running security scans locally

```powershell
.\.venv\Scripts\python.exe -m bandit -r collab/ scripts/ -ll
```

CI runs the same Bandit profile via `scripts/validate_code.py`.

---

## Secrets and configuration

| Variable                    | Sensitivity | Guidance                                              |
| --------------------------- | ----------- | ----------------------------------------------------- |
| `SUPABASE_URL`              | Low         | Project URL; still do not commit `.env`               |
| `SUPABASE_ANON_KEY`         | Medium      | Client-side key; RLS must protect data                |
| `SUPABASE_SERVICE_ROLE_KEY` | **High**    | Admin force-release only; never in extensions or logs |
| `DEVELOPER_ID`              | Low         | Identity label for locks                              |

Copy from `.env.example`. Use per-developer `.env` files excluded by `.gitignore`.

---

## Pre-commit and pre-push hooks

Hooks call `scripts/validate_code.py --quick` (commit) or full validation (push). They:

- Block commits that fail lint, type checks, or tests on staged Python changes.
- Run Bandit on `collab/` and `scripts/`.

Never bypass hooks with `--no-verify` unless you understand the risk.

---

## Dependency supply chain

- Pin versions in `requirements.txt` / `requirements-dev.txt`.
- Release workflow (`.github/workflows/publish.yml`) builds wheels, runs install smoke tests, and can emit SBOM-style dependency metadata in `dist/`.
- Review Dependabot PRs; auto-format workflow keeps style consistent.

---

## Reporting issues

Report security bugs via [GitHub Issues](https://github.com/KirilMT/collab/issues) with the `type: security` label. Do not paste live keys or customer data in issues or PRs.
