from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType[object]):
    cache_ok = True

    def __init__(self, dimensions: int) -> None:
        self.dimensions = dimensions

    def get_col_spec(self, **_kw: object) -> str:
        return f'vector({self.dimensions})'

    def bind_processor(self, dialect: object) -> object:
        def process(value: object) -> object:
            if value is None:
                return None
            if isinstance(value, list):
                return '[' + ','.join(map(str, value)) + ']'
            return value

        return process

    def result_processor(self, dialect: object, coltype: object) -> object:
        def process(value: object) -> object:
            if value is None:
                return None
            if isinstance(value, str):
                cleaned = value.strip('[]')
                if not cleaned:
                    return []
                return [float(x) for x in cleaned.split(',')]
            return value

        return process
