"""readiness 探针纳入检索副库 + 修 is_ok 类型比较 bug。

★ 发现的真 bug: health.py 的 `is_ok` 写成 `d.status in ("up", "disabled")`,
  而 DependencyStatus.status 是 int(0:异常 1:正常) —— int 与字符串比较恒为 False,
  导致 readiness **永远返回 503**。tests/test_main.py::test_check_readiness 与
  test_api_module_common.py::TestHealth::test_health_ready 一直失败, 此前被归因为
  「缺 Redis 环境依赖」, 实为类型比较错误。
"""

from app.api.v1.module_common.monitoring.schema import DependencyStatus


def test_is_ok_accepts_int_status():
    """is_ok 按 int 语义判定(1 正常 / 0 异常), 不是字符串比较。"""
    from app.api.v1.module_common.monitoring.health import _is_dependency_ok

    assert _is_dependency_ok(DependencyStatus(status=1)) is True
    assert _is_dependency_ok(DependencyStatus(status=0)) is False


def test_readiness_includes_retrieval_dependency(_api_client):
    """readiness 依赖列表含 semantic_retrieval 项。"""
    body = _api_client.get("/common/health/ready/").json()
    assert "semantic_retrieval" in body["data"]["dependencies"]


def test_readiness_ok_when_retrieval_disabled(_api_client):
    """检索副库关闭(默认)不拖垮 readiness —— 降级矩阵: 关闭态平台照常可用。"""
    body = _api_client.get("/common/health/ready/").json()
    assert body["data"]["dependencies"]["semantic_retrieval"]["status"] == 0
    # 关闭的可选依赖不应让整体判定为未就绪
    assert body["data"]["status"] == 1, f"关闭态被误判为未就绪: {body['data']['dependencies']}"
