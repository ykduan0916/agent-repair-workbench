"""P1-01b lifespan-owned analysis, durable admission and one-shot cancellation."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from agent_workbench.analysis_store.fencing import AnalysisFence
from agent_workbench.controlled_tools.resources import load_case
from agent_workbench.experience_memory.agent_models import agent_budget
from agent_workbench.mcp_config import canonical, digest
from agent_workbench.model_gateway.gateway import Adapter, Config, DeepSeekAdapter
from agent_workbench.orchestration.runtime import AnalysisRuntime
from agent_workbench.run_store.models import StoreError
from agent_workbench.web_analyses.catalog import CASES, INTENTS, model_config
from agent_workbench.web_analyses.fencing import AnalysisWebFence
from agent_workbench.web_analyses.models import WEB_BUDGET, AnalysisAdmission, AnalysisRequest
from agent_workbench.web_analyses.store import WebAnalysisStore
from agent_workbench.web_memory.agent_service import make_session
from agent_workbench.web_runs.service import Admission

T = TypeVar("T")
POLICY_SHA256 = digest(
    canonical({"version": 1, "cases": CASES, "intents": INTENTS, "budget": WEB_BUDGET.model_dump()})
)


def failure(code: str) -> Admission:
    statuses = {
        "request_conflict": 409,
        "request_gone": 410,
        "analysis_busy": 429,
        "recovery_required": 409,
        "web_analysis_not_found": 404,
        "store_not_initialized": 503,
        "schema_incompatible": 503,
        "locking_unavailable": 503,
        "service_stopping": 503,
        "MODEL_NOT_CONFIGURED": 503,
        "MODEL_CONFIGURATION_INVALID": 503,
        "INPUT_BOUND_UNVERIFIED": 503,
    }
    if code == "not_found":
        code = "store_not_initialized"
    return Admission(
        statuses.get(code, 503), {"error": code if code in statuses else "store_unavailable"}
    )


def accepted(admission: AnalysisAdmission) -> Admission:
    return Admission(
        202 if admission.claim is not None else 200,
        {
            "task_id": admission.saved.summary.task_id,
            "summary": admission.saved.summary.model_dump(mode="json"),
            "sha256": admission.saved.sha256,
            "reused": admission.claim is None,
            "cancel_requested": admission.cancel_requested,
        },
    )


class AnalysisService:
    def __init__(
        self,
        directory: Path,
        *,
        image_root: Path | None = None,
        config_loader: Callable[[], Config] = Config.from_env,
        adapter_factory: Callable[[Config], Adapter] = DeepSeekAdapter,
    ) -> None:
        self.directory, self.image_root = directory, image_root
        self.config_loader, self.adapter_factory = config_loader, adapter_factory
        self.tasks: set[asyncio.Task[None]] = set()
        self.stopping = False

    async def database(self, operation: Callable[[WebAnalysisStore], T]) -> T:
        def invoke() -> T:
            store = WebAnalysisStore(self.directory)
            try:
                return operation(store)
            finally:
                store.close()

        return await asyncio.to_thread(invoke)

    async def catalog(self) -> Admission:
        owner = AnalysisWebFence(self.directory)
        try:
            # Validate DB before attempting locks; GET never initializes or migrates it.
            active = await self.database(lambda store: store.active_id())
            try:
                owner.acquire()
            except StoreError as error:
                if error.code != "analysis_busy":
                    raise
                active = await self.database(lambda store: store.active_id())
                if active is None:
                    # A live owner is still validating resources, before durable admission.
                    return failure("analysis_busy")
                status = "busy"
            else:
                safe = await self.database(lambda store: store.release_safe(owner))
                active = None if safe else await self.database(lambda store: store.active_id())
                status = "available" if safe else "recovery_required"
            _, reason = model_config(self.config_loader)
            return Admission(
                200,
                {
                    "schema_version": 1,
                    "cases": CASES,
                    "intents": INTENTS,
                    "model": {"configured": reason is None, "reason": reason},
                    "slot": {"status": status, "task_id": active},
                },
            )
        except StoreError as error:
            return failure(error.code)
        except Exception:
            return failure("store_unavailable")
        finally:
            owner.close()

    async def submit(self, request: AnalysisRequest) -> Admission:
        if self.stopping:
            return failure("service_stopping")
        response: asyncio.Future[Admission] = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(self._serve(request, response))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return await asyncio.shield(response)

    async def _serve(self, request: AnalysisRequest, response: asyncio.Future[Admission]) -> None:
        owner = AnalysisWebFence(self.directory)
        fence = AnalysisFence(self.directory, request.request_id)
        store: WebAnalysisStore | None = None
        try:
            existing = await self.database(lambda db: db.existing(request))
            if existing is not None:
                response.set_result(accepted(existing))
                return
            try:
                owner.acquire()
            except StoreError:
                existing = await self.database(lambda db: db.existing(request))
                if existing is not None:
                    response.set_result(accepted(existing))
                    return
                raise
            if not await self.database(lambda db: db.release_safe(owner)):
                raise StoreError("recovery_required")
            adapter = None
            if request.intent != "check_only":
                config, reason = model_config(self.config_loader)
                if reason is not None or config is None:
                    response.set_result(failure(reason or "MODEL_CONFIGURATION_INVALID"))
                    return
                adapter = self.adapter_factory(config)
            registry = await asyncio.to_thread(
                load_case,
                request.case_name,
                task_id=request.request_id,
                intent=request.intent,
                budget=agent_budget(request.memory, WEB_BUDGET),
            )
            if self.stopping:
                raise StoreError("service_stopping")
            fence.acquire()
            store = WebAnalysisStore(self.directory)
            runtime = AnalysisRuntime(
                registry,
                store,
                adapter,
                image_root=self.image_root,
                memory=make_session(self.directory, request.memory, registry, "analysis"),
            )
            admission = store.admit(
                request,
                runtime.initial_summary(),
                policy_sha256=POLICY_SHA256,
                owner=owner,
                fence=fence,
            )
            response.set_result(accepted(admission))
            if admission.claim is not None:
                await self._execute(request, runtime, admission, fence)
                store.release_safe(owner)
        except StoreError as error:
            if not response.done():
                response.set_result(failure(error.code))
        except Exception:
            # Preserve accepted claim/slot; never retry or invent an execution outcome.
            if not response.done():
                response.set_result(failure("store_unavailable"))
        finally:
            if store is not None:
                store.close()
            fence.close()
            owner.close()
            if not response.done():
                response.set_result(failure("service_stopping"))

    async def _execute(
        self,
        request: AnalysisRequest,
        runtime: AnalysisRuntime,
        admission: AnalysisAdmission,
        fence: AnalysisFence,
    ) -> None:
        assert admission.claim is not None
        if self.stopping:
            await self.database(lambda db: db.request_cancel(request.request_id))
        execution = asyncio.create_task(
            runtime.run_created(admission.claim, admission.saved, fence=fence)
        )

        async def watch() -> None:
            while not execution.done():
                # Let run_created consume the claim and install its terminal-save handler.
                await asyncio.sleep(0.05)
                if execution.done():
                    return
                requested = await self.database(
                    lambda db: db.cancellation_requested(request.request_id)
                )
                if self.stopping and not requested:
                    await self.database(lambda db: db.request_cancel(request.request_id))
                    requested = True
                if requested:
                    execution.cancel()  # Once only: cleanup is allowed to finish.
                    return

        monitor = asyncio.create_task(watch())
        try:
            done, _ = await asyncio.wait({execution, monitor}, return_when=asyncio.FIRST_COMPLETED)
            if monitor in done and monitor.exception() is not None:
                # A broken cancellation channel must not leave an unbounded live operation.
                execution.cancel()
            await execution
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)

    async def cancel(self, task_id: str) -> Admission:
        try:
            requested = await self.database(lambda db: db.request_cancel(task_id))
            return Admission(
                202 if requested else 200,
                {
                    "task_id": task_id,
                    "status": "cancel_requested" if requested else "already_finished",
                },
            )
        except StoreError as error:
            return failure(error.code)
        except Exception:
            return failure("store_unavailable")

    async def close(self) -> None:
        self.stopping = True
        # Existing runtime has a 600s budget, bounded model timeout and tool cleanup.
        # The cancellation monitor interrupts it once, then final durable writes complete.
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
