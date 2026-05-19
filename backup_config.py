import os
import json

# =========================================================
#                    GLOBAL CONFIGURATION
# =========================================================

# The Firebase Project ID. If empty or None, it will be automatically parsed from the SERVICE_ACCOUNT_FILE
PROJECT_ID = "globalinternational-f42034"

# Path to the Firebase service account JSON key file
SERVICE_ACCOUNT_FILE = "globalinternational-f42034-ffd0ed4ddd.json"

# Optional: Override the storage bucket name. If None, it will default to <project_id>.appspot.com
STORAGE_BUCKET_OVERRIDE = "globalinternational-f42034.firebasestorage.app"


# =========================================================
#                 PATH & FOLDER GENERATOR
# =========================================================

def get_resolved_project_id():
    """Returns the PROJECT_ID if set, otherwise extracts it from the service account file."""
    if PROJECT_ID:
        return PROJECT_ID
    return get_project_id_from_service_account()


def get_backup_paths(project_id=None):
    """Returns a dictionary of all backup directory paths using project ID as the parent folder."""
    if project_id is None:
        project_id = get_resolved_project_id() or "unknown_project"
    
    base_dir = os.path.abspath(project_id)
    return {
        "root": base_dir,
        "firestore_backup": os.path.join(base_dir, "firestore_backup"),
        "storage_backup": os.path.join(base_dir, "storage_backup"),
        "function_backup": os.path.join(base_dir, "function_backup"),
        "key": os.path.join(base_dir, "key"),
        "realtime_db_backup": os.path.join(base_dir, "realtime_db_backup"),
        "hosting_image_backup": os.path.join(base_dir, "hosting_image_backup"),
    }


def init_folders(project_id=None):
    """Creates all backup folders if they don't exist."""
    paths = get_backup_paths(project_id)
    for name, path in paths.items():
        os.makedirs(path, exist_ok=True)
    return paths


def get_project_id_from_service_account():
    """Extracts project_id from the service account JSON file if it exists."""
    if os.path.exists(SERVICE_ACCOUNT_FILE):
        try:
            with open(SERVICE_ACCOUNT_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("project_id", "")
        except Exception:
            pass
    return ""


def get_storage_bucket():
    """Returns the storage bucket name."""
    if STORAGE_BUCKET_OVERRIDE:
        return STORAGE_BUCKET_OVERRIDE
    proj_id = get_resolved_project_id()
    if proj_id:
        return f"{proj_id}.appspot.com"
    return ""
