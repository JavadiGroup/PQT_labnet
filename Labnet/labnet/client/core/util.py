import os
from dotenv import load_dotenv

from .lab_client import LabClient

from .exceptions import (
    AuthenticationError,
    ServerConnectionError
)


def retrieve_client_info() -> tuple:
    load_dotenv()

    url = os.getenv(
        "LABNET_SERVER_URL",
        "http://127.0.0.1:8008",
    )

    api_key = os.getenv("API_KEY")
    
    return url, api_key

def setup_client(url: str, api_key: str) -> LabClient:
    try:
        client = LabClient(server_url=url, api_key=api_key)
        print("Connection successful")
        print(f"Authenticated user: {client.current_user.user}")
        print(f"Role: {client.current_user.role}")

    except AuthenticationError as exc:
        print(f"Authentication failed: {exc}")

    except ServerConnectionError as exc:
        print(f"Connection failed: {exc}")

    return client
