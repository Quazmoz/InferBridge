"""Role-based resident model slots: one chat, one embedding and one reranker model.

InferBridge's manager can hold several engines. This extension makes that predictable:

* **One model per role.** Loading a chat model replaces only the resident chat model;
  the embedding and reranker slots are untouched (and vice versa). A device switch of
  the *same* model keeps the existing handoff behavior (the old engine stays usable
  until the replacement compiles).
* **Memory preflight.** Before a model joins *other* resident slots, the combined
  footprint is estimated from the hardware advisor and the load is refused with a
  friendly message when it clearly cannot fit (or warned when it is tight). The first
  model loaded on an empty manager keeps the previous single-model behavior.
* **Conservative NPU policy.** Two resident engines on the Intel NPU are refused by
  default because NPU contexts are not verified to coexist.

Requests already route by their exact ``model`` id to the matching engine, and every
engine keeps its own lock, so slots serve concurrently while each slot is serialized.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.model_roles import ROLE_CHAT, ROLE_LABELS, ROLES, role_for_backend

_INSTALL_FLAG = "_MODEL_SLOTS_INSTALLED"
# Same headroom rule as the context editor: keep the larger of 2 GiB or 15% free.
_MIN_RESERVE_GIB = 2.0
_RESERVE_FRACTION = 0.15
# A queued load that finishes while the replaced model is still serving waits for it.
_ENFORCE_RETRY_SECONDS = 0.5
_ENFORCE_MAX_ATTEMPTS = 1200  # ten minutes; ponytail: polling, an idle event would be exact


class SlotConflictError(ValueError):
    """A slot operation conflicts with work in progress (HTTP 409)."""


class SlotResourceError(ValueError):
    """A load would exceed the estimated memory or NPU policy (HTTP 409)."""

    def __init__(self, message: str, plan: dict[str, Any]) -> None:
        super().__init__(message)
        self.plan = plan


def _active(task: asyncio.Task | None) -> bool:
    return bool(task is not None and not task.done())


def _uses_npu(device: object) -> bool:
    return "NPU" in str(device or "").upper()


def _simulated(manager: Any) -> bool:
    """Mock engines never touch real accelerators, so hardware rules only advise."""

    return bool(getattr(manager, "force_mock", False))


def _name(manager: Any, model_id: str) -> str:
    cfg = manager.catalog.get(model_id)
    return cfg.name if cfg is not None else model_id


def model_role(manager: Any, model_id: str) -> str:
    cfg = manager.catalog.get(model_id)
    if cfg is not None:
        return role_for_backend(cfg.backend)
    engine = manager.engines.get(model_id)
    return role_for_backend(getattr(engine, "backend", ""))


def resident_models(manager: Any, role: str) -> list[str]:
    """Resident model ids in one role (normally zero or one)."""

    return [model_id for model_id in list(manager.engines) if model_role(manager, model_id) == role]


def loading_models(manager: Any, role: str) -> list[str]:
    """Models of one role with an active load task that are not yet resident."""

    return [
        model_id
        for model_id, task in list(getattr(manager, "load_tasks", {}).items())
        if _active(task)
        and model_id not in manager.engines
        and model_role(manager, model_id) == role
    ]


def resident_for_role(manager: Any, role: str) -> str | None:
    residents = resident_models(manager, role)
    return residents[0] if residents else None


def _estimate_gib(manager: Any, model_id: str, device: str | None) -> float | None:
    cfg = manager.catalog.get(model_id)
    advisor = getattr(manager, "advisor", None)
    if cfg is None or advisor is None:
        return None
    try:
        value = advisor.estimate_model(cfg, device=device).get("runtime_memory_gb")
        return round(float(value), 2) if value is not None else None
    except Exception:  # noqa: BLE001 - estimates are advisory and must never break status
        return None


def _snapshot(manager: Any) -> dict[str, Any]:
    advisor = getattr(manager, "advisor", None)
    if advisor is None:
        return {}
    try:
        return advisor.hardware_snapshot() or {}
    except Exception:  # noqa: BLE001 - telemetry is optional
        return {}


def _positive(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def slot_snapshot(manager: Any) -> list[dict[str, Any]]:
    """One row per role: the resident model, its device, estimate and lifecycle."""

    rows: list[dict[str, Any]] = []
    for role in ROLES:
        model_id = resident_for_role(manager, role)
        pending = loading_models(manager, role)
        row: dict[str, Any] = {
            "role": role,
            "label": ROLE_LABELS[role],
            "model": model_id,
            "name": _name(manager, model_id) if model_id else None,
            "device": None,
            "status": "empty",
            "busy": False,
            "can_unload": False,
            "memory_estimate_gib": None,
            "loading_model": pending[0] if pending else None,
        }
        if model_id is not None:
            device = manager.devices.get(model_id) or getattr(
                manager.engines.get(model_id), "device", None
            )
            lock = manager.locks.get(model_id)
            busy = bool(lock is not None and lock.locked())
            switching = _active(getattr(manager, "load_tasks", {}).get(model_id))
            row.update(
                device=device,
                status="switching" if switching else "loaded",
                busy=busy,
                can_unload=not busy and not switching,
                memory_estimate_gib=_estimate_gib(manager, model_id, device),
            )
        elif pending:
            row["status"] = "loading"
        rows.append(row)
    return rows


def memory_plan(manager: Any, model_id: str, device: str | None) -> dict[str, Any]:
    """Estimate whether ``model_id`` can join the other resident slots on ``device``.

    Host RAM ``available_gb`` already excludes memory held by resident engines, so the
    decision compares only the *new* footprint (less what a replaced same-role model
    frees) against current headroom. The combined total is reported for transparency.
    """

    role = model_role(manager, model_id)
    target = device or getattr(manager.catalog.get(model_id), "recommended_device", None)
    replaced = [peer for peer in resident_models(manager, role) if peer != model_id]
    others = [peer for peer in list(manager.engines) if peer != model_id and peer not in replaced]
    estimate = _estimate_gib(manager, model_id, target)
    residents = [
        {
            "model": peer,
            "role": model_role(manager, peer),
            "device": manager.devices.get(peer),
            "memory_estimate_gib": _estimate_gib(manager, peer, manager.devices.get(peer)),
        }
        for peer in others
    ]
    # A device switch of the resident model replaces its own engine, so its current
    # footprint is freed too (the old engine only serves until the new one compiles).
    freed_ids = replaced + ([model_id] if model_id in manager.engines else [])
    freed = sum(
        _estimate_gib(manager, peer, manager.devices.get(peer)) or 0.0 for peer in freed_ids
    )
    combined = round((estimate or 0.0) + sum(r["memory_estimate_gib"] or 0.0 for r in residents), 2)

    snapshot = _snapshot(manager)
    available = _positive((snapshot.get("memory") or {}).get("available_gb"))
    reserve = max(_MIN_RESERVE_GIB, available * _RESERVE_FRACTION) if available else None
    plan: dict[str, Any] = {
        "model": model_id,
        "role": role,
        "device": target,
        "replaces": replaced[0] if replaced else None,
        "memory_estimate_gib": estimate,
        "resident": residents,
        "combined_estimate_gib": combined,
        "available_ram_gib": round(available, 2) if available else None,
        "reserve_gib": round(reserve, 2) if reserve else None,
        "freed_by_replacement_gib": round(freed, 2),
        "verdict": "ok",
        "message": None,
        "estimate_source": "advisor-estimate",
    }

    if not residents:
        # Single resident model: unchanged behavior (the advisor preflight still
        # reports fit warnings on the model card before the user loads it).
        return plan

    npu_peer = next((r for r in residents if _uses_npu(r["device"])), None)
    if _uses_npu(target) and npu_peer is not None:
        plan["verdict"] = "warning" if _simulated(manager) else "refuse"
        plan["npu_conflict"] = npu_peer["model"]
        plan["message"] = (
            f"{_name(manager, npu_peer['model'])} already uses the NPU. Running two models on "
            "the Intel NPU at once is not verified, so load this one on CPU or GPU instead "
            "(or unload the other model first)."
        )
        return plan

    if estimate is None or available is None:
        plan["verdict"] = "unknown"
        plan["message"] = (
            "Combined memory could not be estimated on this PC; watch memory use after loading."
        )
        return plan

    headroom = available + freed
    if estimate > headroom:
        plan["verdict"] = "refuse"
        plan["message"] = (
            f"Not enough memory to keep these models loaded together: {_name(manager, model_id)} "
            f"needs about {estimate:.1f} GB and only {headroom:.1f} GB is free. Unload another "
            "model, choose a smaller model or format, or close other applications."
        )
    elif estimate + (reserve or 0.0) > headroom:
        plan["verdict"] = "warning"
        plan["message"] = (
            f"Memory will be tight: about {estimate:.1f} GB more is needed with "
            f"{headroom:.1f} GB free. The PC may slow down while these models stay loaded."
        )

    base = str(target or "").split(".", 1)[0].split(":", 1)[0].upper()
    if plan["verdict"] in {"ok", "warning"} and base == "GPU":
        gpu = snapshot.get("gpu") if isinstance(snapshot.get("gpu"), dict) else {}
        gpu_total = _positive(gpu.get("total_memory_gb") or gpu.get("total_gb"))
        on_gpu = sum(
            r["memory_estimate_gib"] or 0.0
            for r in residents
            if str(r["device"] or "").upper().startswith("GPU")
        )
        if gpu_total and estimate + on_gpu > gpu_total:
            plan["verdict"] = "warning"
            plan["message"] = (
                f"Models on the GPU need about {estimate + on_gpu:.1f} GB with {gpu_total:.1f} GB "
                "of reported GPU memory; shared system memory may be used and slow inference."
            )
    return plan


def preflight_slot_load(
    manager: Any, model_id: str, device: str | None, *, allow_resource_risk: bool = False
) -> dict[str, Any]:
    """Return the memory plan, raising :class:`SlotResourceError` on a refusal."""

    plan = memory_plan(manager, model_id, device)
    if plan["verdict"] == "refuse":
        if not allow_resource_risk:
            raise SlotResourceError(plan["message"], plan)
        plan["overridden"] = True
    return plan


def _evict_same_role_peers(manager: Any, model_id: str) -> None:
    role = model_role(manager, model_id)
    label = ROLE_LABELS[role].lower()
    for peer in resident_models(manager, role):
        if peer == model_id:
            continue
        if peer in getattr(manager, "_context_changes", set()):
            raise SlotConflictError(
                f"{_name(manager, peer)} is applying a context change. Try again when it finishes."
            )
        try:
            manager.unload(peer)
        except ValueError as exc:  # ModelBusyError: serving, queued or switching
            raise SlotConflictError(
                f"{_name(manager, peer)} is busy in the {label} slot. Wait for its request to "
                f"finish (or stop it) before loading another {label} model."
            ) from exc
        manager.emit_event(
            "info",
            f"Replaced {_name(manager, peer)} with {_name(manager, model_id)} in the {label} slot",
        )


async def _enforce_after_load(manager: Any, model_id: str) -> None:
    """After a queued load installs its engine, release older same-role residents.

    Two loads of one role can be queued back to back (the newest request wins). The
    schedule-time eviction already freed whatever was resident then; this covers a model
    that became resident while the newer load was waiting for the shared load lock.
    """

    for _ in range(_ENFORCE_MAX_ATTEMPTS):
        if model_id not in manager.engines or getattr(
            manager, "_model_manager_shutting_down", False
        ):
            return
        try:
            _evict_same_role_peers(manager, model_id)
            return
        except SlotConflictError:
            await asyncio.sleep(_ENFORCE_RETRY_SECONDS)
    manager.emit_event(
        "warning", f"Could not release the previous {model_role(manager, model_id)} model yet"
    )


def install_model_slots() -> None:
    """Enforce one resident model per role on ``ModelManager.schedule_load``."""

    from app import model_manager as manager_module

    manager_class = manager_module.ModelManager
    if getattr(manager_class, _INSTALL_FLAG, False):
        return
    original_schedule_load = manager_class.schedule_load

    def schedule_load_into_slot(
        self,
        model_id: str,
        device: str | None = None,
        *,
        draft_model: str | None = None,
    ) -> asyncio.Task | None:
        conversion = getattr(self, "convert_tasks", {}).get(model_id)
        if model_id in self.catalog and not _active(conversion):
            # Validate everything that can fail synchronously before the resident
            # same-role model is released, so a bad request never empties a slot.
            self._resolve_draft_model_path(model_id, draft_model)
            _evict_same_role_peers(self, model_id)
        # While converting, the deferred load re-enters this scheduler afterwards and
        # replaces the slot then, so the current model stays usable during conversion.
        task = original_schedule_load(self, model_id, device, draft_model=draft_model)
        if task is not None and model_id in self.catalog and not _active(conversion):
            tracked = getattr(self, "_slot_enforcers", None)
            if tracked is None:
                tracked = self._slot_enforcers = set()

            def after_load(done: asyncio.Task) -> None:
                if done.cancelled() or model_id not in self.engines:
                    return
                enforcer = asyncio.get_running_loop().create_task(
                    _enforce_after_load(self, model_id)
                )
                tracked.add(enforcer)
                enforcer.add_done_callback(tracked.discard)

            task.add_done_callback(after_load)
        return task

    original_shutdown = manager_class.shutdown

    async def shutdown_with_slot_enforcers(self) -> None:
        for enforcer in list(getattr(self, "_slot_enforcers", ())):
            enforcer.cancel()
        await original_shutdown(self)

    manager_class.schedule_load = schedule_load_into_slot
    manager_class.shutdown = shutdown_with_slot_enforcers
    manager_class.slot_snapshot = slot_snapshot
    manager_class.memory_plan = memory_plan
    setattr(manager_class, _INSTALL_FLAG, True)


__all__ = [
    "ROLE_CHAT",
    "SlotConflictError",
    "SlotResourceError",
    "install_model_slots",
    "loading_models",
    "memory_plan",
    "model_role",
    "preflight_slot_load",
    "resident_for_role",
    "resident_models",
    "slot_snapshot",
]
