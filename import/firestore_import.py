import os
import json
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from google.oauth2 import service_account
from google.cloud import firestore

import backup_config

# =========================================================
#                    CONFIG
# =========================================================

SOURCE_PROJECT_ID = backup_config.get_resolved_project_id()
SOURCE_BACKUP_DIR = os.path.abspath(SOURCE_PROJECT_ID)

TARGET_SERVICE_ACCOUNT_FILE = "momai-ae458-new-service-key.json"
TARGET_PROJECT_ID = "momai-ae458"

FIRESTORE_BACKUP_DIR = os.path.join(SOURCE_BACKUP_DIR, "firestore_backup")

log_queue = queue.Queue()


# =========================================================
#                    LOGGER
# =========================================================

def log(msg):
    log_queue.put(msg)


# =========================================================
#             FIRESTORE CLIENT (TARGET)
# =========================================================

def get_firestore_client(service_account_file, project_id):
    creds = service_account.Credentials.from_service_account_file(service_account_file)
    db = firestore.Client(project=project_id, credentials=creds)
    return db


# =========================================================
#          IMPORT SINGLE COLLECTION
# =========================================================

def import_single_collection(db, collection_name, json_file):
    """Import a single collection JSON file into Firestore."""
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            docs = json.load(f)

        if not isinstance(docs, list):
            log(f"⚠️ Skipping {collection_name}: not a list format")
            return 0

        count = 0
        batch = db.batch()
        batch_count = 0

        for doc_data in docs:
            doc_id = doc_data.pop("id", None)

            if doc_id:
                ref = db.collection(collection_name).document(str(doc_id))
            else:
                ref = db.collection(collection_name).document()

            batch.set(ref, doc_data)
            batch_count += 1
            count += 1

            # Firestore batch limit is 500
            if batch_count >= 450:
                batch.commit()
                batch = db.batch()
                batch_count = 0

        # Commit remaining
        if batch_count > 0:
            batch.commit()

        log(f"✅ Imported {collection_name}: {count} documents")
        return count

    except Exception as e:
        log(f"❌ Error importing {collection_name}: {e}")
        return 0


# =========================================================
#          IMPORT ALL COLLECTIONS
# =========================================================

def import_all_firestore(service_account_file, project_id, backup_dir, selected_collections=None, progress_callback=None):
    """Import all (or selected) collections from backup into target Firestore."""
    db = get_firestore_client(service_account_file, project_id)

    json_files = [f for f in os.listdir(backup_dir) if f.endswith(".json")]

    if selected_collections:
        json_files = [f for f in json_files if os.path.splitext(f)[0] in selected_collections]

    total = len(json_files)
    completed = 0
    total_docs = 0

    for json_file in json_files:
        collection_name = os.path.splitext(json_file)[0]
        file_path = os.path.join(backup_dir, json_file)

        log(f"\n📂 Importing collection: {collection_name}")
        count = import_single_collection(db, collection_name, file_path)
        total_docs += count

        completed += 1
        if progress_callback:
            progress_callback(completed, total, collection_name)

    log(f"\n🎉 Firestore Import Complete! Total documents: {total_docs}")
    return total_docs


# =========================================================
#                       GUI
# =========================================================

class FirestoreImportApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Firestore Data Import")
        self.root.geometry("1000x750")

        self.service_var = tk.StringVar(value=TARGET_SERVICE_ACCOUNT_FILE)
        self.project_var = tk.StringVar(value=TARGET_PROJECT_ID)
        self.source_var = tk.StringVar(value=FIRESTORE_BACKUP_DIR)
        self.status_var = tk.StringVar(value="Ready")

        self.build_ui()
        self.load_collections()
        self.root.after(100, self.process_logs)

    def build_ui(self):
        # ============ TOP CONFIG ============
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Target Service Account").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(top, textvariable=self.service_var, width=70).grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(top, text="Browse", command=self.browse_service).grid(row=0, column=2)

        ttk.Label(top, text="Target Project ID").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(top, textvariable=self.project_var, width=40).grid(row=1, column=1, sticky="w", padx=5)

        ttk.Label(top, text="Source Backup Folder").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(top, textvariable=self.source_var, width=70).grid(row=2, column=1, sticky="ew", padx=5)
        ttk.Button(top, text="Browse", command=self.browse_source).grid(row=2, column=2)

        top.columnconfigure(1, weight=1)

        # ============ COLLECTION SELECTOR ============
        selector_frame = ttk.LabelFrame(self.root, text="Select Collections (leave empty for ALL)", padding=10)
        selector_frame.pack(fill="x", padx=10, pady=5)

        list_frame = ttk.Frame(selector_frame)
        list_frame.pack(fill="x")

        self.selector_listbox = tk.Listbox(list_frame, selectmode="extended", height=8)
        self.selector_listbox.pack(fill="x", side="left", expand=True)

        selector_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.selector_listbox.yview)
        selector_scroll.pack(side="right", fill="y")
        self.selector_listbox.config(yscrollcommand=selector_scroll.set)

        btn_row = ttk.Frame(selector_frame)
        btn_row.pack(fill="x", pady=5)

        ttk.Button(btn_row, text="Refresh", command=self.load_collections).pack(side="left", padx=5)
        ttk.Button(btn_row, text="Select All", command=self.select_all).pack(side="left", padx=5)
        ttk.Button(btn_row, text="Clear", command=self.clear_selection).pack(side="left", padx=5)

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

    def browse_source(self):
        path = filedialog.askdirectory(title="Select firestore_backup folder")
        if path:
            self.source_var.set(path)
            self.load_collections()

    def load_collections(self):
        self.selector_listbox.delete(0, "end")
        backup_dir = self.source_var.get().strip()
        if os.path.exists(backup_dir):
            for f in sorted(os.listdir(backup_dir)):
                if f.endswith(".json"):
                    self.selector_listbox.insert("end", os.path.splitext(f)[0])

    def select_all(self):
        self.selector_listbox.select_set(0, "end")

    def clear_selection(self):
        self.selector_listbox.selection_clear(0, "end")

    def get_selected_items(self):
        selected = self.selector_listbox.curselection()
        if not selected:
            return None
        return [self.selector_listbox.get(i) for i in selected]

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
        backup_dir = self.source_var.get().strip()

        if not service_file or not os.path.isfile(service_file):
            messagebox.showerror("Error", "Valid target service account JSON select karo.")
            return
        if not project_id:
            messagebox.showerror("Error", "Target Project ID required hai.")
            return
        if not backup_dir or not os.path.isdir(backup_dir):
            messagebox.showerror("Error", "Valid source backup folder select karo.")
            return

        selected = self.get_selected_items()

        self.start_btn.config(state="disabled")
        self.status_var.set("Importing...")
        self.progress["value"] = 0

        thread = threading.Thread(
            target=self._run_import,
            args=(service_file, project_id, backup_dir, selected),
            daemon=True
        )
        thread.start()

    def _run_import(self, service_file, project_id, backup_dir, selected):
        try:
            log("\n===== Firestore Data Import =====\n")
            import_all_firestore(
                service_file, project_id, backup_dir, selected,
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
    FirestoreImportApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
