# Collab Runtime — Performance

Guidance for keeping local development fast and the watcher efficient.

---

## Validation runtime

| Mode           | Command                                   | When to use                               |
| -------------- | ----------------------------------------- | ----------------------------------------- |
| Quick (staged) | `python scripts/validate_code.py --quick` | Pre-commit; changed files only            |
| Full CI        | `python scripts/validate_code.py`         | Before push; enforces ≥85% total coverage |

Pre-push hook runs **full** validation. Expect several minutes on a cold run.

### Tips

- Use `pytest` with a narrow path while iterating:
  `pytest tests/backend/unit/lock_client/test_lock_client_cli.py -q`
- `COLLAB_TEST_MODE=1` avoids real daemon shutdown side effects in tests.
- Testmon (`pytest-testmon`) is available in dev dependencies for incremental reruns.

---

## Watcher and daemon

The background watcher polls git status and reconciles locks with Supabase.

| Setting                     | Default   | Effect                                   |
| --------------------------- | --------- | ---------------------------------------- |
| `daemon-start --interval`   | 5 seconds | Git poll frequency; lower = more CPU     |
| `daemon-start --timeout`    | 0 (off)   | Auto-stop after idle minutes             |
| `COLLAB_AUTO_START_WATCHER` | `1`       | Auto-start on `collab active` / `status` |

PyCharm / IDE watcher (`collab-watcher`) uses the same interval model.

### Reducing overhead

- Increase `--interval` on slow laptops or large repos (e.g. 15–30s).
- Stop the watcher when not collaborating: `collab daemon-stop`.
- Avoid multiple overlapping watcher processes; use `collab daemon-status` and `collab cleanup` if needed.

---

## Network and Supabase

- Lock RPCs are short; Realtime keeps UI fresh without polling every command.
- CLI commands use quiet logging for httpx/supabase on user-facing paths to reduce console I/O.
- Batch operations (`acquire-batch`, `release-batch`) reduce round-trips vs. repeated single acquires.

---

## Packaging and install

- Editable install (`pip install -e .`) is fastest for active development.
- Wheel smoke tests in CI verify install size and import time; local `python -m build` is only needed before release.

---

## Benchmarking changes

When optimizing hot paths:

1. Record baseline: `pytest tests/backend/unit/... -q --durations=10`
2. Prefer measuring reconcile/parsing unit tests before full suite.
3. Document before/after in the PR if validation time shifts materially.
