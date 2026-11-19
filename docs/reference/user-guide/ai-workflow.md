# Optional AI-Assisted Workflow

VirtBench offers an optional AI-assisted workflow for adaptive validation runs.
A VirtBench skill for Codex or Claude Code is the natural-language front door,
and the `adaptive` planner in this repository is the deterministic engine that
chooses VM totals, executes scenarios, and writes an auditable
`virtbench_ai_rounds.json` artifact.

The AI-assisted workflow is optional. Every scenario it runs is a regular
`virtbench` command that you can also run directly.

## Set Up the Skill

The repository ships only the `adaptive` planner. The VirtBench skill file and
its installer are not part of the repository; create them from the contents in
[Skill Files](#skill-files) at the end of this page.

1. Create a folder for the two files, for example `ai/` in your
   kubevirt-benchmark checkout or any directory outside it.
2. Save the [skill file](#skillsmd) as `skills.md` in that folder.
3. Save the [installer](#install-skillsh) as `install-skill.sh` in the same
   folder and make it executable:

        chmod +x install-skill.sh

4. Install the skill for Codex, Claude Code, or both:

        ./install-skill.sh                  # Codex and Claude Code
        ./install-skill.sh --target claude  # Claude Code only
        ./install-skill.sh --target codex   # Codex only
        ./install-skill.sh --dry-run        # show the install paths without copying

    The installer copies `skills.md` to `~/.codex/skills/virtbench/SKILL.md`
    and `~/.claude/skills/virtbench/SKILL.md`. To install without the script,
    copy `skills.md` to those paths yourself.

5. Restart Codex or Claude Code so the assistant loads the skill.

The skill runs the planner from your kubevirt-benchmark checkout. Either start
the assistant from the repository root, or point the skill at the checkout:

```bash
export VIRTBENCH_REPO=/path/to/kubevirt-benchmark
```

The checkout needs the repo-local virtual environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

If you save the files inside your checkout, keep them out of commits, for
example by adding the folder to `.git/info/exclude`.

## Basic Usage

Use the skill from an LLM session. In Codex, the `@virtbench` form is
convenient.

Primary VM lifecycle workflows:

```text
@virtbench vm-creation 4 rounds
@virtbench vm-creation 4 rounds max VMs 120
@virtbench bootstorm 4 rounds
@virtbench bootstorm 4 rounds using existing VMs ai-bootstorm-1 through ai-bootstorm-80
@virtbench migration 4 rounds
@virtbench migration 4 rounds using existing VMs ai-bootstorm-1 through ai-bootstorm-80
@virtbench vm-workflow-basic 4 rounds
@virtbench vm-workflow-basic 4 rounds max VMs 120
@virtbench vm-workflow-basic 4 rounds max VMs 40 for 1-disk and 4-disk VMs
@virtbench vm-creation, bootstorm and migration 4 rounds
@virtbench vm-create, bootstorm, rebalance and migrate 4 rounds max VMs 120
```

Storage and disk workflows:

```text
@virtbench fio max-iops 6 rounds
@virtbench fio max-bandwidth 6 rounds
@virtbench elbencho 10 vms
@virtbench disk-ops 4 rounds
```

Resiliency and utility workflows:

```text
@virtbench chaos 3 rounds
@virtbench failure-recovery
@virtbench validate-cluster
```

In Claude Code, use the skill command or natural language:

```text
/virtbench bootstorm 4 rounds
Use the virtbench skill to run bootstorm 4 rounds
Use the virtbench skill to run vm-creation, bootstorm and migration 4 rounds
```

> **Note:** `vm-workflow-basic` is an alias for the combined
> "vm-create, bootstorm, rebalance and migrate" workflow. Both prompt forms
> run the same planner scenario.

The combined VM workflow prompt maps to `vm-workflow-basic`. A prompt such as
`@virtbench vm-workflow-basic 4 rounds max VMs 40 for 1-disk and 4-disk VMs`
runs the same adaptive workflow for both disk profiles, saves both runs under
one results folder, and generates one dashboard with separate `1-disk` and
`4-disk` tabs.

## Using Existing VMs

For scenarios that support reuse, tell the skill the existing namespace prefix
and range:

```text
@virtbench bootstorm 4 rounds using existing VMs ai-bootstorm-1 through ai-bootstorm-80
@virtbench migration 4 rounds using existing VMs ai-bootstorm-1 through ai-bootstorm-80
```

Claude Code examples:

```text
/virtbench migration 4 rounds using existing VMs ai-bootstorm-1 through ai-bootstorm-80
Use the virtbench skill to run migration 4 rounds using existing VMs ai-bootstorm-1 through ai-bootstorm-80
```

The planner interprets this as:

```bash
--namespace-prefix ai-bootstorm
--max-vms 80
```

`bootstorm` and `migration` can reuse healthy existing VMIs. `vm-creation`
does not reuse VMs; each round is a standalone VM creation total.

## Limiting Maximum VMs

Use `max VMs` when you want the planner to stay within a hard cap:

```text
@virtbench vm-creation 4 rounds max VMs 120
@virtbench bootstorm 4 rounds max VMs 120
@virtbench vm-workflow-basic 4 rounds max VMs 120
```

Claude Code example:

```text
/virtbench vm-workflow-basic 4 rounds max VMs 120
```

The planner interprets these as:

```bash
--max-vms 120
```

It still chooses the per-round totals intelligently within that cap. `max VMs`
is a user ceiling, not a fixed per-round size. The planner takes the lower of
the cluster-estimated ceiling and the user cap, then spreads totals across the
requested rounds. For `vm-workflow-basic`, `max VMs 80` with 4 rounds can plan
20, 40, 60, and 80 VMs when the cluster estimate is at least 80. Each round
still creates a fresh full VM range for that round's selected total.
If the best stable result reaches the user cap, the AI summary and dashboard
call that out as a user-capped result instead of presenting it as the discovered
cluster maximum.
Workflow round tables show headline durations: total workflow duration, VM
creation duration, bootstorm duration, and migration duration. Lower-level
reachability or VMIM max timings are kept out of the primary dashboard table
unless they are needed for debugging.

## VM Workflow Basic

`vm-workflow-basic` is an alias for "vm-create, bootstorm, rebalance and
migrate". Use it when you want one adaptive workflow that exercises VM
provisioning, boot storm, rebalance, and live migration against the same VM
set.

For each round, the planner runs:

1. Create the full selected VM range in fresh round namespaces.
2. Run boot storm against those existing VMs.
3. Rebalance the selected VM range across worker nodes.
4. Wait for every selected VMI to become Running/Ready.
5. Run multi-source-node live migration with `--source-nodes all`.
6. Request VM deletion, wait briefly for VMIs to disappear, then request cleanup
   for the round namespaces before the next round. Namespace deletion is
   non-blocking so slow finalization does not stall later rounds that use fresh
   prefixes.

Example planner command:

```bash
.venv/bin/python -m adaptive.planner \
  --scenario vm-workflow-basic \
  --rounds 4 \
  --kubeconfig /path/to/kubeconfig \
  --storage-class portworx-raw-sc \
  --namespace-prefix ai-bootstorm \
  --vm-name rhel-9-vm \
  --vm-template examples/vm-templates/rhel9-vm-datasource.yaml \
  --results-folder results \
  --execute
```

Use the same `--results-folder` for comparable runs that should appear in one
dashboard. For example, run a 1-disk workflow and a follow-up 4-disk workflow
with `--results-folder results`; the dashboard will show separate `1-disk` and
`4-disk` tabs under the same storage driver. Use a different top-level results
folder only when you intentionally want a separate dashboard.

## Scenario Behavior

| Prompt | Planner scenario | Behavior |
| --- | --- | --- |
| `@virtbench vm-creation 4 rounds` | `vm-creation` | Uses fresh namespaces per round and cleans them up after each executed round. |
| `@virtbench bootstorm 4 rounds` | `bootstorm` | Creates missing VM deltas when needed, then runs boot storm. |
| `@virtbench migration 4 rounds` | `migration` | Uses existing healthy VMs when available, rebalances the selected range, then runs multi-source migration. |
| `@virtbench vm-workflow-basic 4 rounds` | `vm-workflow-basic` | Runs full fresh creation, boot storm, rebalance, and multi-source migration in each round, then cleans up before the next round. |
| `@virtbench vm-creation, bootstorm and migration 4 rounds` | `vm-workflow-basic` | Natural-language form for the combined VM workflow. |
| `@virtbench vm-create, bootstorm, rebalance and migrate 4 rounds` | `vm-workflow-basic` | Natural-language form that names each step of the combined VM workflow. |
| `@virtbench fio max-iops 6 rounds` | `fio` | Searches FIO workload settings and VM counts for the requested objective. |
| `@virtbench fio max-bandwidth 6 rounds` | `fio` | Searches FIO workload settings and VM counts for maximum bandwidth. |
| `@virtbench elbencho 10 vms` | `elbencho` | Assists with elbencho command construction and execution. |
| `@virtbench disk-ops 4 rounds` | `disk-ops` | Runs adaptive disk operation testing. |
| `@virtbench chaos 3 rounds` | `chaos` | Assisted chaos benchmark execution. |
| `@virtbench failure-recovery` | `failure-recovery` | Assisted failure-recovery execution. |
| `@virtbench validate-cluster` | `validate-cluster` | Runs cluster validation. |

## Outputs

Each AI run writes:

```text
<results-folder>/ai-runs/<timestamp>_<scenario>/virtbench_ai_rounds.json
<results-folder>/ai-runs/<timestamp>_<scenario>/virtbench_ai_analysis.json
<results-folder>/ai-runs/<timestamp>_<scenario>/virtbench_ai_analysis.md
```

Generate a dashboard from the same results folder:

```bash
.venv/bin/python dashboard/generate_dashboard.py \
  --base-dir results \
  --output-html results_dashboard.html
```

The dashboard places AI round decisions under the corresponding result tab when
the AI artifact points to folders in that result group.

## Notes

- The planner uses Kubernetes allocatable resources, current pod requests, and
  a default 15% headroom before estimating VM ceilings.
- VM creation rounds are standalone total VM counts and do not reuse VMs.
- Migration rounds can reuse healthy existing VMs, but rebalance and migration
  are scoped to the selected namespace range.
- Chaos and failure-recovery are assisted execution scenarios. The skill helps
  run them, but it does not add adaptive intelligence to those tests.

## Skill Files

Copy these files as described in [Set Up the Skill](#set-up-the-skill).

### skills.md

````markdown
---
name: virtbench
description: Run or assist VirtBench validation scenarios from prompts such as @virtbench bootstorm 4 rounds, @virtbench migration 4 rounds, @virtbench vm-creation bootstorm migration 4 rounds, @virtbench fio max-iops 6 rounds, @virtbench chaos 3 rounds, or @virtbench failure-recovery. Use when the user wants an LLM-driven KubeVirt validation workflow that estimates cluster capacity, chooses total VM counts where useful, reuses healthy resources only for scenarios where reuse is valid, executes existing virtbench scenarios, writes an auditable AI rounds artifact, and summarizes the result.
---

# VirtBench

Use this skill as the front door for adaptive KubeVirt validation with VirtBench.
The user-facing interface is the chat prompt in Codex or Claude; the deterministic engine is the `adaptive` planner in a kubevirt-benchmark checkout:

```bash
${VIRTBENCH_PYTHON:-.venv/bin/python} -m adaptive.planner --scenario <scenario> --rounds <N> --execute
```

## Locate the Repository

This skill is installed separately from the kubevirt-benchmark repository, so
find the checkout before running anything. Use the first match:

1. `$VIRTBENCH_REPO`, if set.
2. The current working directory or one of its parents, if it contains
   `adaptive/planner.py` and `virtbench/cli.py`.
3. Otherwise, ask the user for the path to their kubevirt-benchmark checkout.
   Do not clone the repository unless the user asks.

Run every planner command from the repository root (`cd "$VIRTBENCH_REPO"` or
the discovered root), so `-m adaptive.planner` resolves and relative paths such
as `results` and `examples/vm-templates/...` point inside the checkout. If the
checkout has no `adaptive/` directory, tell the user it predates the AI-assisted
workflow and must be updated.

Before the first run in a checkout, ensure the repo-local virtual environment exists:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Prompt Grammar

Accept these forms. In Codex, users may prefix prompts with `@virtbench`.
In Claude Code, users may invoke the skill as `/virtbench`, ask to use the
VirtBench skill, or write the same command without the `@` prefix.

Primary VM lifecycle workflows:

```text
@virtbench vm-creation 4 rounds
@virtbench vm-creation 4 rounds max VMs 120
@virtbench bootstorm 4 rounds
@virtbench bootstorm 4 rounds using existing VMs ai-bootstorm-1 through ai-bootstorm-80
@virtbench migration 4 rounds
@virtbench migration 4 rounds using existing VMs ai-bootstorm-1 through ai-bootstorm-80
@virtbench vm-workflow-basic 4 rounds
@virtbench vm-workflow-basic 4 rounds max VMs 120
@virtbench vm-workflow-basic 4 rounds max VMs 40 for 1-disk and 4-disk VMs
@virtbench vm-creation, bootstorm and migration 4 rounds
```

Storage and disk workflows:

```text
@virtbench fio max-iops 6 rounds
@virtbench fio max-bandwidth 6 rounds
@virtbench elbencho 10 vms
@virtbench disk-ops 4 rounds
```

Resiliency and utility workflows:

```text
@virtbench chaos 3 rounds
@virtbench failure-recovery
@virtbench validate-cluster
```

Equivalent Claude prompts:

```text
/virtbench bootstorm 4 rounds
Use the virtbench skill to run bootstorm 4 rounds
Use the virtbench skill to run vm-creation, bootstorm and migration 4 rounds
```

Treat `batches` and `iterations` as aliases for `rounds`. Treat `boot-storm` as `bootstorm`, `live migration` as `migration`, `datasource-clone` as `vm-creation`, `diskops` as `disk-ops`, and `validate` as `validate-cluster`. When the user requests creation, bootstorm, and live migration together, use `vm-workflow-basic`.
Treat prompts such as `for 1-disk and 4-disk VMs`, `for 1 disk and 4 disk
VMs`, or `for both 1-disk and 4-disk` as a request to run the same scenario
once per disk profile, using the matching VM template for each profile and the
same `--results-folder` so the dashboard shows both disk tabs together.

Treat phrases such as `using existing VMs <prefix>-1 through <prefix>-N`,
`reuse existing VMs <prefix>-1 to <prefix>-N`, or `existing namespace-prefix
<prefix> range 1-N` as a request to set `--namespace-prefix <prefix>` and
`--max-vms N`, then reuse healthy VMIs instead of creating new VMs where the
scenario supports reuse. Never apply existing-VM reuse to `vm-creation`.

Treat phrases such as `max VMs N`, `up to N VMs`, `cap at N VMs`, or `limit
to N VMs` as a request to pass `--max-vms N`. The planner should still choose
the per-round totals intelligently within that cap. `max VMs N` is a user
ceiling, not a fixed per-round size: the effective ceiling is the lower of the
cluster-estimated ceiling and N. For `vm-workflow-basic`, each round creates a
fresh full VM range for the total selected for that round, then cleans those
namespaces before the next round.
When the largest stable total equals a user-provided cap, report it as a
user-capped result, not as the discovered cluster maximum.
For `vm-workflow-basic` rounds, present the headline workflow durations:
total workflow duration, VM creation duration, bootstorm duration, and migration
duration. Keep lower-level reachability or VMIM max timings out of the primary
dashboard table unless they are needed for debugging.

## Existing Resource Preflight

Before executing any mutating workflow, check whether namespaces or VMIs already
exist for the target namespace prefix and planned range.

If matching resources already exist and the user did not explicitly request
`using existing VMs`, `reuse existing VMs`, `cleanup`, or a fresh namespace
prefix, do not run the workflow yet. Ask the user whether to:

```text
1. reuse the existing healthy VMs
2. clean up the existing namespaces and rerun
3. use a new namespace prefix
```

Never clean up existing namespaces automatically based only on an inferred
intent. This is especially important for `vm-workflow-basic`, because it starts
with VM creation and can collide with a partially completed or previous run.

For `vm-creation`, existing-VM reuse is invalid. If the target prefix already
exists, ask whether to clean up or use a new namespace prefix.

## Required Inputs

Before executing, ensure a kubeconfig is available through `KUBECONFIG`, `--kubeconfig`, or user-provided context.

Require `--storage-class` for `bootstorm`, `vm-creation`, `fio`, `disk-ops`, and `chaos`. For `migration`, require it only when existing reusable VMs are not already available. Require `--node` for `failure-recovery`. If required values cannot be inferred from the user request or environment, ask one concise question.

Use these defaults unless the user specifies otherwise:

```text
storage-driver: auto-detect when possible, otherwise ai
results-folder: results
bootstorm namespace-prefix: ai-bootstorm
migration namespace-prefix: ai-bootstorm
failure-recovery namespace-prefix: ai-bootstorm
fio namespace-prefix: ai-fio
bootstorm/migration vm-name: rhel-9-vm
fio vm-name: fio-vm
```

## Results Folder Behavior

Keep comparable runs in the same dashboard base directory by default. The
dashboard groups results by storage driver and disk count underneath one
`--base-dir`, so a follow-up such as "run the same scenario with 4 disks" must
reuse the prior `--results-folder` unless the user explicitly asks for a
different folder, a separate dashboard, or a clean result root.

Use the repo-local `results` directory when there is no prior result folder in
the conversation. If the user supplied a result folder for the first run in a
series, continue using that same folder for later variants in the same series.
Do not create timestamped top-level result folders just to distinguish disk
counts, VM counts, storage drivers, or follow-up runs; VirtBench already writes
timestamped run subdirectories and the dashboard expects those to live under one
common base.

## Execution Workflow

1. Parse scenario, objective, and round count.
2. Run the planner with `--execute` when the user clearly asked to run validation.
3. Pass `--kubeconfig` when a kubeconfig path is known.
4. Pass `--storage-class`, `--storage-driver`, `--namespace-prefix`, `--vm-name`, `--max-vms`, and the selected shared `--results-folder` when the user provided them or when continuing a comparable run series.
5. Let the planner choose total VM counts and reuse actions.
6. After completion, read `results/ai-runs/*/virtbench_ai_rounds.json` and `virtbench_ai_analysis.json`.
7. Summarize the capacity estimate, round decisions, best stable total VM count, AI validation analysis, and result artifact path.

## Failure Analysis Reporting

When a round fails, report the `failure_analysis` block from
`virtbench_ai_analysis.json` if it exists. Keep the language precise:

- `Failure reason` is the observed failure mode supported by VirtBench
  artifacts, such as `VM reachability timeout after Running/IP assignment`.
- `Analysis` explains what the artifacts prove and what they rule out, such as
  "not a VM API create or PVC clone failure" when the failed VMIs reached
  Running, had IPs, and clones completed.
- `Cause evidence` lists the concrete evidence from summaries and logs.
- `Recommended checks` lists the next cluster-level checks needed to prove root
  cause.

Do not present an inferred root cause as proven unless the artifacts contain
direct evidence. If namespaces were already cleaned up and Kubernetes events are
not available, say that the report is based on saved VirtBench logs and result
JSON. For reachability failures, prefer this wording style:

```text
Failure reason: VM reachability timeout after Running/IP assignment.
Analysis: The failed VMIs reached Running and had IPs, but did not respond to
ping from ssh-test-pod within the configured timeout. PVC clone completed, so
this was not a VM API create or PVC clone failure. Root cause still requires
checking guest network readiness, node placement, ssh-test-pod reachability,
and CNI/OVN or node-pressure events.
```

For prompts like `@virtbench elbencho 10 vms`, pass `--max-vms 10`. Do not add
`--elbencho-iops` unless the user asks for capped IOPS; the planner defaults
elbencho to `--rwmixpct 100` for an uncapped read-throughput run.

Use dry-run only when the user asks to preview:

```bash
${VIRTBENCH_PYTHON:-.venv/bin/python} -m adaptive.planner --scenario bootstorm --rounds 4 --storage-class px-csi
```

Use execution for normal `@virtbench` runs:

```bash
${VIRTBENCH_PYTHON:-.venv/bin/python} -m adaptive.planner --scenario bootstorm --rounds 4 --storage-class px-csi --execute
```

## Scenario Mapping

Adaptive scenarios:

For `vm-creation`, the planner uses existing `virtbench datasource-clone` commands with fresh namespaces every round. It never reuses VMs. It first estimates the maximum total VM count from cluster capacity and `--max-vms` if provided, then spreads standalone total VM counts across the requested rounds. For example, a 30-VM cap with 2 rounds plans 15 total VMs, then 30 total VMs. Cleanup is automatic for executed VM creation rounds so the next round is not affected by old VMs.
The planner should pass `--ping-timeout 1200` for datasource-clone-backed adaptive runs unless the user explicitly chooses another timeout.

For `bootstorm`, the planner uses existing `virtbench datasource-clone --boot-storm` commands. It reuses healthy VMs when the requested range already exists, creates only the missing delta when possible, and saves `summary_boot_storm_results.json`.

For `vm-workflow-basic`, the planner follows the documented recommended migration workflow for each round: create the full selected VM range in fresh round namespaces, run boot storm against those existing VMs with skip-creation flags, rebalance the selected VM range across worker nodes, wait for every selected VMI to become Running/Ready, then run multi-source-node live migration with `--source-nodes all`. After each executed round, request VM deletion first, wait briefly for VMIs to disappear, and then request namespace cleanup before the next round without blocking on slow namespace finalization. This is the default when the user asks to run creation, bootstorm, and live migration together. If the user says `max VMs 80` and `4 rounds`, treat 80 as the ceiling and plan intelligent totals up to that ceiling, such as 20, 40, 60, 80 when the cluster estimate is at least 80. Each selected round still deploys a fresh full VM range for that round's total.

For `migration`, the planner assumes existing VMs should be used when they are available. The default AI migration workflow is step 4 and 5 from the recommended workflow: rebalance the selected existing VM range across worker nodes, then run multi-source-node migration with `--source-nodes all`. If enough healthy VMs are not available and storage-class/template inputs are present, the planner may create the missing delta before rebalancing and migrating.

For `fio`, the planner runs a small fixed search for `max-iops` or `max-bandwidth`, using fresh per-round namespaces and cleanup. It reads `summary_fio_benchmark.json` and `fio_benchmark_results.json`.

For `disk-ops`, the planner increases VM count between rounds and calls `virtbench disk-ops`.

Assisted execution scenarios:

For `chaos`, do not add intelligence. Call `virtbench chaos-benchmark` once and map the requested round count to `--max-iterations`.

For `failure-recovery`, do not add intelligence. Require `--node` and call `virtbench failure-recovery` once.

For `elbencho`, assist with command construction and execution. Require a VM template for deploy/run-all. Map an explicit VM count to `--max-vms`.

For `validate-cluster`, call `virtbench validate-cluster`.

## Output Expectations

Always report:

- Capacity estimate: allocatable/free CPU and memory, estimated VM ceiling.
- Rounds: total VM count, success rate, user-facing AI decision, and scenario-appropriate durations or metrics.
- AI validation analysis: status, limit context, slowest workflow phase, bottleneck hints, failure reason/analysis when present, and next actions.
- Best observed stable total VM count or best FIO metric.
- Path to `virtbench_ai_rounds.json` and `virtbench_ai_analysis.json`.
- Dashboard command:

```bash
${VIRTBENCH_PYTHON:-.venv/bin/python} dashboard/generate_dashboard.py --base-dir results --output-html results_dashboard.html
```
````

### install-skill.sh

````bash
#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Install the VirtBench skill for Codex and/or Claude.

Usage:
  ./install-skill.sh [--target all|codex|claude] [--codex-home PATH] [--claude-home PATH] [--target-dir PATH] [--dry-run]

Options:
  --target VALUE     Install target: all, codex, or claude. Defaults to all.
  --codex-home PATH  Codex home directory. Defaults to $CODEX_HOME or ~/.codex.
  --claude-home PATH Claude home directory. Defaults to $CLAUDE_HOME or ~/.claude.
  --target-dir PATH  Exact skill install directory. Only valid with --target codex or --target claude.
  --dry-run          Print the planned install paths without copying.
  -h, --help         Show this help.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_SKILL="${SCRIPT_DIR}/skills.md"
CODEX_HOME_DIR="${CODEX_HOME:-${HOME}/.codex}"
CLAUDE_HOME_DIR="${CLAUDE_HOME:-${HOME}/.claude}"
TARGET="all"
TARGET_DIR=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      if [[ $# -lt 2 ]]; then
        echo "error: --target requires all, codex, or claude" >&2
        exit 2
      fi
      TARGET="$2"
      shift 2
      ;;
    --codex-home)
      if [[ $# -lt 2 ]]; then
        echo "error: --codex-home requires a path" >&2
        exit 2
      fi
      CODEX_HOME_DIR="$2"
      shift 2
      ;;
    --claude-home)
      if [[ $# -lt 2 ]]; then
        echo "error: --claude-home requires a path" >&2
        exit 2
      fi
      CLAUDE_HOME_DIR="$2"
      shift 2
      ;;
    --target-dir)
      if [[ $# -lt 2 ]]; then
        echo "error: --target-dir requires a path" >&2
        exit 2
      fi
      TARGET_DIR="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "${SOURCE_SKILL}" ]]; then
  echo "error: skill source not found: ${SOURCE_SKILL}" >&2
  exit 1
fi

if [[ "${TARGET}" != "all" && "${TARGET}" != "codex" && "${TARGET}" != "claude" ]]; then
  echo "error: --target must be all, codex, or claude" >&2
  exit 2
fi

if [[ -n "${TARGET_DIR}" && "${TARGET}" == "all" ]]; then
  echo "error: --target-dir requires --target codex or --target claude" >&2
  exit 2
fi

echo "VirtBench skill source: ${SOURCE_SKILL}"

install_one() {
  local name="$1"
  local dir="$2"
  local skill_path="${dir}/SKILL.md"

  echo "${name} skill target: ${skill_path}"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    return
  fi

  mkdir -p "${dir}"
  install -m 0644 "${SOURCE_SKILL}" "${skill_path}"
  echo "Installed ${name} skill to ${skill_path}"
}

if [[ "${DRY_RUN}" -eq 1 ]]; then
  case "${TARGET}" in
    all)
      install_one "Codex" "${CODEX_HOME_DIR}/skills/virtbench"
      install_one "Claude" "${CLAUDE_HOME_DIR}/skills/virtbench"
      ;;
    codex)
      install_one "Codex" "${TARGET_DIR:-${CODEX_HOME_DIR}/skills/virtbench}"
      ;;
    claude)
      install_one "Claude" "${TARGET_DIR:-${CLAUDE_HOME_DIR}/skills/virtbench}"
      ;;
  esac
  echo "Dry run complete. No files were changed."
  exit 0
fi

case "${TARGET}" in
  all)
    install_one "Codex" "${CODEX_HOME_DIR}/skills/virtbench"
    install_one "Claude" "${CLAUDE_HOME_DIR}/skills/virtbench"
    ;;
  codex)
    install_one "Codex" "${TARGET_DIR:-${CODEX_HOME_DIR}/skills/virtbench}"
    ;;
  claude)
    install_one "Claude" "${TARGET_DIR:-${CLAUDE_HOME_DIR}/skills/virtbench}"
    ;;
esac

echo "Restart Codex or Claude so the updated skill is loaded, then use prompts such as:"
echo "  Codex:  @virtbench bootstorm 4 rounds"
echo '  Claude: /virtbench bootstorm 4 rounds'
````
