from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import ParamsModel
from .schema import ParamsCreateSchema, ParamsUpdateSchema


class ParamsCRUD(CRUDBase[ParamsModel, ParamsCreateSchema, ParamsUpdateSchema]):
    """配置管理数据层"""

    def __init__(self, auth: AuthSchema) -> None:
        """
        初始化系统参数配置数据层。

        参数:
        - auth (AuthSchema): 认证信息模型（含 DB 会话等上下文）。

        返回:
        - None
        """
        super().__init__(model=ParamsModel, auth=auth)
