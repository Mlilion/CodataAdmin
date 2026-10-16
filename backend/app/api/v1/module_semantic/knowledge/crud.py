from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import SemKnowledgeModel
from .schema import KnowledgeCreateSchema, KnowledgeUpdateSchema


class KnowledgeCRUD(CRUDBase[SemKnowledgeModel, KnowledgeCreateSchema, KnowledgeUpdateSchema]):
    """语义知识 CRUD"""

    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=SemKnowledgeModel, auth=auth)
