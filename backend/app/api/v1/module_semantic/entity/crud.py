from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import SemEntityModel
from .schema import EntityCreateSchema, EntityUpdateSchema


class EntityCRUD(CRUDBase[SemEntityModel, EntityCreateSchema, EntityUpdateSchema]):
    """语义业务实体 CRUD"""

    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=SemEntityModel, auth=auth)
