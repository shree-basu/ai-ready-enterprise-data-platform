from pathlib import Path

from scripts.check_repo_safety import violations


def test_current_repository_cannot_authenticate_or_deploy() -> None:
    assert violations() == []


def test_unsafe_workflow_is_rejected(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "deploy.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        """name: unsafe
on: workflow_dispatch
permissions:
  contents: read
  id-token: write
jobs:
  deploy:
    runs-on: self-hosted
    steps:
      - run: gcloud dataflow jobs run example
""",
        encoding="utf-8",
    )

    findings = violations(tmp_path)

    assert any("manual workflow trigger" in finding for finding in findings)
    assert any("OIDC write permission" in finding for finding in findings)
    assert any("self-hosted runner" in finding for finding in findings)
    assert any("Dataflow submission" in finding for finding in findings)


def test_unpinned_action_and_paid_runner_are_rejected(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "quality.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        """name: unsafe supply chain
on: push
permissions:
  contents: read
jobs:
  quality:
    runs-on: ubuntu-8-core
    steps:
      - uses: actions/checkout@v7
""",
        encoding="utf-8",
    )

    findings = violations(tmp_path)

    assert any("disallowed runner" in finding for finding in findings)
    assert any("not pinned to a full commit SHA" in finding for finding in findings)
