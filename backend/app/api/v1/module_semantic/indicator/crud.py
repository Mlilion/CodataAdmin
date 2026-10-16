from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import SemIndicatorImplModel, SemIndicatorModel
from .schema import (
    IndicatorCreateSchema,
    IndicatorImplCreateSchema,
    IndicatorUpdateSchema,
)


class IndicatorCRUD(CRUDBase[SemIndicatorModel, IndicatorCreateSchema, IndicatorUpdateSchema]):
    """语义指标 CRUD"""

    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=SemIndicatorModel, auth=auth)


class IndicatorImplCRUD(CRUDBase[SemIndicatorImplModel, IndicatorImplCreateSchema, IndicatorImplCreateSchema]):
    """语义指标口径 CRUD"""

    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=SemIndicatorImplModel, auth=auth)
