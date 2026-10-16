from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import (
    SemModelEntityModel,
    SemModelGrantModel,
    SemModelModel,
    SemModelReleaseModel,
)


class ModelCRUD(CRUDBase[SemModelModel, dict, dict]):
    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=SemModelModel, auth=auth)


class ModelEntityCRUD(CRUDBase[SemModelEntityModel, dict, dict]):
    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=SemModelEntityModel, auth=auth)


class ModelReleaseCRUD(CRUDBase[SemModelReleaseModel, dict, dict]):
    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=SemModelReleaseModel, auth=auth)


class ModelGrantCRUD(CRUDBase[SemModelGrantModel, dict, dict]):
    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=SemModelGrantModel, auth=auth)
