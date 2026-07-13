from app.services.UserManager import UserManager, UserNotFoundError
from app.storage.json_storage import JsonStorage


storage = JsonStorage(data_dir="data")
user_manager = UserManager(storage)


try:
    user_manager.get_user("robert")
except UserNotFoundError:
    result = user_manager.create_user(
        user="robert",
        role="admin",
    )
else:
    result = user_manager.rotate_api_key("robert")


print("User:", result["user"])
print("API key:", result["api_key"])
print()
print("Save this API key. The server stores only its hash.")