import pytest
from sqlalchemy import select

from app.api.v1.module_semantic.indicator.model import SemIndicatorImplModel, SemIndicatorModel
from app.api.v1.module_semantic.model.compatibility_audit import audit_snapshot_compatibility
from app.core.exceptions import CustomException


def test_redshift_compatibility_audit_flags_unsupported_functions():
    snapshot = {
        "indicators": [
            {
                "code": "demo",
                "impls": [
                    {
                        "role": "primary",
                        "sql_text": "SELECT UNIX_TIMESTAMP(ts) FROM t",
                    }
                ],
            }
        ]
    }

    problems = audit_snapshot_compatibility(snapshot, "redshift")

    assert problems and "unix_timestamp" in problems[0].lower()
    assert audit_snapshot_compatibility(snapshot, "starrocks") == []


@pytest.mark.asyncio
async def test_publish_rejects_redshift_incompatible_impl(seed_model_knowledge_all_targets, monkeypatch):
    from app.api.v1.module_semantic.model.service import ModelService
    from app.config.setting import settings

    svc, model_id, _ = seed_model_knowledge_all_targets
    monkeypatch.setattr(settings, "SEM_QUERY_DIALECT", "redshift")

    impl = (
        await svc.auth.db.execute(
            select(SemIndicatorImplModel)
            .join(SemIndicatorModel, SemIndicatorModel.id == SemIndicatorImplModel.indicator_id)
            .where(
                SemIndicatorModel.code == "kn_revenue",
                SemIndicatorImplModel.role == "primary",
            )
        )
    ).scalar_one()
    impl.sql_text = "SELECT UNIX_TIMESTAMP(final_revenue) FROM db_kn.t_kn_order"
    await svc.auth.db.flush()

    with pytest.raises(CustomException) as excinfo:
        await ModelService(svc.auth).publish(model_id)

    assert "不兼容当前目标方言" in str(excinfo.value)
