import os
import json
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from google.oauth2 import service_account
from google.cloud import firestore_admin_v1

import backup_config

# =========================================================
#                    CONFIG
# =========================================================

SOURCE_PROJECT_ID = backup_config.get_resolved_project_id()
SOURCE_BACKUP_DIR = os.path.abspath(SOURCE_PROJECT_ID)

TARGET_SERVICE_ACCOUNT_FILE = "momai-ae458-new-service-key.json"
TARGET_PROJECT_ID = "momai-ae458"

INDEX_BACKUP_DIR = os.path.join(SOURCE_BACKUP_DIR, "index_backup")

log_queue = queue.Queue()


# =========================================================
#                    LOGGER
# =========================================================

def log(msg):
    log_queue.put(msg)


# =========================================================
#             FIRESTORE ADMIN CLIENT
# =========================================================

def get_firestore_admin_client(service_account_file):
    creds = service_account.Credentials.from_service_account_file(service_account_file)
    admin = firestore_admin_v1.FirestoreAdminClient(credentials=creds)
    return admin


# =========================================================
#          IMPORT INDEXES
# =========================================================

def import_indexes(service_account_file, project_id, index_file, database_id="(default)", progress_callback=None):
    """Import Firestore composite indexes from backup JSON."""
    try:
        with open(index_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        indexes = data.get("indexes", [])
        if not indexes:
            log("⚠️ No indexes found in backup file")
            return 0

        admin = get_firestore_admin_client(service_account_file)

        total = len(indexes)
        created = 0
        skipped = 0
        failed = 0

        for i, idx_data in enumerate(indexes):
            collection_group = idx_data.get("collectionGroup")
            query_scope = idx_data.get("queryScope", "COLLECTION")
            fields = idx_data.get("fields", [])

            # Convert fields to API format
            api_fields = []
            for field in fields:
                field_config = {"field_path": field["fieldPath"]}

                if "order" in field:
                    order_map = {
                        "ASCENDING": firestore_admin_v1.Index.IndexField.Order.ASCENDING,
                        "DESCENDING": firestore_admin_v1.Index.IndexField.Order.DESCENDING,
                    }
                    field_config["order"] = order_map.get(field["order"], firestore_admin_v1.Index.IndexField.Order.ASCENDING)
                elif "arrayConfig" in field:
                    array_map = {
                        "CONTAINS": firestore_admin_v1.Index.IndexField.ArrayConfig.CONTAINS,
                    }
                    field_config["array_config"] = array_map.get(field["arrayConfig"], firestore_admin_v1.Index.IndexField.ArrayConfig.CONTAINS)

                api_fields.append(firestore_admin_v1.Index.IndexField(**field_config))

            # Map query scope
            scope_map = {
                "COLLECTION": firestore_admin_v1.Index.QueryScope.COLLECTION,
                "COLLECTION_GROUP": firestore_admin_v1.Index.QueryScope.COLLECTION_GROUP,
            }
            api_scope = scope_map.get(query_scope, firestore_admin_v1.Index.QueryScope.COLLECTION)

            parent = f"projects/{project_id}/databases/{database_id}/collectionGroups/{collection_group}"

            index = firestore_admin_v1.Index(
                query_scope=api_scope,
                fields=api_fields,
            )

            try:
                admin.create_index(request={"parent": parent, "index": index})
                log(f"✅ Created index: {collection_group} ({[f['fieldPath'] for f in fields]})")
                created += 1
            except Exception as e:
                error_msg = str(e)
                if "already exists" in error_msg.lower():
                    log(f"⏩ Already exists: {collection_group} ({[f['fieldPath'] for f in fields]})")
                    skipped += 1
                else:
                    log(f"❌ Failed: {collection_group} → {e}")
                    failed += 1

            if progress_callback:
                progress_callback(i + 1, total, collection_group)

        log(f"\n========== INDEX IMPORT RESULT ==========")
        log(f"Total Indexes  : {total}")
        log(f"Created        : {created}")
        log(f"Already Exists : {skipped}")
        log(f"Failed         : {failed}")
        log(f"\n🎉 Index Import Complete!")
        return created

    except Exception as e:
        log(f"❌ Error importing indexes: {e}")
        return 0


# =========================================================
#                       GUI
# =========================================================

class IndexImportApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Firestore Index Import")
        self.root.geometry("980x650")

        self.service_var = tk.StringVar(value=TARGET_SERVICE_ACCOUNT_FILE)
        self.project_var = tk.StringVar(value=TARGET_PROJECT_ID)
        self.database_var = tk.StringVar(value="(default)")
        self.index_file_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Ready")

        # Auto-detect index file
        self._detect_index_file()

        self.build_ui()
        self.root.after(100, self.process_logs)

    def _detect_index_file(self):
        if os.path.exists(INDEX_BACKUP_DIR):
            files = [f for f in os.listdir(INDEX_BACKUP_DIR) if f.endswith(".indexes.json")]
            if files:
                self.index_file_var.set(os.path.join(INDEX_BACKUP_DIR, files[0]))

    def build_ui(self):
        # ============ TOP CONFIG ============
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Target Service Account").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(top, textvariable=self.service_var, width=70).grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(top, text="Browse", command=self.browse_service).grid(row=0, column=2)

        ttk.Label(top, text="Target Project ID").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(top, textvariable=self.project_var, width=40).grid(row=1, column=1, sticky="w", padx=5)

        ttk.Label(top, text="Database ID").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(top, textvariable=self.database_var, width=40).grid(row=2, column=1, sticky="w", padx=5)

        ttk.Label(top, text="Index Backup File").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Entry(top, textvariable=self.index_file_var, width=70).grid(row=3, column=1, sticky="ew", padx=5)
        ttk.Button(top, text="Browse", command=self.browse_index_file).grid(row=3, column=2)

        top.columnconfigure(1, weight=1)

        # ============ IMPORT BUTTON ============
        action_frame = ttk.Frame(self.root, padding=10)
        action_frame.pack(fill="x")

        self.start_btn = ttk.Button(action_frame, text="START IMPORT", command=self.start_import)
        self.start_btn.pack(side="left")

        ttk.Label(action_frame, textvariable=self.status_var).pack(side="right")

        # ============ PROGRESS ============
        progress_frame = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        progress_frame.pack(fill="x")

        self.progress = ttk.Progressbar(progress_frame, mode="determinate")
        self.progress.pack(fill="x")

        self.progress_label = ttk.Label(progress_frame, text="Waiting...")
        self.progress_label.pack(anchor="w")

        # ============ LOG ============
        log_frame = ttk.Frame(self.root, padding=10)
        log_frame.pack(fill="both", expand=True)

        self.log_text = ScrolledText(log_frame, wrap="word")
        self.log_text.pack(fill="both", expand=True)

    # =====================================================

    def browse_service(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if path:
            self.service_var.set(path)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("project_id"):
                    self.project_var.set(data["project_id"])
            except Exception:
                pass

    def browse_index_file(self):
        path = filedialog.askopenfilename(
            title="Select index backup JSON",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if path:
            self.index_file_var.set(path)

    def process_logs(self):
        while not log_queue.empty():
            msg = log_queue.get()
            self.log_text.insert("end", msg + "\n")
            self.log_text.see("end")
        self.root.after(100, self.process_logs)

    def update_progress(self, current, total, name):
        self.progress["maximum"] = total
        self.progress["value"] = current
        self.progress_label.config(text=f"{current}/{total} → {name}")
        self.root.update_idletasks()

    # =====================================================

    def start_import(self):
        service_file = self.service_var.get().strip()
        project_id = self.project_var.get().strip()
        database_id = self.database_var.get().strip() or "(default)"
        index_file = self.index_file_var.get().strip()

        if not service_file or not os.path.isfile(service_file):
            messagebox.showerror("Error", "Valid target service account JSON select karo.")
            return
        if not project_id:
            messagebox.showerror("Error", "Target Project ID required hai.")
            return
        if not index_file or not os.path.isfile(index_file):
            messagebox.showerror("Error", "Valid index backup file select karo.")
            return

        self.start_btn.config(state="disabled")
        self.status_var.set("Importing Indexes...")
        self.progress["value"] = 0

        thread = threading.Thread(
            target=self._run_import,
            args=(service_file, project_id, index_file, database_id),
            daemon=True
        )
        thread.start()

    def _run_import(self, service_file, project_id, index_file, database_id):
        try:
            log("\n===== Firestore Index Import =====\n")
            import_indexes(
                service_file, project_id, index_file, database_id,
                progress_callback=lambda c, t, n: self.root.after(0, lambda: self.update_progress(c, t, n))
            )
            self.root.after(0, lambda: self.status_var.set("Completed"))
        except Exception as e:
            log(f"\n❌ Fatal Error: {e}")
            self.root.after(0, lambda: self.status_var.set("Error"))
        finally:
            self.root.after(0, lambda: self.start_btn.config(state="normal"))


# =========================================================
#                       MAIN
# =========================================================

def main():
    root = tk.Tk()
    IndexImportApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
