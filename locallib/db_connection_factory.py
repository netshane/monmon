from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


class DbConnectionFactory:
    def __init__(
        self,
        connections: dict,
        passwords: dict,
    ):
        self.connections = connections.copy()
        self.passwords = passwords.copy()

    def add_connections(self, new_connections: dict):
        self.connections.update(new_connections)

    def connections_snapshot(self) -> dict:
        return self.connections.copy()

    def restore_connections(self, snapshot: dict):
        self.connections = dict(snapshot)

    def add_passwords(self, new_passwords: dict):
        self.passwords.update(new_passwords)

    @staticmethod
    def __create_db_engine(
        conn_str: str,
        isolation_level: str,
    ) -> Engine:
        def mssql_creator(conn_str: str):
            import mssql_python

            return mssql_python.connect(conn_str)

        if conn_str.startswith("mssql+mssql-python://"):
            # Remove the prefix for the creator function
            conn_str = conn_str[len("mssql+mssql-python://") :]
            return create_engine(
                "mssql://",
                creator=lambda: mssql_creator(conn_str),
                isolation_level=isolation_level,
            )
        else:
            return create_engine(
                conn_str,
                isolation_level=isolation_level,
            )

    def create(
        self,
        connection: str,
    ) -> Engine:
        conn_str = self.connections.get(connection)
        if not conn_str:
            raise ValueError(f"No connection string found for '{connection}'")

        passwd = self.passwords.get(connection)
        if passwd:
            conn_str = conn_str.format(passwd=passwd)

        return DbConnectionFactory.__create_db_engine(
            conn_str,
            isolation_level="AUTOCOMMIT",
        )
