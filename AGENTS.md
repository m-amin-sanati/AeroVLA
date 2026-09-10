# AGENTS.md — Hard Documentation Rules for AI Agents

> This file is a **hard rule** for any AI agent (opencode, Claude, other chat bots)
> working in this repository. Violating it is a failure. Read it before doing work.

---

## 1. Purpose of this file

The human maintains a fleet of agents across multiple machines (local dev box + a
remote **H100** VM at `ssh main.copper.sanati-emp.coder`). Agents frequently hand work
off to other agents, sessions are lost, and context is expensive. **The only durable
knowledge is the documentation we write down.** Therefore: if you did it, you must
document it.

---

## 2. The hard rule (applies to EVERY session)

> **Every action you take that changes state, every problem you hit, every solution
> you apply, every runbook you produce — MUST be written into the docs before the
> session ends.**

Concretely, for each thing you do, record **all four** of these, in the doc(s) below:

| What | Meaning |
|------|---------|
| **What you did** | The action, command, file, or change. Include exact paths and commands. |
| **Problem** | The blocker you faced (error message, symptom, constraint). |
| **Solution** | Exactly how you fixed it (what you tried, what worked, why). |
| **Runbook** | The repeatable step-by-step procedure to do the task again (commands + expected output), so another agent or the human can execute it without you. |

---

## 3. Where documentation goes

Maintain a **running journal** and keep the runbooks/status docs current.

### 3.1 `docs/DOC_JOURNAL.md` (NEW per session — append)

- If it doesn't exist, **create** it with a heading for the current session
  (date + short goal).
- Append, in reverse-date order or a simple chronological log, each session's:
  - **Goal**
  - **What you did** (with commands/paths)
  - **Problems faced** (verbatim errors/symptoms)
  - **Solutions applied**
  - **Current state** (what's done, what's pending)
  - **Next steps**
- One entry block per topic/work-item. Be terse but complete.

### 3.2 Runbooks & status docs (keep updated)

- `docs/AEROVLA_EVAL_RUNBOOK.md` — full eval ops (transfer → server → eval → metrics → pull results).
- `docs/SPLIT_RUNBOOK.md` — split execution (UE4 server on local GPU + model on H100), pre-created by an earlier agent.
- `docs/PROJECT_HANDOFF.md` — project context (what the project is, all changes, formats, env, disk state, blockers).
- Create new `docs/<NAME>_RUNBOOK.md` for any new workflow you produce.
- **When you change a workflow, UPDATE the corresponding runbook in the same session.**

### 3.3 Formatting rules for docs

- Markdown only. Use tables for change-logs (item / what / problem / solution).
- Include **exact shell commands** (copy-pasteable) and **expected output** markers.
- Record **file paths with line numbers** for code changes (`src/foo.py:42`).
- Note **hardware/env constraints** (e.g. "6 GB VRAM cannot run model", "H100 MIG has no Vulkan → CPU render").

---

## 4. Mandatory milestones for documentation

Do these **without being asked**:

1. **Start of work**: create/append the `docs/DOC_JOURNAL.md` session entry stating your goal.
2. **After any state change** (new file, edit, install, config change): log "what + problem + solution" in the journal.
3. **On a surfaced problem**: even if unresolved, log the problem + attempted solutions + what remains.
4. **End of session / handoff**: update `docs/PROJECT_HANDOFF.md` (§1 "what changed" tables, §7 disk-state, §9 next steps), update the relevant runbook, and add a final journal summary.
5. **Never rely on memory**: if you cannot open the file, write the doc block into your final message so the human can paste it.

---

## 5. Convention: SPLIT/deprecated-path markers

When something becomes deprecated or is replaced:
- Keep old runbooks but prepend `> **SUPERSEDED** <date> — see <new-doc> for the current approach.`
- Do not delete documentation of a failed attempt; it is valuable. Mark it clearly instead.

---

## 6. Current key context (verified facts — keep this section updated)

- **Project**: AeroVLA (ECCV 2026) — VLA model for UAV navigation, TravelUAV/AirSim.
- **User goal**: closed-loop eval of a custom LoRA (`checkpoints/`) on the custom
  `BrushifyCountryRoads` env; results viewed locally.
- **Local box**: RTX 3050, 6 GB VRAM → **cannot run the model** (even 4-bit OOM, verified).
  Can run the **UE4/AirSim server** for rendering.
- **H100 VM** (`ssh main.copper.sanati-emp.coder`, project at `/workspaces/AeroVLA`):
  - MIG 7g.80gb, **no working Vulkan** → UE4 renders on CPU (llvmpipe), ~20 h for 123 eps.
  - venv `.venv` (`--system-site-packages`) has full stack (transformers 4.42.4, peft,
    accelerate 0.32.1, airsim client patched, bf16 wrapper).
- **Split approach** (current plan): local UE4 server + H100 model client, connected by
  SSH **reverse tunnels** (H100 cannot reach local directly, but local can reach H100).
  - Local `airsim_plugin/AirVLNSimulatorServerTool.py:696` → `HOST='0.0.0.0'`.
  - `scripts/split.sh` opens server + `ssh -R` tunnels; H100 `param.py` stays `127.0.0.1`.
- **Env archive layout** (learned the hard way): `envs/BrushifyCountryRoads.zip` is a
  **standalone complete UE4 env** (launcher + binary + 2.47 GB `.pak` + Engine). Do NOT
  let 7z auto-merge the `.z01`/`.z02` (they are separate raw-data parts, `.z01` corrupt,
  `.z02` empty). Extract with the `.z0x` files moved aside.
- **Env layout schema (since 2026-09-10)**: per-env folder `envs/<Map>/` with
  `data_raws/` (episode archives) and `engine/<Map>/` (extracted UE4, launcher
  `engine/<Map>/<Map>.sh`). Server resolves via `resolve_env_launcher`
  (`AirVLNSimulatorServerTool.py`), uses `engine_dir='engine'` in `env_exec_path_dict`.
- **Local env status**: **BrushifyCountryRoads engine EXTRACTED + verified** at
  `envs/BrushifyCountryRoads/engine/BrushifyCountryRoads/` (launcher `.sh` + 165M
  binary + 2.47G pak, chmod +x applied); server resolves it (exists+exec True).
  BrushifyForestPack extracted but nested at
  `envs/BrushifyForestPack/envs/BrushifyForestPack/` (pending move to `engine/`;
  not registered in `env_exec_path_dict`, not needed for brushify eval).
- **Split eval is launch-ready**: local `bash scripts/split.sh [PORT] [--windowed]`
  sanity check passes; then H100 `bash scripts/run_eval.sh`. **Data prep flow
  (2026-09-10)**: if `merged_data.json` is missing, run on H100
  `bash scripts/prepare_env_data.sh <Map>` (TravelUAV generator → then symlinks
  `dataset_raw/<Map>` → `envs/data_raws/<Map>`). `aerovla_wrapper_ui.py` now has
  optional `tkinter`/`ImageTk` imports (headless-safe; commit `f0be9b2`).
- **One-command split (since 2026-09-10)**: `split.sh` auto-launches the H100 eval
  via `ssh H100 bash scripts/run_eval.sh <PORT> /tmp/split_eval.log` (independent,
  tracked, pidfile `/tmp/aerovla_eval_<PORT>.pid`); **`kill $(cat
  /tmp/aerovla_split_<PORT>.pid)` tears down everything** (server + tunnel + viewer +
  UE4 orphans via `fuser -k <PORT+1..+16>` + remote eval). Viewer fixed to a single
  persistent tkinter window (commit `7a45cdb`). **Split eval RUNNING (2026-09-10,
  windowed, healthy, 0 image timeouts)** on BrushifyCountryRoads: H100 client
  (pidfile `/tmp/aerovla_eval_30000.pid`) + local UE4 server via reverse tunnel,
  resume from prior results, `Completed: 34 / 82` at 13:33z.
- **Git sync (since 2026-09-10)**: both repos now track the personal fork
  `git@github.com:m-amin-sanati/AeroVLA.git` (added as `fork` remote; `origin` stays
  upstream `XuPeng23/AeroVLA`). Fork `main` is the single source of truth; local + H100
  `main` are synced to it. Server tool default `HOST=127.0.0.1` (`--host 0.0.0.0` for
  split/tunnel); `--windowed` optional; `aerovla_wrapper_ui.py` is pristine upstream.
  To sync H100 from local commits: `git push fork main` locally, then on H100
  `git fetch fork && git reset --hard fork/main`.

---

## 7. Checklist before finishing ANY session

- [ ] `docs/DOC_JOURNAL.md` session entry appended (goal / did / problems / solutions / state / next).
- [ ] `docs/PROJECT_HANDOFF.md` current (changes, disk state, blockers, next steps).
- [ ] Relevant runbooks updated to match reality.
- [ ] AGENTS.md §6 "current key context" refreshed if needed.
- [ ] No secrets/keys committed (never log passwords, tokens, model paths with credentials).
