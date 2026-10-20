# Public portfolio overlap audit — 2026-10-20

Only public project documentation and source were inspected. No private company
repositories, historical activity fixtures, or production traces were used.

| Existing project | Inspected evidence | Boundary retained here |
| --- | --- | --- |
| weasley-assistant | README, `weasley_assistant/ops/scoring.py`, `evals/`, ops module inventory | Assistant's scorer checks its expected tool, argument substrings, and minimum tool count. This package consumes independent versioned task/result files, structured final-state assertions, ordered outcomes, caps, and paired coverage. It does not invoke Assistant or duplicate its live arena/judge. |
| weasley-autoresearch | README | Optimizes code through measured experiments, correctness checks, and keep/revert decisions. Bench compares saved agent task outcomes; it never changes a worktree or runs a benchmark command. |
| weasley-deepmind | README, tracked benchmark/evaluation file inventory | Operates durable memory and evaluates recall in its runtime. Bench can state portable memory task contracts, but stores no memory and makes no recall calls. |
| weasley-subagents | README and tracked eval inventory | Orchestrates child agents with lifecycle and acceptance guards. Bench scores recorded outputs; it schedules no agents. |
| weasley-podex | README and tracked eval inventory | Runs a multi-provider agent workspace. Bench is a dependency-free offline consumer of explicit result exports. |
| weasley-frame | README and pipeline schema context from parent audit | Builds live-web datasets; schema/source-quality checks belong in Frame. Bench does not crawl or build datasets. |
| potaty | README | Produces and evaluates evidence-bearing Diagram IR. Bench does not import repositories or render diagrams. |
| potato_wiki_box | README | Maintains source-backed Markdown knowledge, retrieval, and review. Bench owns no knowledge store. |

The separately proposed `weasley-trace-replay` handles trace validation and
replay/differential execution. This project deliberately does not replay traces;
an adapter could export replay outcomes as saved run results in a later revision.

The account inventory checked by the coordinating task showed no genuine
engineering repository created in the week beginning 2026-10-19. GitHub returned
404 for `potatohoney-p/weasley-agent-bench` before implementation. The local MVP
does not count as a newly created private GitHub repository until publication is
authorized, performed, and verified.
