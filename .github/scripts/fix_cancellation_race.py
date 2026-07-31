from pathlib import Path

path = Path("app/model_cancellation.py")
text = path.read_text(encoding="utf-8")
old = """            latest_operation_id, latest_phase, _ = _progress_identity(self, model_id)
            if latest_operation_id == operation_id and latest_phase != \"cancelled\":
                cfg = self.catalog[model_id]
                self._set_status(model_id, \"cancelled\")
                self._set_progress(
                    model_id,
                    \"cancelled\",
                    f\"Model preparation cancelled for {cfg.name}.\",
                )

            final_operation_id, final_phase, _ = _progress_identity(self, model_id)
"""
new = """            latest_operation_id, latest_phase, _ = _progress_identity(self, model_id)
            if latest_operation_id == operation_id and latest_phase in {\"ready\", \"error\"}:
                raise CancellationConflict(
                    \"task_finished\",
                    \"The model preparation task finished before cancellation completed.\",
                    current_operation_id=latest_operation_id,
                )
            if latest_operation_id == operation_id and latest_phase != \"cancelled\":
                cfg = self.catalog[model_id]
                self._set_status(model_id, \"cancelled\")
                self._set_progress(
                    model_id,
                    \"cancelled\",
                    f\"Model preparation cancelled for {cfg.name}.\",
                )

            final_operation_id, final_phase, _ = _progress_identity(self, model_id)
"""
if text.count(old) != 1:
    raise SystemExit(f"Expected one cancellation race block, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
