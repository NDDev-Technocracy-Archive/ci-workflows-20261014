# Evidence orchestration caller pattern

Run the compiler in a caller-native planning job, save its outputs, and call the
existing reusable workflows conditionally. Do not copy their implementation.
The planning job's own timeout/concurrency policy belongs to the caller; elapsed
duration is never an evidence-selection or pass/fail input.

```yaml
jobs:
  evidence-plan:
    runs-on: ubuntu-latest
    permissions: { contents: read }
    outputs:
      selected: ${{ steps.plan.outputs.selected_lane_ids }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          repository: NDDev-Technocracy-Archive/ci-workflows-20261014
          ref: <sha>
          path: .ci-workflows
          persist-credentials: false
      - id: python
        uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: '3.13'
          update-environment: false
      - uses: astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9 # v9.0.0
        with:
          version: 0.13.1
      - name: Provision the compiler environment
        env:
          PYTHON_PATH: ${{ steps.python.outputs.python-path }}
        run: |
          "$PYTHON_PATH" -I -B -m venv --copies .ci-workflows/.venv
          uv pip install --python .ci-workflows/.venv/bin/python \
            --require-hashes -r .ci-workflows/requirements-ci.txt
      - id: plan
        run: |
          cd .ci-workflows
          .venv/bin/python -I -B scripts/check_python_syntax.py
          .venv/bin/python -I -B scripts/check_python_execution_contract.py \
            --launch compile_evidence_plan.py -- \
            --level pr-required --platform github-actions --os ubuntu --arch x64 \
            --profile public --risks code,security --changes workflows

  actionlint:
    needs: evidence-plan
    if: contains(fromJSON(needs.evidence-plan.outputs.selected), 'actionlint')
    permissions: { contents: read }
    uses: NDDev-Technocracy-Archive/ci-workflows-20261014/.github/workflows/actionlint.yml@<sha>
    with: { runner: ubuntu-latest }
```

For `native-disposable-host` lanes, the output contains a handoff rather than a
workflow. The owning fleet/bootstrap repository must prove every declared host
capability on a one-job disposable machine; this repository does not simulate
reboot, GUI/session, SSH/network, or system-hardening evidence on hosted CI.
