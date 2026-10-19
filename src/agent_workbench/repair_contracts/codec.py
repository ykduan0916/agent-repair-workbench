"""P0-06 role dispatch reuses the P0-05 strict, bounded JSON decoder."""

from agent_workbench.agents.diagnosis.repair_contracts import (
    ADAPTER as DIAGNOSIS_ADAPTER,
)
from agent_workbench.agents.diagnosis.repair_contracts import DiagnosisOutput
from agent_workbench.agents.repair.repair_contracts import ADAPTER as REPAIR_ADAPTER
from agent_workbench.agents.repair.repair_contracts import RepairOutput
from agent_workbench.agents.single.repair_contracts import ADAPTER as SINGLE_ADAPTER
from agent_workbench.agents.single.repair_contracts import SingleOutput
from agent_workbench.agents.verification.repair_contracts import (
    ADAPTER as VERIFICATION_ADAPTER,
)
from agent_workbench.agents.verification.repair_contracts import VerificationOutput
from agent_workbench.orchestration.codec import decode
from agent_workbench.orchestration.policy import PolicyError
from agent_workbench.repair_contracts.common import RepairRole

type RepairRoleOutput = DiagnosisOutput | RepairOutput | VerificationOutput | SingleOutput


def decode_role(raw: bytes, role: RepairRole, *, diagnostic: bool = False) -> RepairRoleOutput:
    if role == "diagnosis":
        return decode(raw, DIAGNOSIS_ADAPTER, diagnostic=diagnostic)
    if role == "repair":
        return decode(raw, REPAIR_ADAPTER, diagnostic=diagnostic)
    if role == "verification":
        return decode(raw, VERIFICATION_ADAPTER, diagnostic=diagnostic)
    if role == "single":
        return decode(raw, SINGLE_ADAPTER, diagnostic=diagnostic)
    raise PolicyError("UNKNOWN_ROLE")
