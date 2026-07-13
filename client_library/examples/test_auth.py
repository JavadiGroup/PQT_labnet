import os
from dotenv import load_dotenv
import httpx

from labnet import (
    AuthenticationError,
    LabClient,
    ServerConnectionError,
)

load_dotenv()

server_url = os.getenv(
    "LABNET_SERVER_URL",
    "http://127.0.0.1:8008",
)

api_key_real = os.getenv("LABNET_API_KEY")
api_key_fake = "hello world"


def real_user_connection(api_key, server_url):
    print("\nTesting real user connection...")
    try:
        with LabClient(
            server_url=server_url,
            api_key=api_key,
        ) as client:
            print("Connection successful")
            print(f"Authenticated user: {client.current_user.user}")
            print(f"Role: {client.current_user.role}")

    except AuthenticationError as exc:
        print(f"Authentication failed: {exc}")

    except ServerConnectionError as exc:
        print(f"Connection failed: {exc}")


def fake_user_connection(api_key_fake, server_url):
    print("\nTesting fake user connection...")
    try:
        with LabClient(
            server_url=server_url,
            api_key=api_key_fake,
        ) as client:
            print("Connection successful")
            print(f"Authenticated user: {client.current_user.user}")
            print(f"Role: {client.current_user.role}")

    except AuthenticationError as exc:
        print(f"Authentication failed: {exc}")

    except ServerConnectionError as exc:
        print(f"Connection failed: {exc}")


def no_header_connection(server_url):
    print("\nTesting no header authentication...")
    client = httpx.Client(
        base_url=server_url,
        timeout=10,
        )
    
    try:
        response = client.request(
            method="GET",
            url="/auth/me",
            json=None
        )
    except httpx.RequestError as exc:
        raise ServerConnectionError(
            f"Could not connect to Central Server at "
            f"{server_url}: {exc}"
        ) from exc

    if response.is_success:
        raise ValueError("Request was sent with no header it should fail")
    
    print("No header request failed (expected behavior)")
    
if __name__ == "__main__" :
    real_user_connection(api_key_real, server_url)
    fake_user_connection(api_key_fake, server_url)
    no_header_connection(server_url)
