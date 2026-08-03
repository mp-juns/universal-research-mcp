"""Read-only audit findings derived from canonical records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from core.ledger import validate_records


@dataclass(frozen=True)
class AuditFinding:
    finding_id: str
    audit_type: str
    severity: str
    subject_ref: str
    rule_ref: str
    evidence_refs: list[str]
    reason: str
    recommended_next_action: str
    status: str = "open"


def audit_records(records: list[dict[str, Any]]) -> list[AuditFinding]:
    """Return evidence-addressable findings without mutating or approving records."""

    findings: list[AuditFinding] = []
    for number, issue in enumerate(validate_records(records), 1):
        findings.append(AuditFinding(
            finding_id=f"finding_validation_{number:04d}",
            audit_type="policy" if "approval" in issue.message else "record_integrity",
            severity=issue.severity,
            subject_ref=issue.record_id,
            rule_ref="core/1.0",
            evidence_refs=[f"record://{issue.record_id}{issue.path}"],
            reason=issue.message,
            recommended_next_action="Record an amendment or obtain the required human review; do not rewrite the canonical record.",
        ))
    for record in records:
        record_id = str(record.get("record_id") or record.get("event_id") or "<unknown>")
        if record.get("schema_version") == "core/1.0" and record.get("record_kind") == "decision":
            actor = record.get("created_by") or {}
            reviewer = (record.get("payload") or {}).get("human_reviewer_ref")
            if actor.get("actor_type") == "ai" and not reviewer:
                findings.append(AuditFinding(
                    finding_id=f"finding_contribution_{len(findings)+1:04d}",
                    audit_type="contribution",
                    severity="warning",
                    subject_ref=record_id,
                    rule_ref="core/1.0/contribution",
                    evidence_refs=[f"record://{record_id}/created_by"],
                    reason="AI-authored decision has no recorded human reviewer.",
                    recommended_next_action="Add a human review record or keep the decision as a proposal.",
                ))
    return findings


def audit_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    findings = [asdict(finding) for finding in audit_records(records)]
    return {"record_count": len(records), "finding_count": len(findings), "findings": findings}
