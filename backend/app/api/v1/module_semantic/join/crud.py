from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import SemJoinModel
from .schema import JoinCreateSchema, JoinUpdateSchema


class JoinCRUD(CRUDBase[SemJoinModel, JoinCreateSchema, JoinUpdateSchema]):
    """语义 JOIN 关系 CRUD"""

    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=SemJoinModel, auth=auth)
