from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType[object]):
    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **_kw: object) -> str:
        return f'vector({self.dimensions})'
