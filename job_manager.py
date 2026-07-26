"""
Minimal thread-based job manager for long-running pipeline stages
(footage matching, rendering). Good enough for a single-user local
tool — no Redis/Celery needed.

Each job is a dict: {status, progress, message, result, error}
status is one of: pending | running | done | error
"""
import threading
import uuid
import traceback


class JobManager:
    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def create(self) -> str:
        job_id = str(uuid.uuid4())
        with self._lock:
            self._jobs[job_id] = {
                "status": "pending", "progress": 0, "message": "",
                "result": None, "error": None,
            }
        return job_id

    def update(self, job_id: str, **fields):
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(fields)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def run_async(self, job_id: str, fn, *args, **kwargs):
        """Runs fn(job_id, *args, **kwargs) in a background thread.
        fn is expected to call job_manager.update(job_id, progress=..., message=...)
        as it works, and return a JSON-serializable result at the end."""

        def _target():
            self.update(job_id, status="running", message="Starting…")
            try:
                result = fn(job_id, *args, **kwargs)
                self.update(job_id, status="done", progress=100, message="Selesai", result=result)
            except Exception as e:
                traceback.print_exc()
                self.update(job_id, status="error", error=str(e))

        threading.Thread(target=_target, daemon=True).start()


job_manager = JobManager()
