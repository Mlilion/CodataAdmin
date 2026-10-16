from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import SemDomainModel
from .schema import DomainCreateSchema, DomainUpdateSchema


class DomainCRUD(CRUDBase[SemDomainModel, DomainCreateSchema, DomainUpdateSchema]):
    """语义业务域 CRUD"""

    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=SemDomainModel, auth=auth)
