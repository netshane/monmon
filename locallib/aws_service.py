from requests_aws4auth import AWS4Auth
import boto3


class AwsService:
    def __init__(self, region: str):
        self.region = region

    def get_auth(self, service: str):
        credentials = boto3.Session().get_credentials()
        auth = AWS4Auth(
            credentials.access_key,
            credentials.secret_key,
            self.region,
            service,
            session_token=credentials.token,
        )

        return auth
