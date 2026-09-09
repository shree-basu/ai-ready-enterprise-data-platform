"""Fail when repository automation or tracked files gain deployment capability."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_WORKFLOW_PATTERNS = {
    r"id-token:\s*write": "OIDC write permission",
    r"\bsecrets\.": "GitHub secret access",
    r"pull_request_target": "privileged pull-request trigger",
    r"workflow_dispatch": "manual workflow trigger",
    r"google-github-actions/auth": "Google Cloud authentication",
    r"\bgcloud\b": "Google Cloud CLI",
    r"\bgsutil\b": "Cloud Storage CLI",
    r"\bbq\s+query\b": "BigQuery execution",
    r"\bterraform\s+(?:apply|destroy)\b": "Terraform mutation",
    r"\bdocker\s+push\b": "container publication",
    r"\bdatabricks\b": "Databricks command",
    r"\bcloud\s+run\s+deploy\b": "Cloud Run deployment",
    r"\bdataflow\b.*\b(?:run|submit|create)\b": "Dataflow submission",
    r"\bvertex\b.*\bdeploy": "Vertex deployment",
    r"\bagent\s+runtime\b.*\bdeploy": "agent runtime deployment",
    r"\bself-hosted\b": "self-hosted runner",
    r"actions/upload-artifact": "workflow artifact upload",
}

FORBIDDEN_FILE_NAMES = {
    ".env",
    "application_default_credentials.json",
    "service-account.json",
    "service_account.json",
}

CREDENTIAL_CONTENT_PATTERNS = {
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----": "private key material",
    r'"type"\s*:\s*"service_account"': "service-account JSON",
    r'"private_key"\s*:\s*"-----BEGIN': "embedded service-account key",
}


def _repository_files(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and not any(part in {".git", ".venv"} for part in path.parts)
    ]


def violations(root: Path = ROOT) -> list[str]:
    """Return safety violations without modifying the repository."""

    findings: list[str] = []
    workflow_root = root / ".github" / "workflows"
    for path in sorted(workflow_root.glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(root)
        if not re.search(r"permissions:\s*\n\s+contents:\s*read\b", text):
            findings.append(f"{relative}: workflow permissions are not contents: read")
        for runner in re.findall(r"runs-on:\s*([^\s#]+)", text):
            if runner != "ubuntu-latest":
                findings.append(f"{relative}: disallowed runner {runner}")
        for pattern, label in FORBIDDEN_WORKFLOW_PATTERNS.items():
            if re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL):
                findings.append(f"{relative}: {label}")

    for path in _repository_files(root):
        relative = path.relative_to(root)
        if path.name.lower() in FORBIDDEN_FILE_NAMES:
            findings.append(f"{relative}: forbidden credential filename")
            continue
        if path.suffix.lower() not in {".json", ".pem", ".key", ".txt", ".yaml", ".yml"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern, label in CREDENTIAL_CONTENT_PATTERNS.items():
            if re.search(pattern, text, flags=re.IGNORECASE):
                findings.append(f"{relative}: {label}")
    return findings


def main() -> None:
    findings = violations()
    if findings:
        raise SystemExit("Unsafe repository state:\n" + "\n".join(findings))
    print("Repository is cloud-free and deployment-disabled.")


if __name__ == "__main__":
    main()
