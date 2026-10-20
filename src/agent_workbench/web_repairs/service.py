"""B2 lifecycle-owned execution: HTTP waiters never own a repair's lifetime."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from agent_workbench.candidates.snapshots import SnapshotStore
from agent_workbench.controlled_tools.resources import load_repair_case
from agent_workbench.controlled_tools.runtime import _owned
from agent_workbench.experience_memory.agent_models import agent_budget
from agent_workbench.mcp_config import ROOT
from agent_workbench.model_gateway.gateway import Config
from agent_workbench.model_gateway.repair import RepairAdapter, RepairDeepSeekAdapter
from agent_workbench.repair_runtime.controller import RepairController
from agent_workbench.repair_store.fencing import RepairFence
from agent_workbench.repair_store.models import RepairTask
from agent_workbench.run_store.models import StoreError
from agent_workbench.web_analyses.catalog import model_config
from agent_workbench.web_memory.agent_service import make_session
from agent_workbench.web_runs.service import Admission

from . import catalog
from .fencing import RepairWebFence
from .models import WEB_BUDGET, RepairReceipt, RepairRequest
from .policy import policy_sha256
from .recovery import WebRepairRecovery
from .store import WebRepairStore

T = TypeVar("T")


def failure(code: str) -> Admission:
    codes = {
        "request_conflict": 409,
        "request_gone": 410,
        "recovery_required": 409,
        "version_mismatch": 409,
        "repair_busy": 429,
        "repair_reads_busy": 429,
        "web_repair_not_found": 404,
        "schema_incompatible": 503,
        "model_not_configured": 503,
        "model_configuration_invalid": 503,
        "input_bound_unverified": 503,
        "image_unavailable": 503,
        "store_unavailable": 503,
        "service_stopping": 503,
    }
    return Admission(
        codes.get(code, 503), {"error": code if code in codes else "store_unavailable"}
    )


def receipt_body(receipt: RepairReceipt, *, reused: bool = True) -> dict[str, object]:
    return {
        "schema_version": 1,
        "request_id": receipt.request.request_id,
        "task_id": receipt.saved.task.task_id,
        "case_id": receipt.request.case_id,
        "mode": receipt.request.mode,
        "task": receipt.saved.task.model_dump(mode="json"),
        "sha256": receipt.saved.task_sha256,
        "cancel_requested": receipt.cancel_requested,
        "active": receipt.active,
        "reused": reused,
    }


class RepairService:
    def __init__(
        self,
        directory: Path,
        *,
        image_root: Path = ROOT,
        config_loader: Callable[[], Config] = Config.from_env,
        adapter_factory: Callable[[Config], RepairAdapter] = RepairDeepSeekAdapter,
    ) -> None:
        self.directory, self.image_root = directory, image_root
        self.config_loader, self.adapter_factory = config_loader, adapter_factory
        self.tasks: set[asyncio.Task[None]] = set()
        self.active_ids: set[str] = set()
        self.reads = asyncio.Semaphore(2)
        self.admissions = asyncio.Semaphore(2)
        self.stopping = False

    async def database(self, operation: Callable[[WebRepairStore], T]) -> T:
        def invoke() -> T:
            store = WebRepairStore(self.directory)
            try:
                return operation(store)
            finally:
                store.close()

        value, _ = await _owned(asyncio.to_thread(invoke), cancel_operation=False)
        return value

    async def read(self, task_id: str, *, cancel: bool = False) -> Admission:
        if self.reads.locked():
            return failure("repair_reads_busy")
        async with self.reads:
            try:
                receipt = await self.database(
                    lambda store: (
                        store.request_cancel(task_id) if cancel else store.receipt(task_id)
                    )
                )
                status = 202 if cancel and receipt.saved.task.phase != "finished" else 200
                return Admission(status, receipt_body(receipt))
            except StoreError as error:
                return failure(error.code)
            except Exception:
                return failure("store_unavailable")

    async def events(self, task_id: str) -> Admission:
        if self.reads.locked():
            return failure("repair_reads_busy")
        async with self.reads:
            try:
                return Admission(
                    200, await self.database(lambda store: store.public_events(task_id))
                )
            except StoreError as error:
                return failure(error.code)
            except Exception:
                return failure("store_unavailable")

    async def catalog(self) -> Admission:
        if self.reads.locked():
            return failure("repair_reads_busy")
        async with self.reads:
            owner = RepairWebFence(self.directory)
            try:
                active = await self.database(lambda store: store.active_id())
                try:
                    owner.acquire()
                except StoreError as error:
                    if error.code != "repair_busy":
                        raise
                    status = "busy"
                else:
                    status = "recovery_required" if active else "available"
                _, reason = model_config(self.config_loader)
                return Admission(
                    200,
                    {
                        "schema_version": 1,
                        "cases": catalog.CASES,
                        "modes": catalog.MODES,
                        "budget": WEB_BUDGET.model_dump(mode="json"),
                        "model": {
                            "configured": reason is None,
                            "reason": reason.lower() if reason else None,
                        },
                        "image": {"prepared": catalog.image_ready(self.image_root)},
                        "slot": {"status": status, "task_id": active},
                        "accepting": not self.stopping,
                    },
                )
            except StoreError as error:
                return failure(error.code)
            except Exception:
                return failure("store_unavailable")
            finally:
                owner.close()

    async def submit(self, request: RepairRequest) -> Admission:
        if self.stopping:
            return failure("service_stopping")
        if self.admissions.locked():
            return failure("repair_busy")
        await self.admissions.acquire()
        response: asyncio.Future[Admission] = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(self._serve(request, response))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        # Admission gate belongs to the owner, not the disconnected HTTP waiter.
        response.add_done_callback(lambda _: self.admissions.release())
        return await asyncio.shield(response)

    async def _serve(self, request: RepairRequest, response: asyncio.Future[Admission]) -> None:
        owner = RepairWebFence(self.directory)
        fence = RepairFence(self.directory, request.request_id)
        store = WebRepairStore(self.directory)
        try:
            existing = await self.database(lambda db: db.existing(request))
            if existing is not None:
                response.set_result(await self.read(request.request_id))
                return
            try:
                owner.acquire()
            except StoreError:
                existing = await self.database(lambda db: db.existing(request))
                if existing is not None:
                    response.set_result(await self.read(request.request_id))
                    return
                raise
            # A previous owner may have committed between the first lookup and flock.
            existing = await self.database(lambda db: db.existing(request))
            if existing is not None:
                response.set_result(await self.read(request.request_id))
                return
            active = await self.database(lambda db: db.active_id())
            if active:
                with RepairFence(self.directory, active) as old_fence:
                    result = await WebRepairRecovery(store, self.image_root).release_checked(
                        active,
                        owner=owner,
                        fence=old_fence,
                        apply=True,
                    )
                if not result.released:
                    raise StoreError("recovery_required")
            config, reason = model_config(self.config_loader)
            if config is None:
                raise StoreError(reason.lower() if reason else "model_not_configured")
            adapter = self.adapter_factory(config)
            await catalog.preflight(self.image_root)
            if self.stopping:
                raise StoreError("service_stopping")
            fence.acquire()
            registry = load_repair_case(
                request.case_id,
                task_id=request.request_id,
                budget=agent_budget(request.memory, WEB_BUDGET),
            )
            snapshots = SnapshotStore(self.directory / "repair-snapshots")
            baseline = snapshots.create_baseline(str(uuid4()), case_id=request.case_id)
            task = RepairTask(
                task_id=request.request_id,
                mode=request.mode,
                baseline=baseline.ref,
                started_at=datetime.now(UTC),
                budget=agent_budget(request.memory, WEB_BUDGET),
                model_config_sha256=adapter.config_sha256,
            )
            admission = store.admit(
                request,
                task,
                policy_sha256=policy_sha256(),
                owner=owner,
                task_fence=fence,
            )
            receipt = store.receipt(request.request_id)
            response.set_result(
                Admission(
                    202 if admission.claim else 200,
                    receipt_body(receipt, reused=admission.claim is None),
                )
            )
            if admission.claim is None:
                return
            self.active_ids.add(request.request_id)
            if self.stopping:
                store.request_cancel(request.request_id)
            await RepairController(
                store=store,
                snapshots=snapshots,
                baseline=baseline,
                registry=registry,
                adapter=adapter,
                mode=request.mode,
                image_root=self.image_root,
                memory=make_session(self.directory, request.memory, registry, "repair"),
            ).run_created(admission.claim, admission.saved, fence)
            await WebRepairRecovery(store, self.image_root).release_checked(
                request.request_id,
                owner=owner,
                fence=fence,
                apply=True,
            )
        except StoreError as error:
            if not response.done():
                response.set_result(failure(error.code))
        except Exception:
            if not response.done():
                response.set_result(failure("store_unavailable"))
        finally:
            self.active_ids.discard(request.request_id)
            store.close()
            fence.close()
            owner.close()
            if not response.done():
                response.set_result(failure("service_stopping"))

    async def _request_stop(self, task_id: str) -> None:
        await self.database(lambda db: db.request_cancel(task_id))

    async def close(self) -> None:
        self.stopping = True

        async def drain() -> None:
            for task_id in tuple(self.active_ids):
                try:
                    await self._request_stop(task_id)
                except Exception:
                    # Controller's watcher stops on a broken persistent cancellation channel.
                    pass
            if self.tasks:
                await asyncio.gather(*self.tasks, return_exceptions=True)

        await _owned(drain(), cancel_operation=False)
