import os
import json
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import backup_config

# Initialize folders and get paths from central config
paths = backup_config.init_folders()
EXPORT_FOLDER = paths["firestore_backup"]

# Firebase init using central config
cred = credentials.Certificate(backup_config.SERVICE_ACCOUNT_FILE)
firebase_admin.initialize_app(cred)

db = firestore.client()


# Convert Firestore data
def convert_to_json_safe(data):
    if isinstance(data, dict):
        return {k: convert_to_json_safe(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [convert_to_json_safe(i) for i in data]
    elif isinstance(data, datetime):
        return data.isoformat()
    else:
        return data


# Export single collection
def export_collection(collection):
    collection_name = collection.id

    print(f"📂 Exporting: {collection_name}")

    docs = collection.stream()

    data_list = []

    for doc in docs:
        doc_data = doc.to_dict()
        doc_data["id"] = doc.id

        doc_data = convert_to_json_safe(doc_data)

        data_list.append(doc_data)

    file_path = os.path.join(
        EXPORT_FOLDER,
        f"{collection_name}.json"
    )

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data_list, f, indent=2, ensure_ascii=False)

    print(f"✅ Saved: {collection_name}")


# Get collections
collections = list(db.collections())

# Multi-thread export
with ThreadPoolExecutor(max_workers=10) as executor:
    executor.map(export_collection, collections)

print("🎉 ALL COLLECTIONS EXPORTED")
