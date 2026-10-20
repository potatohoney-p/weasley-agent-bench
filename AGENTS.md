# Working on Agent Bench

- Keep execution offline. The evaluator does not call models, tools, or networks.
- Preserve strict versioned contracts; unknown input must not silently change scores.
- Keep fixture data synthetic and label it as such.
- Missing cases must remain visible in evaluation and cannot be dropped for comparison.
- Run `python -m unittest discover -s tests -v` and the README demo before committing.
- Update `docs/contracts.md` for any schema or scoring behavior change.
- Keep this repository private unless the owner specifically authorizes publication.
