from app.config import Settings  # noqa: F401 - installs composed browser extensions
from app.ui_extension import inject_multimodal_ui


def test_null_percentage_stays_indeterminate():
    rendered = inject_multimodal_ui("<html><body></body></html>")

    assert "value === null || value === undefined || value === ''" in rendered
    assert "raw !== null" in rendered
    assert "track.classList.toggle('indeterminate'" in rendered
    assert "track.removeAttribute('aria-valuenow')" in rendered


def test_download_percentage_prefers_aggregate_multi_file_progress():
    rendered = inject_multimodal_ui("<html><body></body></html>")

    assert "function aggregateDownloadPercent" in rendered
    assert "aggregateDownloadPercent(progress) ?? strictPercent(progress.percent)" in rendered


def test_overall_progress_is_monotonic_across_phases():
    rendered = inject_multimodal_ui("<html><body></body></html>")

    assert "overall: 0" in rendered
    # The floor combines the completed stage base with the current stage reading. Every
    # operand must be defined, or Math.max yields NaN for the whole run.
    assert "const metaStart = meta.stage >= 0 ? meta.stage * 33 : 0;" in rendered
    assert "let overall = Math.max(prior.overall ?? 0, metaStart, raw ?? 0);" in rendered
    assert "meta.start" not in rendered
    # The floor only carries across polls when it is persisted with the model state.
    assert "modelState.set(model.id" in rendered
    assert "terminal: ['ready', 'error', 'cancelled'].includes(phase),\n            overall," in (
        rendered
    )


def test_indeterminate_phases_show_real_stage_not_fake_zero_percent():
    rendered = inject_multimodal_ui("<html><body></body></html>")

    assert "Stage ${info.meta.stage + 1} of 3" in rendered
    assert "info.raw === null" in rendered
    assert "elapsed ${duration(info.elapsed)}" in rendered
    assert "Number(value)" in rendered
