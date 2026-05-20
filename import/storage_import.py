import os
import json
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.oauth2 import service_account
from google.cloud import storage

import backup_config

# =========================================================
#                    CONFIG
# =========================================================

SOURCE_PROJECT_ID = backup_config.get_resolved_project_id()
SOURCE_BACKUP_DIR = os.path.abspath(SOURCE_PROJECT_ID)

TARGET_SERVICE_ACCOUNT_FILE = "momai-ae458-new-service-key.json"
TARGET_PROJECT_ID = "momai-ae458"
TARGET_BUCKET_NAME = "momai-ae458.firebasestorage.app"

STORAGE_BACKUP_DIR = os.path.join(SOURCE_BACKUP_DIR, "storage_backup")

MAX_WORKERS = 5

log_queue = queue.Queue()


# =========================================================
#                    LOGGER
# =========================================================

def log(msg):
    log_queue.put(msg)


# =========================================================
#             STORAGE CLIENT (TARGET)
# =========================================================

def get_storage_client(service_account_file):
    creds = service_account.Credentials.from_service_account_file(service_account_file)
    client = storage.Client(credentials=creds, project=TARGET_PROJECT_ID)
    return client


# =========================================================
#          UPLOAD SINGLE FILE
# =========================================================

def upload_single_file(client, bucket_name, local_path, blob_path):
    """Upload a single file to Cloud Storage."""
    try:
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)

        # Detect content type
        import mimetypes
        content_type, _ = mimetypes.guess_type(local_path)
        if content_type is None:
            content_type = "application/octet-stream"

        blob.upload_from_filename(local_path, content_type=content_type)
        log(f"✅ Uploaded: {blob_path}")
        return True

    except Exception as e:
        log(f"❌ Failed to upload {blob_path}: {e}")
        return False


# =========================================================
#          IMPORT ALL STORAGE FILES
# =========================================================

def import_all_storage(service_account_file, bucket_name, backup_dir, selected_files=None, progress_callback=None):
    """Upload all files from storage_backup to target Cloud Storage bucket."""
    client = get_storage_client(service_account_file)

    # Collect all files with their relative paths
    all_files = []
    for root_dir, dirs, files in os.walk(backup_dir):
        for file_name in files:
            local_path = os.path.join(root_dir, file_name)
            # Relative path from backup_dir = blob path in storage
            blob_path = os.path.relpath(local_path, backup_dir).replace("\\", "/")

            if selected_files and blob_path not in selected_files:
                continue

            all_files.append((local_path, blob_path))

    if not all_files:
        log("⚠️ No files found to upload")
        return 0

    total = len(all_files)
    completed = 0
    success_count = 0

    log(f"📦 Total files to upload: {total}")
    log(f"🪣 Target bucket: {bucket_name}\n")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for local_path, blob_path in all_files:
            future = executor.submit(upload_single_file, client, bucket_name, local_path, blob_path)
            futures[future] = blob_path

        for future in as_completed(futures):
            blob_path = futures[future]
            completed += 1

            if future.result():
                success_count += 1

            if progress_callback:
                progress_callback(completed, total, blob_path)

    log(f"\n========== STORAGE IMPORT RESULT ==========")
    log(f"Total Files    : {total}")
    log(f"Uploaded       : {success_count}")
    log(f"Failed         : {total - success_count}")
    log(f"\n🎉 Storage Import Complete!")
    return success_count


# =========================================================
#                       GUI
# =========================================================

class StorageImportApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Cloud Storage Import")
        self.root.geometry("1050x750")

        self.service_var = tk.StringVar(value=TARGET_SERVICE_ACCOUNT_FILE)
        self.project_var = tk.StringVar(value=TARGET_PROJECT_ID)
        self.bucket_var = tk.StringVar(value=TARGET_BUCKET_NAME)
        self.source_var = tk.StringVar(value=STORAGE_BACKUP_DIR)
        self.status_var = tk.StringVar(value="Ready")

        self.build_ui()
        self.load_files()
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

        ttk.Label(top, text="Target Bucket Name").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(top, textvariable=self.bucket_var, width=60).grid(row=2, column=1, sticky="w", padx=5)

        ttk.Label(top, text="Storage Backup Folder").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Entry(top, textvariable=self.source_var, width=70).grid(row=3, column=1, sticky="ew", padx=5)
        ttk.Button(top, text="Browse", command=self.browse_source).grid(row=3, column=2)

        top.columnconfigure(1, weight=1)

        # ============ FILE SELECTOR ============
        selector_frame = ttk.LabelFrame(self.root, text="Files to Upload (leave empty for ALL)", padding=10)
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

        ttk.Button(btn_row, text="Refresh", command=self.load_files).pack(side="left", padx=5)
        ttk.Button(btn_row, text="Select All", command=self.select_all).pack(side="left", padx=5)
        ttk.Button(btn_row, text="Clear", command=self.clear_selection).pack(side="left", padx=5)

        # ============ IMPORT BUTTON ============
        action_frame = ttk.Frame(self.root, padding=10)
        action_frame.pack(fill="x")

        self.start_btn = ttk.Button(action_frame, text="START UPLOAD", command=self.start_import)
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
        path = filedialog.askdirectory(title="Select storage_backup folder")
        if path:
            self.source_var.set(path)
            self.load_files()

    def load_files(self):
        self.selector_listbox.delete(0, "end")
        backup_dir = self.source_var.get().strip()
        if os.path.exists(backup_dir):
            for root_dir, dirs, files in os.walk(backup_dir):
                for file_name in files:
                    local_path = os.path.join(root_dir, file_name)
                    blob_path = os.path.relpath(local_path, backup_dir).replace("\\", "/")
                    self.selector_listbox.insert("end", blob_path)

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
        bucket_name = self.bucket_var.get().strip()
        backup_dir = self.source_var.get().strip()

        if not service_file or not os.path.isfile(service_file):
            messagebox.showerror("Error", "Valid target service account JSON select karo.")
            return
        if not project_id:
            messagebox.showerror("Error", "Target Project ID required hai.")
            return
        if not bucket_name:
            messagebox.showerror("Error", "Target bucket name required hai.")
            return
        if not backup_dir or not os.path.isdir(backup_dir):
            messagebox.showerror("Error", "Valid storage backup folder select karo.")
            return

        selected = self.get_selected_items()

        self.start_btn.config(state="disabled")
        self.status_var.set("Uploading...")
        self.progress["value"] = 0

        thread = threading.Thread(
            target=self._run_import,
            args=(service_file, bucket_name, backup_dir, selected),
            daemon=True
        )
        thread.start()

    def _run_import(self, service_file, bucket_name, backup_dir, selected):
        try:
            log("\n===== Cloud Storage Import =====\n")
            import_all_storage(
                service_file, bucket_name, backup_dir, selected,
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
    StorageImportApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
