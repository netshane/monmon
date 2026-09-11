from opensearchpy import OpenSearch, RequestsHttpConnection
from .aws_service import AwsService


class OpenSearchService:
    def __init__(self, aws_service: AwsService, host: str, default_index: str):
        self.aws_service = aws_service
        self.default_index = default_index
        self.client = None
        self.host = host

    def __get_client(self):
        if self.client is None:
            # Initialize OpenSearch client here using aws_service for authentication
            auth = self.aws_service.get_auth(service="es")

            client = OpenSearch(
                hosts=[{"host": self.host, "port": 443}],
                http_auth=auth,
                use_ssl=True,
                verify_certs=True,
                connection_class=RequestsHttpConnection,
            )

            self.client = client
        return self.client

    def query(self, query: str, index: str | None = None):
        if index is None:
            index = self.default_index

        client = self.__get_client()

        response = client.search(
            body=query,
            index=index,
        )

        return response


class OpenSearchServiceFactory:
    def __init__(
        self,
        aws_service: AwsService,
        connections: dict,
    ):
        self.aws_service = aws_service
        self.connections = connections.copy()

    def add_connections(self, new_connections: dict):
        self.connections.update(new_connections)

    def connections_snapshot(self) -> dict:
        return self.connections.copy()

    def restore_connections(self, snapshot: dict):
        self.connections = dict(snapshot)

    def create(
        self,
        connection: str,
    ) -> OpenSearchService:
        os_conn = self.connections.get(connection)
        host = os_conn.get("host") if isinstance(os_conn, dict) else None
        default_index = (
            os_conn.get("default_index") if isinstance(os_conn, dict) else None
        )

        if not host or not default_index:
            raise ValueError(f"No valid OpenSearch connection found for '{connection}'")

        return OpenSearchService(
            aws_service=self.aws_service,
            host=host,
            default_index=default_index,
        )
