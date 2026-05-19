import os
import firebase_admin
from firebase_admin import credentials, storage
from concurrent.futures import ThreadPoolExecutor, as_completed
import backup_config

# Initialize folders and get paths from central config
paths = backup_config.init_folders()
LOCAL_DOWNLOAD_FOLDER = paths["storage_backup"]

# ==============================
# FIREBASE CONFIG
# ==============================

cred = credentials.Certificate(backup_config.SERVICE_ACCOUNT_FILE)

firebase_admin.initialize_app(cred, {
    'storageBucket': backup_config.get_storage_bucket()
})

bucket = storage.bucket()

# ==============================
# SETTINGS
# ==============================

# Increase for more speed
MAX_WORKERS = 32

# ==============================
# DOWNLOAD SINGLE FILE
# ==============================

def download_blob(blob):

    try:
        # Skip folders
        if blob.name.endswith("/"):
            return ("skip", blob.name)

        # Local file path
        local_path = os.path.join(
            LOCAL_DOWNLOAD_FOLDER,
            blob.name
        )

        # Create subfolders
        os.makedirs(
            os.path.dirname(local_path),
            exist_ok=True
        )

        # Skip existing files
        if os.path.exists(local_path):

            # Compare file sizes
            local_size = os.path.getsize(local_path)

            if local_size == blob.size:
                return ("exists", blob.name)

        # Download file
        blob.download_to_filename(local_path)

        return ("downloaded", blob.name)

    except Exception as e:
        return ("error", f"{blob.name} -> {e}")

# ==============================
# MAIN BACKUP FUNCTION
# ==============================

def backup_storage():

    print("\n📥 Fetching Firebase Storage files...\n")

    blobs = list(bucket.list_blobs())

    total = len(blobs)

    print(f"🔥 Total Files Found: {total}\n")

    downloaded = 0
    skipped = 0
    errors = 0

    # Parallel downloads
    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = [
            executor.submit(download_blob, blob)
            for blob in blobs
        ]

        completed = 0

        for future in as_completed(futures):

            status, message = future.result()

            completed += 1

            if status == "downloaded":
                downloaded += 1
                print(
                    f"✅ [{completed}/{total}] {message}"
                )

            elif status == "exists":
                skipped += 1
                print(
                    f"⏭️ [{completed}/{total}] Exists: {message}"
                )

            elif status == "error":
                errors += 1
                print(
                    f"❌ [{completed}/{total}] {message}"
                )

    # ==============================
    # FINAL REPORT
    # ==============================

    print("\n🎉 BACKUP COMPLETE\n")

    print(f"📦 Total Files   : {total}")
    print(f"✅ Downloaded    : {downloaded}")
    print(f"⏭️ Skipped       : {skipped}")
    print(f"❌ Errors        : {errors}")

    print(
        f"\n📂 Backup Folder:\n"
        f"{os.path.abspath(LOCAL_DOWNLOAD_FOLDER)}"
    )

# ==============================
# START
# ==============================

if __name__ == "__main__":
    backup_storage()
