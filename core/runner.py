from __future__ import annotations


class RunPipeline:
    """
    Thin facade for run-only execution.
    Build-heavy flows are intentionally disabled in this path.
    """

    def __init__(self, factory):
        self.factory = factory

    def run(self, task_input: str, role_spec: str = "General") -> dict:
        return self.factory.run(task_input=task_input, role_spec=role_spec, enable_build=False)
