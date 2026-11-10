"""从待验证流程生成控制用例的前置失败报告。"""

from .pytest_plugin import fail_e2e_prerequisite


def report_blocked_control_case(request, *, required):
    flow = request.node.get_closest_marker("case_flow")
    fail_e2e_prerequisite(
        request,
        required=required,
        missing=f"当前流程预先标记为阻塞，未启动现场验证。{flow.kwargs['blocked']}",
        checks=flow.args,
    )
