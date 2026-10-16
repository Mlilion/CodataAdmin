from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import SemDimensionModel
from .schema import DimensionCreateSchema, DimensionUpdateSchema


class DimensionCRUD(CRUDBase[SemDimensionModel, DimensionCreateSchema, DimensionUpdateSchema]):
    """语义维度 CRUD"""

    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=SemDimensionModel, auth=auth)
