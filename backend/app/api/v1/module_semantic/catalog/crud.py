from app.core.base_crud import CRUDBase
from app.core.base_schema import AuthSchema

from .model import CatDatabaseModel, CatDatasourceModel, CatTableModel


class DatasourceCRUD(CRUDBase[CatDatasourceModel, dict, dict]):
    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=CatDatasourceModel, auth=auth)


class DatabaseCRUD(CRUDBase[CatDatabaseModel, dict, dict]):
    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=CatDatabaseModel, auth=auth)


class TableCRUD(CRUDBase[CatTableModel, dict, dict]):
    def __init__(self, auth: AuthSchema) -> None:
        super().__init__(model=CatTableModel, auth=auth)
