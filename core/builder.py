from __future__ import annotations


class BuildPipeline:
    """
    Thin facade for build-only execution.
    This keeps build concerns separate from runtime-only execution paths.
    """

    def __init__(self, factory):
        self.factory = factory

    def run(self, task_input: str, role_spec: str = "General") -> dict:
        return self.factory.run(task_input=task_input, role_spec=role_spec, enable_build=True)
