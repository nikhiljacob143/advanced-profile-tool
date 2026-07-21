# -*- coding: utf-8 -*-
"""Background task: run a list of export jobs with progress and cancel.

Copyright (C) 2026 Nikhil Jacob — GPL v2 or later.
"""
import time
import traceback

from qgis.core import QgsTask


class ExportTask(QgsTask):
    """Executes (name, callable) export jobs sequentially off-thread.

    Each callable takes no arguments (bind everything with functools.partial
    on the main thread) and returns the written path. GUI/QGIS-layer work
    (memory layers, print layouts) must NOT be exported through this task —
    run those on the main thread.

    Results: .results list[(name, path)], .failures list[(name, error)],
    .elapsed_s.
    """

    def __init__(self, description, jobs):
        super().__init__(description, QgsTask.Flag.CanCancel)
        self.jobs = list(jobs)
        self.results = []
        self.failures = []
        self.error = None
        self.elapsed_s = 0.0

    def run(self):
        t0 = time.time()
        n = len(self.jobs)
        for i, (name, fn) in enumerate(self.jobs):
            if self.isCanceled():
                self.error = "Cancelled by user."
                return False
            try:
                path = fn()
                self.results.append((name, path))
            except Exception:                          # noqa: BLE001
                self.failures.append(
                    (name, traceback.format_exc(limit=4)))
            self.setProgress(100.0 * (i + 1) / max(n, 1))
        self.elapsed_s = time.time() - t0
        if self.failures and not self.results:
            self.error = f"All {len(self.failures)} export job(s) failed."
            return False
        return True
