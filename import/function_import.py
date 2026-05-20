import os
import json
import subprocess
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

import backup_config

# =========================================================
#                    CONFIG
# =========================================================

SOURCE_PROJECT_ID = backup_config.get_resolved_project_id()
SOURCE_BACKUP_DIR = os.path.abspath(SOURCE_PROJECT_ID)

TARGET_SERVICE_ACCOUNT_FILE = "momai-ae458-new-service-key.json"
TARGET_PROJECT_ID = "momai-ae458"

FUNCTION_BACKUP_DIR = os.path.join(SOURCE_BACKUP_DIR, "function_backup")

log_queue = queue.Queue()


# =========================================================
#                    LOGGER
# =========================================================

def log(msg):
    log_queue.put(msg)


# =========================================================
#                 SHELL COMMAND
# =========================================================

def run_command(command):
    log(f"🟢 COMMAND: {command}")
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True
    )
    if result.stdout.strip():
        log(f"🔹 STDOUT: {result.stdout.strip()}")
    if result.stderr.strip():
        log(f"🔸 STDERR: {result.stderr.strip()}")
    return result.returncode, result.stdout.strip()


# =========================================================
#       DEPLOY SINGLE FUNCTION
# =========================================================

def deploy_single_function(fn_metadata, source_dir, project_id):
    """Deploy a single Cloud Function using gcloud."""
    name = fn_metadata["name"]
    region = fn_metadata["region"]
    runtime = fn_metadata.get("runtime", "nodejs22")
    entry_point = fn_metadata.get("entry_point", "helloWorld")
    memory = fn_metadata.get("memory", 256)
    timeout = fn_metadata.get("timeout", "60s")
    trigger_type = fn_metadata.get("trigger_type", "http")

    # Build gcloud deploy command
    cmd = (
        f"gcloud functions deploy {name} "
        f"--project={project_id} "
        f"--region={region} "
        f"--runtime={runtime} "
        f"--entry-point={entry_point} "
        f"--memory={memory}MB "
        f"--timeout={timeout} "
        f"--source=\"{source_dir}\" "
    )

    if trigger_type == "http":
        cmd += "--trigger-http --allow-unauthenticated "
    elif trigger_type == "firestore":
        event_type = fn_metadata.get("event_type", "providers/cloud.firestore/eventTypes/document.write")
        document_path = fn_metadata.get("document_path", "")

        # Replace old project ID with new project ID in document path
        document_path = document_path.replace(SOURCE_PROJECT_ID, project_id)

        # Extract just the document pattern (after /documents/)
        if "/documents/" in document_path:
            doc_pattern = document_path.split("/documents/")[1]
        else:
            doc_pattern = document_path

        cmd += f"--trigger-event={event_type} --trigger-resource=\"projects/{project_id}/databases/(default)/documents/{doc_pattern}\" "

    log(f"\n🚀 Deploying: {name}")
    returncode, output = run_command(cmd)

    if returncode == 0:
        log(f"✅ Deployed: {name}")
        return True
    else:
        log(f"❌ Failed to deploy: {name}")
        return False


# =========================================================
#       IMPORT ALL FUNCTIONS
# =========================================================

def import_functions(project_id, service_account_file, function_backup_dir, selected_functions=None, progress_callback=None):
    """Deploy Cloud Functions from backup to target project."""

    # Set gcloud project and auth
    run_command(f"gcloud config set project {project_id}")
    run_command(f"gcloud auth activate-service-account --key-file=\"{service_account_file}\"")

    configs_dir = os.path.join(function_backup_dir, "configs")

    # Load function metadata
    all_functions = {}

    # Load HTTP functions
    http_config = os.path.join(configs_dir, f"{SOURCE_PROJECT_ID}_http_functions.json")
    if os.path.exists(http_config):
        with open(http_config, "r", encoding="utf-8") as f:
            all_functions.update(json.load(f))

    # Load Firestore functions
    firestore_config = os.path.join(configs_dir, f"{SOURCE_PROJECT_ID}_firestore_functions.json")
    if os.path.exists(firestore_config):
        with open(firestore_config, "r", encoding="utf-8") as f:
            all_functions.update(json.load(f))

    if selected_functions:
        all_functions = {k: v for k, v in all_functions.items() if k in selected_functions}

    if not all_functions:
        log("❌ No functions found to deploy")
        return 0

    total = len(all_functions)
    completed = 0
    success_count = 0

    for fn_name, fn_meta in all_functions.items():
        trigger_type = fn_meta.get("trigger_type", "http")
        region = fn_meta.get("region", "asia-south1")

        # Find source directory
        source_dir = os.path.join(function_backup_dir, trigger_type, region, fn_name)

        if not os.path.exists(source_dir):
            log(f"⚠️ Source not found for {fn_name}, skipping...")
            completed += 1
            if progress_callback:
                progress_callback(completed, total, fn_name)
            continue

        success = deploy_single_function(fn_meta, source_dir, project_id)
        if success:
            success_count += 1

        completed += 1
        if progress_callback:
            progress_callback(completed, total, fn_name)

    log(f"\n========== FUNCTION IMPORT RESULT ==========")
    log(f"Total Functions : {total}")
    log(f"Deployed        : {success_count}")
    log(f"Failed          : {total - success_count}")
    log(f"\n🎉 Function Import Complete!")
    return success_count


# =========================================================
#                       GUI
# =========================================================

class FunctionImportApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Cloud Functions Import")
        self.root.geometry("1050x750")

        self.service_var = tk.StringVar(value=TARGET_SERVICE_ACCOUNT_FILE)
        self.project_var = tk.StringVar(value=TARGET_PROJECT_ID)
        self.source_var = tk.StringVar(value=FUNCTION_BACKUP_DIR)
        self.status_var = tk.StringVar(value="Ready")

        self.build_ui()
        self.load_functions()
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

        ttk.Label(top, text="Function Backup Folder").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(top, textvariable=self.source_var, width=70).grid(row=2, column=1, sticky="ew", padx=5)
        ttk.Button(top, text="Browse", command=self.browse_source).grid(row=2, column=2)

        top.columnconfigure(1, weight=1)

        # ============ FUNCTION SELECTOR ============
        selector_frame = ttk.LabelFrame(self.root, text="Select Functions (leave empty for ALL)", padding=10)
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

        ttk.Button(btn_row, text="Refresh", command=self.load_functions).pack(side="left", padx=5)
        ttk.Button(btn_row, text="Select All", command=self.select_all).pack(side="left", padx=5)
        ttk.Button(btn_row, text="Clear", command=self.clear_selection).pack(side="left", padx=5)

        # ============ IMPORT BUTTON ============
        action_frame = ttk.Frame(self.root, padding=10)
        action_frame.pack(fill="x")

        self.start_btn = ttk.Button(action_frame, text="START DEPLOY", command=self.start_import)
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
        path = filedialog.askdirectory(title="Select function_backup folder")
        if path:
            self.source_var.set(path)
            self.load_functions()

    def load_functions(self):
        self.selector_listbox.delete(0, "end")
        configs_dir = os.path.join(self.source_var.get().strip(), "configs")
        if os.path.exists(configs_dir):
            all_fns = set()
            for cfg_file in os.listdir(configs_dir):
                if cfg_file.endswith("_functions.json"):
                    try:
                        with open(os.path.join(configs_dir, cfg_file), "r", encoding="utf-8") as f:
                            data = json.load(f)
                            all_fns.update(data.keys())
                    except Exception:
                        pass
            for fn in sorted(all_fns):
                self.selector_listbox.insert("end", fn)

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
        source_dir = self.source_var.get().strip()

        if not service_file or not os.path.isfile(service_file):
            messagebox.showerror("Error", "Valid target service account JSON select karo.")
            return
        if not project_id:
            messagebox.showerror("Error", "Target Project ID required hai.")
            return
        if not source_dir or not os.path.isdir(source_dir):
            messagebox.showerror("Error", "Valid function backup folder select karo.")
            return

        selected = self.get_selected_items()

        self.start_btn.config(state="disabled")
        self.status_var.set("Deploying...")
        self.progress["value"] = 0

        thread = threading.Thread(
            target=self._run_import,
            args=(project_id, service_file, source_dir, selected),
            daemon=True
        )
        thread.start()

    def _run_import(self, project_id, service_file, source_dir, selected):
        try:
            log("\n===== Cloud Functions Deploy =====\n")
            import_functions(
                project_id, service_file, source_dir, selected,
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
    FunctionImportApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
