import json
import os
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from collections import OrderedDict

from google.oauth2 import service_account
from google.cloud import firestore
from google.cloud import firestore_admin_v1

import backup_config

# =========================================================
#                    CONFIG
# =========================================================

# Initialize folders and get paths from central config
paths = backup_config.init_folders()

PROJECT_ID = backup_config.get_resolved_project_id()
SERVICE_ACCOUNT_FILE = backup_config.SERVICE_ACCOUNT_FILE

BASE_DIR = paths["index_backup"]

os.makedirs(BASE_DIR, exist_ok=True)

log_queue = queue.Queue()


# =========================================================
#                    LOGGER
# =========================================================

def log(msg):
    log_queue.put(msg)


# =========================================================
#                 FIRESTORE CLIENTS
# =========================================================

def make_clients(cred_path: str, project_id: str, database_id: str):
    creds = service_account.Credentials.from_service_account_file(cred_path)

    db = firestore.Client(
        project=project_id,
        credentials=creds,
        database=database_id
    )

    admin = firestore_admin_v1.FirestoreAdminClient(credentials=creds)
    return db, admin


# =========================================================
#              COLLECTION IDS
# =========================================================

def get_top_level_collection_ids(db: firestore.Client):
    return sorted([c.id for c in db.collections()])


# =========================================================
#           INDEX TO FIREBASE FORMAT
# =========================================================

def index_to_firebase_format(index_obj):
    fields = []
    for f in index_obj.fields:
        if f.field_path == "__name__":
            continue

        item = OrderedDict()
        item["fieldPath"] = f.field_path

        if f.order and str(f.order) != "0":
            item["order"] = firestore_admin_v1.Index.IndexField.Order(f.order).name
        elif f.array_config and str(f.array_config) != "0":
            item["arrayConfig"] = firestore_admin_v1.Index.IndexField.ArrayConfig(f.array_config).name
        elif getattr(f, "vector_config", None):
            item["vectorConfig"] = {"configured": True}

        fields.append(item)

    if not fields:
        return None

    return OrderedDict({
        "collectionGroup": index_obj.name.split("/collectionGroups/")[1].split("/indexes/")[0],
        "queryScope": firestore_admin_v1.Index.QueryScope(index_obj.query_scope).name,
        "fields": fields
    })


# =========================================================
#              FETCH INDEXES
# =========================================================

def fetch_indexes(admin_client, project_id: str, database_id: str, collection_ids):
    indexes = []

    for coll_id in collection_ids:
        parent = f"projects/{project_id}/databases/{database_id}/collectionGroups/{coll_id}"

        try:
            for idx in admin_client.list_indexes(request={"parent": parent}):
                if firestore_admin_v1.Index.State(idx.state).name != "READY":
                    continue

                converted = index_to_firebase_format(idx)
                if converted:
                    indexes.append(converted)

        except Exception as e:
            log(f"⚠️ Could not list indexes for collection group '{coll_id}': {e}")

    seen = set()
    unique = []
    for item in indexes:
        key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            unique.append(item)

    unique.sort(key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
    return unique


# =========================================================
#              SAVE EXPORT
# =========================================================

def save_export(indexes, output_file: str):
    data = {
        "indexes": indexes,
        "fieldOverrides": []
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    log(f"\n✅ Index export saved to: {output_file}")
    log(f"✅ Total composite indexes exported: {len(indexes)}")

    return data


# =========================================================
#             COMPARE INDEXES
# =========================================================

def compare_indexes(saved_data, live_data):
    saved_set = {
        json.dumps(x, sort_keys=True, ensure_ascii=False)
        for x in saved_data.get("indexes", [])
    }
    live_set = {
        json.dumps(x, sort_keys=True, ensure_ascii=False)
        for x in live_data.get("indexes", [])
    }

    missing = saved_set - live_set
    extra = live_set - saved_set

    log("\n========== RESULT ==========")
    log(f"Local Indexes   : {len(saved_set)}")
    log(f"Server Indexes  : {len(live_set)}")
    log(f"Missing Indexes : {len(missing)}")
    log(f"Extra Indexes   : {len(extra)}")

    if not missing and not extra:
        log("\n✅ PERFECT MATCH — Indexes are identical 🔥")
    else:
        log("\n⚠️ Differences found")

        if missing:
            log("\n❌ Missing Indexes (first 5):")
            for item in list(missing)[:5]:
                log(json.dumps(json.loads(item), indent=2, ensure_ascii=False))

        if extra:
            log("\n⚠️ Extra Indexes on Server (first 5):")
            for item in list(extra)[:5]:
                log(json.dumps(json.loads(item), indent=2, ensure_ascii=False))


# =========================================================
#                       GUI
# =========================================================

class FirestoreIndexDownloaderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Firestore Index Downloader")
        self.root.geometry("980x720")

        self.cred_var = tk.StringVar(value=SERVICE_ACCOUNT_FILE)
        self.project_var = tk.StringVar(value=PROJECT_ID)
        self.database_var = tk.StringVar(value="(default)")
        self.output_var = tk.StringVar(value=BASE_DIR)
        self.status_var = tk.StringVar(value="Ready")

        self.build_ui()
        self.root.after(100, self.process_log_queue)

    def build_ui(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Service Account JSON").grid(row=0, column=0, sticky="w", pady=6)
        ttk.Entry(top, textvariable=self.cred_var, width=70).grid(row=0, column=1, sticky="ew", pady=6, padx=6)
        ttk.Button(top, text="Browse", command=self.browse_cred).grid(row=0, column=2, pady=6)

        ttk.Label(top, text="Project ID").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(top, textvariable=self.project_var, width=40).grid(row=1, column=1, sticky="w", pady=6, padx=6)

        ttk.Label(top, text="Database ID").grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(top, textvariable=self.database_var, width=40).grid(row=2, column=1, sticky="w", pady=6, padx=6)

        ttk.Label(top, text="Output Folder").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Entry(top, textvariable=self.output_var, width=70).grid(row=3, column=1, sticky="ew", pady=6, padx=6)
        ttk.Button(top, text="Browse", command=self.browse_output).grid(row=3, column=2, pady=6)

        top.columnconfigure(1, weight=1)

        btn_frame = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        btn_frame.pack(fill="x")

        self.start_btn = ttk.Button(btn_frame, text="Download Indexes", command=self.start_download)
        self.start_btn.pack(side="left")

        ttk.Label(btn_frame, textvariable=self.status_var).pack(side="right")

        progress_frame = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        progress_frame.pack(fill="x")

        self.progress = ttk.Progressbar(progress_frame, mode="indeterminate")
        self.progress.pack(fill="x")

        log_frame = ttk.Frame(self.root, padding=10)
        log_frame.pack(fill="both", expand=True)

        self.log_text = ScrolledText(log_frame, wrap="word")
        self.log_text.pack(fill="both", expand=True)

    def browse_cred(self):
        path = filedialog.askopenfilename(
            title="Select service account JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if path:
            self.cred_var.set(path)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not self.project_var.get().strip():
                    self.project_var.set(data.get("project_id", ""))
            except Exception:
                pass

    def browse_output(self):
        path = filedialog.askdirectory(title="Select output folder for index backup")
        if path:
            self.output_var.set(path)

    def process_log_queue(self):
        while not log_queue.empty():
            msg = log_queue.get()
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
        self.root.after(100, self.process_log_queue)

    def start_download(self):
        cred_path = self.cred_var.get().strip()
        project_id = self.project_var.get().strip()
        database_id = self.database_var.get().strip() or "(default)"
        output_dir = self.output_var.get().strip()

        if not cred_path or not os.path.isfile(cred_path):
            messagebox.showerror("Error", "Valid service account JSON select karo.")
            return

        if not project_id:
            messagebox.showerror("Error", "Project ID required hai.")
            return

        if not output_dir:
            output_dir = BASE_DIR
            self.output_var.set(output_dir)

        os.makedirs(output_dir, exist_ok=True)

        self.start_btn.config(state="disabled")
        self.status_var.set("Running...")
        self.progress.start(10)

        thread = threading.Thread(
            target=self.run_task,
            args=(cred_path, project_id, database_id, output_dir),
            daemon=True
        )
        thread.start()

    def run_task(self, cred_path, project_id, database_id, output_dir):
        try:
            log("\n===== Firestore Index Export + Verify (Service Account JSON) =====\n")
            log("🔌 Creating Firestore clients...")
            db, admin = make_clients(cred_path, project_id, database_id)

            log("📂 Reading collection groups...")
            collection_ids = get_top_level_collection_ids(db)
            log(f"✅ Top-level collections found: {len(collection_ids)}")

            log("📥 Fetching indexes from server...")
            indexes = fetch_indexes(admin, project_id, database_id, collection_ids)

            # Save to the index_backup folder
            output_file = os.path.join(output_dir, f"{project_id}_firestore.indexes.json")
            live_data = save_export(indexes, output_file)

            log("📂 Loading exported file for verification...")
            with open(output_file, "r", encoding="utf-8") as f:
                saved_data = json.load(f)

            compare_indexes(saved_data, live_data)

            self.root.after(0, lambda: self.status_var.set("Completed"))
        except Exception as e:
            log(f"\n❌ Error: {e}")
            self.root.after(0, lambda: self.status_var.set("Error"))
        finally:
            self.root.after(0, self.finish_ui)

    def finish_ui(self):
        self.progress.stop()
        self.start_btn.config(state="normal")


# =========================================================
#                       MAIN
# =========================================================

def main():
    root = tk.Tk()
    FirestoreIndexDownloaderGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
