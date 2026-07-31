from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"Expected exactly one match in {path}, found {text.count(old)}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "app/progress_reliability.py",
    """    const modelState = new Map();
    const optimistic = new Map();
""",
    """    const modelState = new Map();
    const acceptedServerModels = new Map();
    const optimistic = new Map();
""",
)

replace_once(
    "app/progress_reliability.py",
    """        const reportedStart = Number(progress.started_at) || 0;
        const previous = modelState.get(model.id);
        const newOperation = !previous
            || (reportedStart > 0 && previous.startedAt > 0 && reportedStart !== previous.startedAt)
            || (previous.terminal && !['ready', 'error', 'cancelled'].includes(phase));
        const prior = newOperation ? {
            overall: 0,
            rank: -1,
            startedAt: reportedStart || Math.floor(Date.now() / 1000),
            targetDevice: null,
            terminal: false,
        } : previous;
""",
    """        const reportedStart = Number(progress.started_at) || 0;
        const operationId = String(progress.operation_id || '');
        const operationType = String(progress.operation_type || '');
        const serverRevision = Number.isInteger(progress.revision) && progress.revision >= 0
            ? progress.revision
            : 0;
        const previous = modelState.get(model.id);
        const operationChanged = !!(
            operationId && previous?.operationId && operationId !== previous.operationId
        );
        const newOperation = !previous
            || operationChanged
            || (!operationId && reportedStart > 0 && previous.startedAt > 0
                && reportedStart !== previous.startedAt)
            || (previous.terminal && !['ready', 'error', 'cancelled'].includes(phase));
        const prior = newOperation ? {
            overall: 0,
            rank: -1,
            startedAt: reportedStart || Math.floor(Date.now() / 1000),
            targetDevice: null,
            operationId,
            operationType,
            serverRevision,
            terminal: false,
        } : previous;
""",
)

replace_once(
    "app/progress_reliability.py",
    """        modelState.set(model.id, {
            overall,
            rank: Math.max(prior.rank, meta.stage),
            startedAt,
            targetDevice,
            terminal: ['ready', 'error', 'cancelled'].includes(phase),
        });

        return {
            model,
            progress,
            phase,
            meta,
            raw,
            overall,
            determinate,
            targetDevice,
            elapsed: Math.max(0, now - startedAt),
            staleFor: Math.max(0, now - updatedAt),
        };
""",
    """        modelState.set(model.id, {
            overall,
            rank: Math.max(prior.rank, meta.stage),
            startedAt,
            targetDevice,
            operationId: operationId || prior.operationId || '',
            operationType: operationType || prior.operationType || '',
            serverRevision: Math.max(prior.serverRevision || 0, serverRevision),
            terminal: ['ready', 'error', 'cancelled'].includes(phase),
        });

        return {
            model,
            progress,
            phase,
            meta,
            raw,
            overall,
            determinate,
            targetDevice,
            operationId: operationId || prior.operationId || '',
            operationType: operationType || prior.operationType || '',
            serverRevision: Math.max(prior.serverRevision || 0, serverRevision),
            elapsed: Math.max(0, now - startedAt),
            staleFor: Math.max(0, now - updatedAt),
        };
""",
)

replace_once(
    "app/progress_reliability.py",
    """    function buildDetail(info, operationCount) {
""",
    """    function shortOperationId(operationId) {
        const value = String(operationId || '');
        if (!value) return '';
        const prefix = value.startsWith('convert-')
            ? 'convert'
            : (value.startsWith('load-') ? 'load' : 'operation');
        return `${prefix} ${value.slice(-8)}`;
    }

    function buildDetail(info, operationCount) {
""",
)

replace_once(
    "app/progress_reliability.py",
    """            info.targetDevice ? `Device ${info.targetDevice}` : null,
            operationCount > 1 ? `${operationCount} model operations active` : null,
""",
    """            info.targetDevice ? `Device ${info.targetDevice}` : null,
            info.operationId
                ? `Operation ${shortOperationId(info.operationId)} · update ${info.serverRevision}`
                : null,
            operationCount > 1 ? `${operationCount} model operations active` : null,
""",
)

replace_once(
    "app/progress_reliability.py",
    """    function announce(info) {
        const text = `${baseName(info.model)}: ${info.meta.label}`;
        if (text === lastAnnouncement || !liveRegion) return;
        lastAnnouncement = text;
        liveRegion.textContent = text;
    }
""",
    """    function announce(info) {
        const text = `${baseName(info.model)}: ${info.meta.label}`;
        const key = `${info.operationId || info.model.id}:${info.meta.label}`;
        if (key === lastAnnouncement || !liveRegion) return;
        lastAnnouncement = key;
        liveRegion.textContent = text;
    }
""",
)

replace_once(
    "app/progress_reliability.py",
    """        if (!displayable) {
            dock.classList.remove('visible', 'error', 'cancelled', 'terminal');
            lastAnnouncement = '';
            return null;
        }
        const info = progressInfo(model);
""",
    """        if (!displayable) {
            dock.classList.remove('visible', 'error', 'cancelled', 'terminal');
            delete dock.dataset.operationId;
            delete dock.dataset.operationRevision;
            lastAnnouncement = '';
            return null;
        }
        const info = progressInfo(model);
        dock.dataset.operationId = info.operationId;
        dock.dataset.operationRevision = String(info.serverRevision);
""",
)

replace_once(
    "app/progress_reliability.py",
    """    function mergeOptimistic(source) {
""",
    """    function reconcileServerModels(source) {
        return source.map(model => {
            const progress = model?.progress || {};
            const operationId = String(progress.operation_id || '');
            const revision = Number.isInteger(progress.revision) && progress.revision >= 0
                ? progress.revision
                : 0;
            const previous = acceptedServerModels.get(model.id);
            if (
                previous
                && operationId
                && previous.operationId === operationId
                && revision < previous.revision
            ) {
                return previous.model;
            }
            acceptedServerModels.set(model.id, { operationId, revision, model });
            return model;
        });
    }

    function mergeOptimistic(source) {
""",
)

replace_once(
    "app/progress_reliability.py",
    """        const models = mergeOptimistic(source);
""",
    """        const models = mergeOptimistic(reconcileServerModels(source));
""",
)

replace_once(
    "app/progress_reliability.py",
    """        for (const modelId of modelState.keys()) {
            if (!retained.has(modelId)) modelState.delete(modelId);
        }
""",
    """        for (const modelId of modelState.keys()) {
            if (!retained.has(modelId)) modelState.delete(modelId);
        }
        for (const modelId of acceptedServerModels.keys()) {
            if (!retained.has(modelId)) acceptedServerModels.delete(modelId);
        }
""",
)

replace_once(
    "app/progress_reliability.py",
    """        const model = {
            ...base,
""",
    """        const optimisticOperationId = `optimistic:${modelId}:${Date.now().toString(36)}`;
        const model = {
            ...base,
""",
)

replace_once(
    "app/progress_reliability.py",
    """            progress: {
                phase: 'queued',
                message,
                percent: null,
                started_at: now,
                updated_at: now,
                log_tail: [],
            },
""",
    """            progress: {
                schema_version: 1,
                operation_id: optimisticOperationId,
                operation_type: converting ? 'convert' : 'load',
                revision: 1,
                phase: 'queued',
                message,
                percent: null,
                completed: null,
                total: null,
                started_at: now,
                updated_at: now,
                log_tail: [],
            },
""",
)

replace_once(
    "tests/test_model_converter.py",
    """    def fake_streaming_command(cmd):
        captured[\"cmd\"] = cmd
""",
    """    def fake_streaming_command(cmd, **kwargs):
        captured[\"cmd\"] = cmd
        captured[\"progress_emitter\"] = kwargs.get(\"progress_emitter\")
""",
)

replace_once(
    "tests/test_model_converter.py",
    """    assert \"int8\" in captured[\"cmd\"]
    console = capsys.readouterr().out
""",
    """    assert \"int8\" in captured[\"cmd\"]
    assert captured[\"progress_emitter\"] is not None
    console = capsys.readouterr().out
""",
)

replace_once(
    "tests/test_server_mock.py",
    """    assert set(progress) == {\"phase\", \"message\", \"percent\", \"started_at\", \"updated_at\", \"log_tail\"}
""",
    """    assert set(progress) == {
        \"schema_version\",
        \"operation_id\",
        \"operation_type\",
        \"revision\",
        \"phase\",
        \"message\",
        \"percent\",
        \"completed\",
        \"total\",
        \"started_at\",
        \"updated_at\",
        \"log_tail\",
    }
""",
)

replace_once(
    "tests/test_server_mock.py",
    """    assert isinstance(progress[\"message\"], str)
    assert isinstance(progress[\"log_tail\"], list)
""",
    """    assert isinstance(progress[\"message\"], str)
    assert isinstance(progress[\"revision\"], int)
    assert isinstance(progress[\"log_tail\"], list)
""",
)

replace_once(
    "tests/test_progress_reliability.py",
    """def test_progress_announcements_only_change_with_operation_phase() -> None:
""",
    """def test_server_operation_identity_drives_browser_reconciliation() -> None:
    script = PROGRESS_RELIABILITY_JS

    assert \"acceptedServerModels\" in script
    assert \"progress.operation_id\" in script
    assert \"progress.operation_type\" in script
    assert \"progress.revision\" in script
    assert \"reconcileServerModels\" in script
    assert \"revision < previous.revision\" in script
    assert \"dock.dataset.operationId\" in script
    assert \"Operation ${shortOperationId(info.operationId)}\" in script
    assert \"optimisticOperationId\" in script


def test_progress_announcements_only_change_with_operation_phase() -> None:
""",
)
