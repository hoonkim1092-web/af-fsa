from core.hooks.base import ContinuationHook, ToolCallDecision
from core.hooks.event_bus import HookEventBus


class _NormalizeArgsHook(ContinuationHook):
    PRIORITY = 20

    def __init__(self, trace):
        self.trace = trace

    def pre_tool_call(self, agent_state, tool_name, tool_args):
        self.trace.append(("normalize", tool_name, dict(tool_args)))
        mutated = dict(tool_args)
        mutated["normalized"] = True
        return ToolCallDecision(allowed=True, tool_args=mutated)


class _ReadonlyWorkspaceHook(ContinuationHook):
    PRIORITY = 30

    def __init__(self, trace):
        self.trace = trace

    def pre_tool_call(self, agent_state, tool_name, tool_args):
        self.trace.append(("guard", tool_name, dict(tool_args)))
        path = str(tool_args.get("path") or "")
        workspace = str(agent_state.get("workspace") or "")
        if workspace and path and not path.startswith(workspace):
            return ToolCallDecision(
                allowed=False,
                tool_args=dict(tool_args),
                reason="path_outside_workspace",
            )
        return ToolCallDecision(allowed=True, tool_args=dict(tool_args))


class _TruncateToolResultHook(ContinuationHook):
    PRIORITY = 10

    def post_tool_call(self, agent_state, tool_name, result):
        if isinstance(result, dict) and "stdout" in result:
            result = dict(result)
            result["stdout"] = str(result["stdout"])[:5]
        return result


def test_pre_tool_call_hooks_can_mutate_and_block():
    trace = []
    bus = HookEventBus()
    bus.register(_NormalizeArgsHook(trace))
    bus.register(_ReadonlyWorkspaceHook(trace))

    decision = bus.run_pre_tool_call(
        {"workspace": "C:/repo/workspace"},
        "write_file",
        {"path": "D:/elsewhere/file.txt"},
    )

    assert decision.allowed is False
    assert decision.reason == "path_outside_workspace"
    assert decision.tool_args["normalized"] is True
    assert [item[0] for item in trace] == ["normalize", "guard"]


def test_post_tool_call_hooks_can_transform_result():
    bus = HookEventBus()
    bus.register(_TruncateToolResultHook())

    result = bus.run_post_tool_call({}, "shell", {"stdout": "abcdefgh"})

    assert result["stdout"] == "abcde"

