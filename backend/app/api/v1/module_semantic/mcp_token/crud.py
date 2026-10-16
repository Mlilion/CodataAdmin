from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import SemMcpTokenModel


class McpTokenCRUD(CRUDBase[SemMcpTokenModel, dict, dict]):
    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=SemMcpTokenModel, auth=auth)
