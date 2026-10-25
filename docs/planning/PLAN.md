# androsync: Software Plan

Date: 2026-10-15 · Owner: Abdoh-Abqr

This is the process and tracking plan. The technical design and the task specs live in the repo root:
`PLAN.md` (architecture, algorithm, API) and `NEXT_TASKS.md` (one write-up per task).

## 1. Goals and constraints

- Goal: a safe, open-source tool that keeps chosen folders identical on an Ubuntu laptop and an Android phone, started from the phone, the desktop window or the command line.
- Team: 1 person, plus AI agent sessions that implement one task at a time from `NEXT_TASKS.md`.
- Deadline: none.
- Requirements: evolving. New needs have appeared every few days (undo, conflicts folder, setup without debugging).
- System type: mobile (Android app) plus a desktop service (Python daemon, CLI, Tk window) talking over HTTP and ADB.
- Assumptions:
  - GitHub Issues and Projects instead of Jira. The repo is `Abdoh-Abqr/androsync`, the project is meant to go public, and the user was unsure about the difference.
  - One real test phone and one laptop for device checks.
  - No release date. A phase is released when its verification list passes.

## 2. Development approach

**Chosen: Kanban**

Why: one person, no deadline and changing requirements fit a continuous board with a work-in-progress limit. Agent sessions work best on small, independent slices, and the existing task write-ups already are such slices.

UML: used for sequence and state diagrams only, where behaviour is hard to explain in words (stop flow, job lifecycle). Not used for class diagrams; `CODE_INDEX.md` and the dataclasses cover structure. UML is a notation here, not a process.

Cadence:
- WIP limit: 2 in progress (at most one laptop task and one app task), 2 in review.
- Weekly 15-minute board review: reorder Ready, close or split stale items.
- Release when the verification list of a phase passes on the real phone. Tag `vX.Y.Z`, CI builds the release (task 35c).

## 3. Tracker: GitHub Issues + Projects

Why not Jira here: Jira adds sprints, workflow rules and reporting that a team needs. For one person on a public repo it is overhead, it sits outside the code, and a contributor would need a Jira account. GitHub Issues live next to the code, link to commits and pull requests automatically (`Fixes #12`), and are free and public. If a team or employer ever needs Jira, the same epics and stories carry over one to one.

| Jira term | Used here as |
|---|---|
| Epic | Issue with label `epic`, with its stories attached as sub-issues |
| Story | Issue with label `story`, title is a verb phrase |
| Task / Sub-task | Issue with label `task`, or a sub-issue |
| Bug | Issue with label `bug` and steps to reproduce |
| Component | Labels `comp:app`, `comp:daemon`, `comp:engine`, `comp:gui`, `comp:cli`, `comp:docs` |
| Board | GitHub Projects (v2) board "androsync" |

- Columns: Backlog, Ready, In Progress (WIP 2), In Review (WIP 2), Done.
- Other labels: `needs-device` (cannot be verified without the phone), `safety`, `tech-debt`, `release`.
- Story format: title `<verb> <thing>`, body "As a user, I want <goal>, so that <benefit>", acceptance criteria as "Given, when, then" lines, link to the task section in `NEXT_TASKS.md`.
- Branch: `<issue>-short-name`, for example `36-cancel-kills-adb`.
- Commit or PR title: `Cancel kills the running adb call (#36)`. The PR body ends with `Fixes #36`.

Definition of Ready:
1. Acceptance criteria written.
2. Sized S, M or L (S under 2 hours, M half a day, L a day or more; split anything larger).
3. No open blocker.
4. Task write-up exists in `NEXT_TASKS.md`, with a diagram linked from section 4 if the design is not obvious.

Definition of Done:
1. Merged via pull request with green CI.
2. `pytest laptop -q` and `./gradlew :app:testDebugUnitTest` pass; `scripts/check_no_personal_data.sh` is clean.
3. Anything touching ADB or the app screens checked on the real phone and noted in `NEXT_TASKS.md`.
4. README, `CODE_INDEX.md` and the diagrams below updated if behaviour changed.

### Starter epics

| Epic | Goal | First stories |
|---|---|---|
| E1 Safety and control (Phase O) | A sync can always be seen and stopped, and cannot destroy files by accident | Stop kills the running transfer (36a). Show the running sync and its log on every device (36b). Reject invalid phone folders (36c). Confirm mass deletes and reset history on folder edits (36d). |
| E2 Easy setup (Phase P) | A new user connects a phone in minutes, with or without debugging | Guided "Connect your phone" checklist (37f). Syncthing guide (37-doc). `play` and `full` build flavors (37g). |
| E3 Without debugging (Phase P, optional) | Preview, undo and conflicts work with no ADB | Spike All files access (37a). Agent protocol and transport (37b). Android agent (37c). |
| E4 Several phones and PCs (Phase L) | One laptop serves several phones, one app serves several laptops | Per-device state (33a). Device registry (33b). Laptop profiles in the app (33c). |
| E5 Public release (Phase N) | Installable by strangers from GitHub and F-Droid | Private to public history (35b). CI and releases (35c). F-Droid readiness (35d). Donation links (35e). |

Issue map (created 2026-10-16 in the private repo; each story names its `NEXT_TASKS.md` task in the body):

| Epic | Stories (task number, issue) |
|---|---|
| #1 Safety and control | 36a #6, 36b #7, 36c #8, 36d #9, window job-state bug #27 |
| #2 Easy setup | 37f #10, 37-doc #11, 37g #12 |
| #3 Without debugging | 37a #13, 37b #14, 37c #15, 37d #16, 37e #17 |
| #4 Several phones and PCs | 33a #18, 33b #19, 33c #20, 33d #21 |
| #5 Public release | 35b #22, 35c #23, 35d #24, 35e #25, 35f #26 |

Not opened because already done: 33e, 35a, and the scrub part of 35b. Open the project board once the token has the `project` scope (section 5).

## 4. Diagrams

The diagrams live in [`docs/architecture/`](../architecture/README.md), one place only, so they cannot drift from
this file. Created 2026-10-16 from a read of the code; 27 diagrams, all Mermaid, all parse-checked.

| Diagram | Status | Where |
|---|---|---|
| C4 L1 Context | Required | [README.md section 1](../architecture/README.md) |
| C4 L2 Container | Required | [README.md sections 2 and 3](../architecture/README.md), as built and target |
| Deployment | Required (promoted) | [README.md section 4](../architecture/README.md) |
| C4 L3 Component | Required (promoted: the engine, daemon and app are each large enough) | [components.md sections 1 to 3](../architecture/components.md) |
| UML class | Required (promoted: core model and transport hierarchy) | [components.md section 4](../architecture/components.md) |
| Module dependencies | Extra | [components.md section 5](../architecture/components.md) |
| Data Flow Diagram L0 and L1 | Required | [data.md sections 1 and 2](../architecture/data.md) |
| ER diagram | Required (promoted: state formats change with #18) | [data.md section 3](../architecture/data.md) |
| Flowchart | Required | [behavior.md sections 1 and 2](../architecture/behavior.md) |
| UML sequence | Required | [behavior.md sections 3 to 8](../architecture/behavior.md), eight diagrams |
| UML state | Required | [behavior.md section 9](../architecture/behavior.md), five diagrams |
| UML use case | Required (promoted: full architecture was requested) | [overview.md section 1](../architecture/overview.md) |

Keeping them true: a change that alters behaviour updates its diagram in the same pull request (part of the
Definition of Done). `scripts/check_mermaid.mjs` parses every block.

## 5. Board and next action

Board: https://github.com/users/Abdoh-Abqr/projects/1 (private, linked to the repo). Created 2026-10-16.
- Status columns: Backlog, Ready, In Progress (limit 2), In Review (limit 2), Done. Closing an issue moves it to Done.
- Fields: Size (S, M, L) and Order (1 is first). Epics sit at the end with Order 90 to 94.
- Milestones: M1 Safe and stoppable (#1, #6 to #9, #27), M2 Easy setup (#2, #10, #11, #12), M3 First public release (#5, #22 to #25).

Ready, in this order:

| Order | Issue | Size |
|---|---|---|
| 1 | #8 Reject invalid phone folders | M |
| 2 | #6 Stop a running sync within seconds | M |
| 3 | #27 Fix the window waiting forever on a failed job | S |
| 4 | #11 Syncthing guide | S |
| 5 | #9 Protect files when a pair or a plan changes a lot | L |
| 6 | #10 Guide users to the USB debugging switch | L |
| 7 | #7 Show the running sync and its log on every device | L |
| 8 | #12 Build a play and a full version | M |
| 9 | #23 Build and publish releases from CI | M |

Lanes: A (laptop) takes #8, #6, #27, #9, #7, #23. B (app and docs) takes #11, #10, #12. Never more than two in progress.

One click the API cannot do: in the board, add a view with the Board layout (columns follow Status) and keep the
default Table view sorted by Order.

Next action: open the board, press "New view", choose Board, then move #8 to In Progress and give it to the agent session.
