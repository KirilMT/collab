<!-- markdownlint-disable MD024 -->

<!-- prettier-ignore -->
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.10.0](https://github.com/KirilMT/collab/compare/v0.9.3...v0.10.0) (2026-07-02)


### Features

* **daemon:** per-worktree teardown + fast worktree-gone reap ([#179](https://github.com/KirilMT/collab/issues/179)) ([9c68c5d](https://github.com/KirilMT/collab/commit/9c68c5da0317cdc54fa115a251327638e1ada187)), closes [#168](https://github.com/KirilMT/collab/issues/168)


### Bug Fixes

* **daemon:** honor PR claims end-to-end and auto-update git hooks ([#181](https://github.com/KirilMT/collab/issues/181)) ([#185](https://github.com/KirilMT/collab/issues/185)) ([34e1e39](https://github.com/KirilMT/collab/commit/34e1e39ed96287637cb0a3289f7b5b9e16282227))
* **daemon:** stop phantom auto-watch locks when local branch is behind upstream ([#184](https://github.com/KirilMT/collab/issues/184)) ([5ffc481](https://github.com/KirilMT/collab/commit/5ffc48176ccd8012bb0b473d92ed7de2ca98f073)), closes [#178](https://github.com/KirilMT/collab/issues/178)
* **locks:** heal orphan lock rows and stop test daemon leaks ([#182](https://github.com/KirilMT/collab/issues/182), [#183](https://github.com/KirilMT/collab/issues/183)) ([#186](https://github.com/KirilMT/collab/issues/186)) ([b6524d1](https://github.com/KirilMT/collab/commit/b6524d1c5eb47d1581a0776bb68fc15253695668))
* **security:** resolve all 13 CodeQL code scanning alerts ([#188](https://github.com/KirilMT/collab/issues/188)) ([7310e34](https://github.com/KirilMT/collab/commit/7310e34e8bb735911738c3fd86b478ad5d43f65f)), closes [#187](https://github.com/KirilMT/collab/issues/187)
* **security:** switch dependabot-autoformat from pull_request_target to pull_request ([#189](https://github.com/KirilMT/collab/issues/189)) ([4da4106](https://github.com/KirilMT/collab/commit/4da41062327aa33e57df1c9a8e22d4630e47186b)), closes [#175](https://github.com/KirilMT/collab/issues/175)

## [0.9.3](https://github.com/KirilMT/collab/compare/v0.9.2...v0.9.3) (2026-07-01)

### Bug Fixes

- **daemon:** prevent orphaned watchers when Agents/worktree IDE windows close ([#166](https://github.com/KirilMT/collab/issues/166)) ([6ed3553](https://github.com/KirilMT/collab/commit/6ed3553416b73301c1add3e9676490b2abfc8ae2))
- **locks:** sticky agent attribution, scratch-file ignores, and self-conflict silence ([#174](https://github.com/KirilMT/collab/issues/174)) ([0e2419f](https://github.com/KirilMT/collab/commit/0e2419f353a5a15cd292e642b26a58cffe97701d))

## [0.9.2](https://github.com/KirilMT/collab/compare/v0.9.1...v0.9.2) (2026-06-28)

### Bug Fixes

- add tomli to pyproject.toml dependencies ([#164](https://github.com/KirilMT/collab/issues/164)) ([b7c7548](https://github.com/KirilMT/collab/commit/b7c7548b7f6adc484e96006ee7cd8e168e3e5520))
- **overlap:** self-heal shallow CI clones in fetch_pr_ref for merge-tree ([#160](https://github.com/KirilMT/collab/issues/160)) ([8b8aa40](https://github.com/KirilMT/collab/commit/8b8aa40e3d9c648f0adbace58575f85f2f90d2a6)), closes [#159](https://github.com/KirilMT/collab/issues/159)
- **setup:** enforce Python version from .python-version ([#163](https://github.com/KirilMT/collab/issues/163)) ([caeb092](https://github.com/KirilMT/collab/commit/caeb09255bcc5ef70eb9fca2edbeeb02131f02af))

## [0.9.1](https://github.com/KirilMT/collab/compare/v0.9.0...v0.9.1) (2026-06-26)

### Bug Fixes

- remove min-hold from stale-lock release to prevent post-merge lock stickiness ([#156](https://github.com/KirilMT/collab/issues/156)) ([7b02d8c](https://github.com/KirilMT/collab/commit/7b02d8c6a2481adc73db3634713c4c664b132c04))

## [0.9.0](https://github.com/KirilMT/collab/compare/v0.8.1...v0.9.0) (2026-06-26)

### Features

- add edit-time cross-branch/agent conflict detection and worktree-aware locking ([#153](https://github.com/KirilMT/collab/issues/153)) ([d34e677](https://github.com/KirilMT/collab/commit/d34e677c621237aa2c033b8b7555f5007bc08b03))
- **overlap:** consolidate merge-tree logic, add line-level PR overlap guard ([#144](https://github.com/KirilMT/collab/issues/144)) ([f74b28c](https://github.com/KirilMT/collab/commit/f74b28ce9ee057eadbdab9e426e7d7dd86f5a1e4)), closes [#143](https://github.com/KirilMT/collab/issues/143)

### Bug Fixes

- **deps:** resolve js-yaml quadratic-complexity DoS via npm overrides ([#146](https://github.com/KirilMT/collab/issues/146)) ([6172945](https://github.com/KirilMT/collab/commit/6172945f50c565846d2fb0a1629f0d2b1b23cd03))
- eliminate sub-minute lock durations caused by criteria mismatch and missing hysteresis ([#152](https://github.com/KirilMT/collab/issues/152)) ([440df19](https://github.com/KirilMT/collab/commit/440df1908db5e027ed584afd6e3477e6933d1ad1)), closes [#151](https://github.com/KirilMT/collab/issues/151)
- **overlap:** ignore exit code in git_version_supports_merge_tree ([#149](https://github.com/KirilMT/collab/issues/149)) ([189dcf8](https://github.com/KirilMT/collab/commit/189dcf8bbd723e87256ec0ee26ee08d1db9ff5e8))
- **schema:** add DROP FUNCTION before CREATE OR REPLACE acquire_lock ([#154](https://github.com/KirilMT/collab/issues/154)) ([1f02c31](https://github.com/KirilMT/collab/commit/1f02c31c0c8e6f232a16ea41686e292289018b4f))

## [0.8.1](https://github.com/KirilMT/collab/compare/v0.8.0...v0.8.1) (2026-06-14)

### Bug Fixes

- **logging:** use local time in extension, prevent false startup notifications, quiet upstream log ([#139](https://github.com/KirilMT/collab/issues/139)) ([c1648d9](https://github.com/KirilMT/collab/commit/c1648d98562f4548fd2bb4e02c67657d7ae2ed2b))
- **pr_overlap:** honor GITHUB_API_URL for GitHub Enterprise Server (GHES) support ([#138](https://github.com/KirilMT/collab/issues/138)) ([c8605db](https://github.com/KirilMT/collab/commit/c8605db984adad3721331f4ba3eaff7afef499d4)), closes [#137](https://github.com/KirilMT/collab/issues/137)

### Code Refactoring

- **cli:** rename restart to daemon-restart ([#135](https://github.com/KirilMT/collab/issues/135)) ([2f8fe5f](https://github.com/KirilMT/collab/commit/2f8fe5fc04b1eece6cc991bd79ee083e636c0b79)), closes [#134](https://github.com/KirilMT/collab/issues/134)

## [0.8.0](https://github.com/KirilMT/collab/compare/v0.7.0...v0.8.0) (2026-06-11)

### Features

- add strict cross-branch overlap prevention on push ([#130](https://github.com/KirilMT/collab/issues/130)) ([6843921](https://github.com/KirilMT/collab/commit/68439214f2407ca64f43ac65dc1fde776efc7e11))
- auto-repair editable install on pull/checkout with post-merge and post-checkout git hooks ([#133](https://github.com/KirilMT/collab/issues/133)) ([99900ae](https://github.com/KirilMT/collab/commit/99900aeb55ad72bce864a6b5615087a6a9d1b109))
- **dashboard:** richer metrics, filtering UX, interactive charts, dark theme, and row details ([#121](https://github.com/KirilMT/collab/issues/121)) ([23b87d8](https://github.com/KirilMT/collab/commit/23b87d8abd333d69793af3cfad9e9b5d8f535110))
- **dashboard:** richer metrics, filtering UX, interactive charts, dark theme, and row details (replaces [#121](https://github.com/KirilMT/collab/issues/121)) ([#125](https://github.com/KirilMT/collab/issues/125)) ([84e74a0](https://github.com/KirilMT/collab/commit/84e74a06dcaf0625edbf0a869b7fe68bdf31e637))
- **overlap:** warn on cross-branch file overlap before merge conflicts ([#124](https://github.com/KirilMT/collab/issues/124)) ([a857896](https://github.com/KirilMT/collab/commit/a857896dae4887540016328d04e40dcc0bb8b11f))

### Bug Fixes

- **daemon:** refresh PID file mtime for dashboard watcher-health ([#123](https://github.com/KirilMT/collab/issues/123)) ([c90ab74](https://github.com/KirilMT/collab/commit/c90ab7463cffdcb8843aecbd84411ecf1c87dc9a)), closes [#120](https://github.com/KirilMT/collab/issues/120)

### Code Refactoring

- **lock_client:** simplify heartbeat grace logic to reduce false shutdowns ([#118](https://github.com/KirilMT/collab/issues/118)) ([3f39c57](https://github.com/KirilMT/collab/commit/3f39c5795cb31d226861779e12c24e4cae919eb7)), closes [#74](https://github.com/KirilMT/collab/issues/74)

## [0.7.0](https://github.com/KirilMT/collab/compare/v0.6.2...v0.7.0) (2026-06-07)

### Features

- **cli:** add --version, restart, ping, info, logs commands and update agent instructions ([#116](https://github.com/KirilMT/collab/issues/116)) ([6338a7e](https://github.com/KirilMT/collab/commit/6338a7e463abda911ecee106c56caa2260bac5b2))

## [0.6.2](https://github.com/KirilMT/collab/compare/v0.6.1...v0.6.2) (2026-06-07)

### Bug Fixes

- **cli:** release() now clears agent-claimed locks; eliminates false-positive 204 ([#113](https://github.com/KirilMT/collab/issues/113)) ([ada512c](https://github.com/KirilMT/collab/commit/ada512cdb122e39f75f95a37ad48ae406a090470)), closes [#112](https://github.com/KirilMT/collab/issues/112)

## [0.6.1](https://github.com/KirilMT/collab/compare/v0.6.0...v0.6.1) (2026-06-06)

### Bug Fixes

- **lock_client:** write -1 sentinel to .shutdown_complete when active() fails during shutdown ([#108](https://github.com/KirilMT/collab/issues/108)) ([e4a5a07](https://github.com/KirilMT/collab/commit/e4a5a0748c8e50498aa15abaf3f6de01ba833494)), closes [#75](https://github.com/KirilMT/collab/issues/75)
- **locks:** allow same-developer lock takeover and clear own agent locks ([#110](https://github.com/KirilMT/collab/issues/110)) ([#111](https://github.com/KirilMT/collab/issues/111)) ([e869de3](https://github.com/KirilMT/collab/commit/e869de31d65683fa8aec81f6b7436417852bf1f9))

## [0.6.0](https://github.com/KirilMT/collab/compare/v0.5.1...v0.6.0) (2026-06-05)

### Features

- **attribution:** automate AI-agent edit attribution across IDEs ([#102](https://github.com/KirilMT/collab/issues/102)) ([c66f98f](https://github.com/KirilMT/collab/commit/c66f98fe13ef382d2e1323b58d2d95ec90160c4a))
- **setup:** zero-touch Supabase config via pre-filled .env.example ([#80](https://github.com/KirilMT/collab/issues/80)) ([1101097](https://github.com/KirilMT/collab/commit/1101097428e208eba9c489e0ef61ab874079d593)), closes [#79](https://github.com/KirilMT/collab/issues/79)

### Bug Fixes

- **attribution:** make AI-agent attribution fire on Windows + refine display ([#104](https://github.com/KirilMT/collab/issues/104)) ([2e0f8d0](https://github.com/KirilMT/collab/commit/2e0f8d0595c6fba990bfd7ba4eada4b337b224a3))
- **core:** preserve locks when git status fails during watch/shutdown ([#84](https://github.com/KirilMT/collab/issues/84)) ([c23dff5](https://github.com/KirilMT/collab/commit/c23dff5d477a1e845005ab8dc99fc5c0373ee597))
- **lock_client:** terminate orphan launcher when daemon_start verification fails ([#96](https://github.com/KirilMT/collab/issues/96)) ([4c41173](https://github.com/KirilMT/collab/commit/4c41173d4a454065236251b45252370d0750838c)), closes [#73](https://github.com/KirilMT/collab/issues/73)
- **logging:** surface silent git/API failures as warning/debug logs ([#100](https://github.com/KirilMT/collab/issues/100)) ([4ec4ed3](https://github.com/KirilMT/collab/commit/4ec4ed3ccc6a10b32d51e25240c5df2c00e0da5a))
- **setup-dev:** run full npm install instead of only prettier ([#82](https://github.com/KirilMT/collab/issues/82)) ([f058e0d](https://github.com/KirilMT/collab/commit/f058e0dea6a525965b37902918ba41e81eb6dd00))

### Documentation

- **project:** consolidate workflow on built-in Status field with auto-transitions ([#98](https://github.com/KirilMT/collab/issues/98)) ([bc3572f](https://github.com/KirilMT/collab/commit/bc3572f9ce18390a94b6eb8ac567cb85386661ef))

## [0.5.1](https://github.com/KirilMT/collab/compare/v0.5.0...v0.5.1) (2026-06-03)

### Bug Fixes

- **ide:** ship init-hooks and IDE-safe git hooks for VS Code/Cursor ([#76](https://github.com/KirilMT/collab/issues/76)) ([#77](https://github.com/KirilMT/collab/issues/77)) ([9f25996](https://github.com/KirilMT/collab/commit/9f25996a7c992e70f5a08c654e8a2aae230ef023))

## [0.5.0](https://github.com/KirilMT/collab/compare/v0.4.2...v0.5.0) (2026-06-03)

### Features

- **attribution:** strict human vs AI agent locks and dashboard UX ([#68](https://github.com/KirilMT/collab/issues/68)) ([11bbf18](https://github.com/KirilMT/collab/commit/11bbf18781d942e489ac931f7f89db16e3eb1b98))

## [0.4.2](https://github.com/KirilMT/collab/compare/v0.4.1...v0.4.2) (2026-06-02)

### Bug Fixes

- **dashboard:** ship dashboard static assets in the wheel ([#62](https://github.com/KirilMT/collab/issues/62)) ([79e4073](https://github.com/KirilMT/collab/commit/79e407389f1a1cda8fb9ad13c9abc033820fbed0))
- **locks:** preserve leading status column in git porcelain parsing ([#64](https://github.com/KirilMT/collab/issues/64)) ([91bf878](https://github.com/KirilMT/collab/commit/91bf8782b580915914014cd77c9ccf2ead7f632d))
- **watcher:** launch watcher via interpreter and reap orphaned collab.exe wrappers ([#66](https://github.com/KirilMT/collab/issues/66)) ([1bf2d67](https://github.com/KirilMT/collab/commit/1bf2d67670a33689e41ee04b81c9eea1b05ce1fc))

## [0.4.1](https://github.com/KirilMT/collab/compare/v0.4.0...v0.4.1) (2026-06-01)

### Bug Fixes

- **dashboard:** show repository name instead of Supabase project ref ([#60](https://github.com/KirilMT/collab/issues/60)) ([9325863](https://github.com/KirilMT/collab/commit/93258632d680765e40c5f983e0ea1316f41fe979))

## [0.4.0](https://github.com/KirilMT/collab/compare/v0.3.1...v0.4.0) (2026-06-01)

### Features

- **agent:** multi-agent file locking with per-agent ownership ([#58](https://github.com/KirilMT/collab/issues/58)) ([90fb9e6](https://github.com/KirilMT/collab/commit/90fb9e6deaebd46be6428452a5495bcb02a31b44))

## [0.3.1](https://github.com/KirilMT/collab/compare/v0.3.0...v0.3.1) (2026-05-31)

### Bug Fixes

- **dashboard:** reload Supabase credentials on each sync (release 0.3.1) ([#56](https://github.com/KirilMT/collab/issues/56)) ([21e262d](https://github.com/KirilMT/collab/commit/21e262d115ad2d857e1565a174cf44fb41d2c531))

## [0.3.0](https://github.com/KirilMT/collab/compare/v0.2.9...v0.3.0) (2026-05-31)

### Features

- **dashboard:** frontend testing stack, static asset fix, and CI-parity validation ([#54](https://github.com/KirilMT/collab/issues/54)) ([5c660f5](https://github.com/KirilMT/collab/commit/5c660f53b865af9d84693e8013c67bfdd5b0f8f6))
- **phase5:** subprocess hardening, platform probes, and lifecycle errors ([#49](https://github.com/KirilMT/collab/issues/49)) ([3bfdfd5](https://github.com/KirilMT/collab/commit/3bfdfd5a069cd8ad5e263d6a04c623d5f683a711))
- **phase6:** flat collab/ package at repo root (Option A) ([#51](https://github.com/KirilMT/collab/issues/51)) ([4d7d0f6](https://github.com/KirilMT/collab/commit/4d7d0f6cb19eff2872f6622a011ca8459fb2aa66))

## [0.2.9](https://github.com/KirilMT/collab/compare/v0.2.8...v0.2.9) (2026-05-14)

### Bug Fixes

- **workflows:** repair for publish.yml and release.yml ([#43](https://github.com/KirilMT/collab/issues/43)) ([06157e6](https://github.com/KirilMT/collab/commit/06157e60e56d0b0816d63514a40fbcd538567611))

## [0.2.8](https://github.com/KirilMT/collab/compare/v0.2.7...v0.2.8) (2026-05-14)

### Bug Fixes

- **cli:** repair for release and publish ([#41](https://github.com/KirilMT/collab/issues/41)) ([e1ef15c](https://github.com/KirilMT/collab/commit/e1ef15cb0cee7202d453ddbb764c7d6b749dd5af))

## [0.2.7](https://github.com/KirilMT/collab/compare/v0.2.6...v0.2.7) (2026-05-14)

### Bug Fixes

- **ci:** resolve GitHub Actions failures ([#39](https://github.com/KirilMT/collab/issues/39)) ([cc29cca](https://github.com/KirilMT/collab/commit/cc29cca23d0fa2b5f30e60f5120305d7a8e7054d))

## [0.2.6](https://github.com/KirilMT/collab/compare/v0.2.5...v0.2.6) (2026-05-14)

### Bug Fixes

- **release:** json parsing error ([#37](https://github.com/KirilMT/collab/issues/37)) ([2a2de27](https://github.com/KirilMT/collab/commit/2a2de277d6adb2b515c6582c0a8da2dda2a21e96))

## [0.2.5](https://github.com/KirilMT/collab/compare/v0.2.4...v0.2.5) (2026-05-14)

### Bug Fixes

- **ci:** improve workflow robustness and add repository_dispatch trigger ([#30](https://github.com/KirilMT/collab/issues/30)) ([9a5b47b](https://github.com/KirilMT/collab/commit/9a5b47b44c216d2386068345324b59392bf04d4b))

## [0.2.4](https://github.com/KirilMT/collab/compare/v0.2.3...v0.2.4) (2026-05-13)

### Bug Fixes

- **ci:** remove skip condition blocking release-please tag creation on merge ([#26](https://github.com/KirilMT/collab/issues/26)) ([3e11b0a](https://github.com/KirilMT/collab/commit/3e11b0abc5263cfd6af940ac965c33530319c68f))

## [0.2.3](https://github.com/KirilMT/collab/compare/v0.2.2...v0.2.3) (2026-05-13)

### Bug Fixes

- **ci:** create GitHub Release before uploading artifacts; fix GPG signing ([#20](https://github.com/KirilMT/collab/issues/20)) ([659e64a](https://github.com/KirilMT/collab/commit/659e64afb78ff7e2bd6653a1cae7718559588c91))

## [0.2.2](https://github.com/KirilMT/collab/compare/v0.2.1...v0.2.2) (2026-05-08)

### Bug Fixes

- **ci:** restore dependabot auto-format stability with Ruff E9 gate ([#17](https://github.com/KirilMT/collab/issues/17)) ([3cb43e0](https://github.com/KirilMT/collab/commit/3cb43e080049ef43a1fa7ece3a723df03e097425))

## [0.2.1](https://github.com/KirilMT/collab/compare/v0.2.0...v0.2.1) (2026-05-08)

### Bug Fixes

- **ci:** enforce LF line endings for all repository text files ([#8](https://github.com/KirilMT/collab/issues/8)) ([46200d4](https://github.com/KirilMT/collab/commit/46200d42762d33a42da5fc1249fd6a5cc304874d))

## [0.2.0](https://github.com/KirilMT/collab/compare/v0.1.0...v0.2.0) (2026-05-07)

### Features

- **hooks:** align collab hook lifecycle and CI parity with consumer application patterns ([27c5ce3](https://github.com/KirilMT/collab/commit/27c5ce39d1f6849fadbe5458d3437dc65d8c3f68))
- **infra:** Phase 0.5 infrastructure scaffolding and frontend parity ([fca3d51](https://github.com/KirilMT/collab/commit/fca3d51f8f99dc5bcc26cee0162d691c4e43ddfd))
- **phase1:** complete migration Phase 1 and add shell-compatibility skill ([27ee505](https://github.com/KirilMT/collab/commit/27ee50569ee69b83b96d77d636d4dbb79cf14cd1))
- **phase2:** update extension to call installed collab package ([ea7dbe6](https://github.com/KirilMT/collab/commit/ea7dbe671773503121834bf62dfe3ad2cbfaf929))
- **phase3:** update setup scripts to provision collab package and extension ([97f07e9](https://github.com/KirilMT/collab/commit/97f07e96919dc12ef0f75385a6f35c10a508b6a6))
- **vscode-extension:** add collaborative locks VS Code extension with watcher lifecycle management ([4de01d1](https://github.com/KirilMT/collab/commit/4de01d1437814d28b56af4c5fcae81867ea26c9e))

### Bug Fixes

- **build:** remove redundant License classifier conflicting with PEP 639 ([974bbc0](https://github.com/KirilMT/collab/commit/974bbc07ec2e106405ac75a6769066a0b0180ac8))
- **ci:** harden workflow triggers and manual dispatch support ([44b0886](https://github.com/KirilMT/collab/commit/44b0886ff1f6c67a473f1b80cc3d141a421eb1c5))
- **ci:** preserve coverage artifacts for report step ([2b371b9](https://github.com/KirilMT/collab/commit/2b371b9c3058c1f02fd2098a6bd521cea324afd6))
- **hooks:** enable verbose output for collab lock hooks ([146c217](https://github.com/KirilMT/collab/commit/146c217169800f34fbf75320e20b42da5fabc043))
- **hooks:** show only collab messages in hooks ([90c0da4](https://github.com/KirilMT/collab/commit/90c0da4a4fe5054ab580772094447f8ddbace15f))
- **hooks:** stop forwarding git args to pre-push pre-commit run ([3fa8456](https://github.com/KirilMT/collab/commit/3fa84569512af10e7c76f3764f8f76fb01bac88e))
- **test:** close subprocess stdin in hook template tests to prevent CI hang ([e14356a](https://github.com/KirilMT/collab/commit/e14356ab1d97ffe8060a6e3733bc126ecc7a42b5))
- **test:** resolve infinite loop in hook template pre-push test ([1518010](https://github.com/KirilMT/collab/commit/15180100302e5a7367e56accde4b4dd6c1e34e70))
- **validation:** clarify skipped checks and harden CI/frontend detection ([2dee206](https://github.com/KirilMT/collab/commit/2dee206cec13fdc245ef26595529d4794bd14122))
- **validation:** make checks deterministic and tighten cleanup coverage ([dea2028](https://github.com/KirilMT/collab/commit/dea202810aaeb6a09dc06617f3acd5a3dafaf40a))

## [0.1.0] (2026-05-04)

### ✨ Features

- **infrastructure:** Phase 0.5 scaffolding — complete development environment with CI/CD,
  testing, linting, and AI governance infrastructure
- **validation:** Enhanced validate_code.py with 10 robust validation steps including
  docformatter, flake8, yamllint, and diff-cover
- **testing:** All 635 unit tests passing with ≥85% coverage, zero skipped tests
- **lock-client:** Atomic file locking with Supabase Realtime conflict detection
- **live-locks-watcher:** Real-time collaborative workflow synchronization
- **dashboard:** Interactive file lock status visualization

### 🔧 Infrastructure

- Full Python package structure with setuptools entry points
- Comprehensive tool configuration (pytest, ruff, black, mypy, coverage, bandit)
- Git workflows for CI/CD automation and release management
- Pre-commit hooks for code quality enforcement
- Development scripts: setup, format, validate, cleanup, test generation

### 📝 Documentation

- Complete repository structure matching industry patterns
- AI agent governance for collaborative development (AGENTS.md, CLAUDE.md)
- Skill-based workflow documentation for file-locking, testing, commits, bugs
- Architecture and API reference documentation

---

## [0.1.1] (2026-05-06)

### ✨ Features

- **frontend:** Add `eslint.config.js` with production-aligned flat-config rules (recommended
  - no-unused-vars warn, no-console warn, no-undef error; targets
    `collab/dashboard/**/*.js` and `tests/frontend/playwright/**/*.js`)
- **frontend:** Add `playwright.config.js` with full E2E test
  configuration — globalSetup/globalTeardown hooks, webServer auto-start,
  `collab/dashboard` static serving, Chromium + Firefox projects, visual regression
  settings, .env feature-flag support
- **frontend:** Add four Playwright E2E helper modules under
  `tests/frontend/playwright/` (e2e-test-setup.js, e2e-test-teardown.js,
  pre-test-cleanup.js, test-utils.js) — full pattern port adapted for
  collab dashboard and Supabase environment

### 🔧 Infrastructure

- **npm:** Add `@eslint/js`, `eslint`, `globals`, `@playwright/test` to
  `package.json` devDependencies (versions aligned with reference tooling stack)
- **npm:** Add `lint:frontend`, `lint:frontend:fix`, `test:frontend:e2e`,
  `test:frontend:e2e:chromium` scripts
- **ci:** Branch triggers updated from `master` to `main` across all GitHub
  workflow files (`ci.yml`, `release.yml`, `lock-service-smoke-test.yml`)
- **hooks:** Pre-push hook hardened — lock release now atomic with validation
  (`validate_and_release` in `scripts/collab_git_hook.py`)
- **validate:** Default diff-cover compare-branch updated to `origin/main`/`main`
  (removed `master` and `origin/master` candidates)
- **src:** Source tree reorganised from `src/collab/` flat layout to `src/`
  (renames tracked by git)
- **tests:** Test directories reorganised from `tests/unit/` to
  `tests/backend/unit/`; new `tests/frontend/` scaffold with Jest and Playwright
  subdirectories

### 🐛 Bug Fixes

- **migration:** Remove duplicate Phase 0.5 status line and fix broken repository
  tree markdown block in `MIGRATION_PLAN.md`
- **validate:** Fix ESLint fallback target from `src tests/frontend` (fails on
  directories with no `.js` files) to `tests/frontend/playwright`

### 📝 Documentation

- **license:** Add MIT `LICENSE` file (Copyright 2026 KirilMT)
- **readme:** Align README with current command surface, daemon lifecycle,
  VS Code notification channels, and `watch` command
- **gitignore:** Extend ignore rules to cover `.env.*` variants while preserving
  `.env.example`
- **roadmap:** Add Optional Enhancements Backlog section to
  `docs/collab_roadmap.md` (since migrated to [GitHub Projects](https://github.com/KirilMT/collab/projects))
- **migration:** Update `MIGRATION_PLAN.md` Phase 0.5 status, fix tree block
  formatting, promote `eslint.config.js` and `playwright.config.js` from
  "could add" to completed, remove deferred lower-priority block

---

## [Unreleased]

### Planned for Future Releases

**Phase 1:** Extract into installable Python wheel with golden command suite

**Phase 2:** Extension integration with installed runtime package

**Phase 3:** Setup automation for integrated environment provisioning

**Phase 4:** Decouple application repos from collab source code

**Phase 5:** Security hardening and error taxonomy refinement
