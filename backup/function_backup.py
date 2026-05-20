import os
import json
import zipfile
import threading
import queue
import requests
import subprocess
import tkinter as tk

from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from concurrent.futures import ThreadPoolExecutor, as_completed

from google.oauth2 import service_account
from googleapiclient.discovery import build


# =========================================================
#                    CONFIG
# =========================================================

import backup_config

# Initialize folders and get paths from central config
paths = backup_config.init_folders()

PROJECT_ID = backup_config.get_resolved_project_id()
SERVICE_ACCOUNT_FILE = backup_config.SERVICE_ACCOUNT_FILE

BASE_DIR = paths["function_backup"]
CONFIG_DIR = os.path.join(BASE_DIR, "configs")

DEBUG = True
MAX_WORKERS = 5

os.makedirs(BASE_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)

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

    if DEBUG:
        log(f"\n🟢 COMMAND: {command}")

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=True
    )

    if DEBUG:
        log(f"🔹 STDOUT:\n{result.stdout.strip() or 'EMPTY'}")
        log(f"🔸 STDERR:\n{result.stderr.strip() or 'EMPTY'}")

    return result.stdout.strip()


# =========================================================
#             GCLOUD PROJECT SETTER
# =========================================================

def set_gcloud_project(project_id):

    log(f"\n🔧 Setting project: {project_id}")

    run_command(f"gcloud config set project {project_id}")

    log("✅ Project set")


# =========================================================
#                  AUTH CLIENT
# =========================================================

def get_cloudfunctions_client():

    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )

    return build(
        "cloudfunctions",
        "v1",
        credentials=creds,
        cache_discovery=False
    )


# =========================================================
#                 REGIONS
# =========================================================

def list_regions(project_id):

    service = get_cloudfunctions_client()

    parent = f"projects/{project_id}"

    resp = service.projects().locations().list(
        name=parent
    ).execute()

    return [
        loc["locationId"]
        for loc in resp.get("locations", [])
    ]


# =========================================================
#               FETCH FUNCTIONS
# =========================================================

def fetch_functions(project_id, region):

    service = get_cloudfunctions_client()

    parent = f"projects/{project_id}/locations/{region}"

    try:

        resp = service.projects().locations().functions().list(
            parent=parent
        ).execute()

        return resp.get("functions", [])

    except Exception as e:

        log(f"⚠️ Error in {region}: {e}")

        return []


# =========================================================
#             DETECT TRIGGER TYPE
# =========================================================

def detect_trigger(fn):

    if fn.get("httpsTrigger"):
        return "http"

    evt = json.dumps(
        fn.get("eventTrigger", {})
    ).lower()

    if "firestore" in evt:
        return "firestore"

    return "other"


# =========================================================
#             EXTRACT METADATA
# =========================================================

def extract_metadata(fn):

    name = fn["name"].split("/")[-1]

    region = fn["name"].split(
        "/locations/"
    )[1].split("/")[0]

    trigger = detect_trigger(fn)

    metadata = {
        "name": name,
        "region": region,
        "runtime": fn.get("runtime"),
        "entry_point": fn.get("entryPoint"),
        "memory": fn.get("availableMemoryMb", 256),
        "timeout": fn.get("timeout", "60s"),
        "trigger_type": trigger,
        "ingressSettings": fn.get(
            "ingressSettings",
            "ALLOW_ALL"
        )
    }

    # ---------------- HTTP ----------------

    if trigger == "http":

        metadata["securityLevel"] = fn.get(
            "httpsTrigger",
            {}
        ).get(
            "securityLevel",
            "SECURE_ALWAYS"
        )

    # ---------------- FIRESTORE ----------------

    if trigger == "firestore":

        evt = fn.get("eventTrigger", {})

        metadata["event_type"] = evt.get("eventType")

        metadata["document_path"] = evt.get("resource")

        metadata["retry"] = bool(
            evt.get(
                "failurePolicy",
                {}
            ).get("retry")
        )

    return metadata


# =========================================================
#                  SAVE JSON
# =========================================================

def save_json(data, filename):

    path = os.path.join(CONFIG_DIR, filename)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    log(f"💾 Saved: {path}")


# =========================================================
#                DOWNLOAD ZIP
# =========================================================

def download_zip(url, output_path):

    response = requests.get(
        url,
        stream=True,
        timeout=120
    )

    response.raise_for_status()

    with open(output_path, "wb") as f:

        for chunk in response.iter_content(
            chunk_size=1024
        ):

            if chunk:
                f.write(chunk)


# =========================================================
#            PROCESS SINGLE FUNCTION
# =========================================================

def process_single_function(fn):

    try:

        metadata = extract_metadata(fn)

        name = metadata["name"]
        region = metadata["region"]
        trigger = metadata["trigger_type"]

        folder = os.path.join(
            BASE_DIR,
            trigger,
            region,
            name
        )

        os.makedirs(folder, exist_ok=True)

        # Skip if already downloaded

        if os.listdir(folder):

            log(f"⏩ Skipping {name}")

            return metadata

        log(f"\n🧩 Processing: {name}")

        source_url = fn.get("sourceArchiveUrl")

        # Generate temporary URL if missing

        if not source_url:

            try:

                service = get_cloudfunctions_client()

                resp = service.projects().locations().functions().generateDownloadUrl(
                    name=fn["name"]
                ).execute()

                source_url = resp.get("downloadUrl")

            except Exception as e:

                log(f"❌ URL Error ({name}): {e}")

                return metadata

        if not source_url:

            log(f"❌ No source URL: {name}")

            return metadata

        zip_path = os.path.join(
            folder,
            f"{name}.zip"
        )

        log(f"⬇️ Downloading {name}")

        download_zip(source_url, zip_path)

        try:

            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(folder)

            log(f"✅ Extracted: {name}")

        except zipfile.BadZipFile:

            log(f"❌ Corrupted ZIP: {name}")

        finally:

            if os.path.exists(zip_path):
                os.remove(zip_path)

        return metadata

    except Exception as e:

        log(f"❌ Error processing function: {e}")

        return None


# =========================================================
#             PROCESS ALL FUNCTIONS
# =========================================================

def process_all_functions(functions, progress_callback=None):

    all_metadata = {}

    total = len(functions)
    completed = 0

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = [
            executor.submit(
                process_single_function,
                fn
            )
            for fn in functions
        ]

        for future in as_completed(futures):

            completed += 1

            metadata = future.result()

            if metadata:

                name = metadata["name"]

                all_metadata[name] = metadata

            if progress_callback:

                progress_callback(
                    completed,
                    total,
                    metadata["name"]
                    if metadata else "Unknown"
                )

    return all_metadata


# =========================================================
#                       GUI
# =========================================================

class App:

    def __init__(self, root):

        self.root = root

        self.root.title(
            "Cloud Functions Downloader"
        )

        self.root.geometry("1100x750")

        self.project_var = tk.StringVar(value=backup_config.get_resolved_project_id())

        self.service_var = tk.StringVar(value=backup_config.SERVICE_ACCOUNT_FILE)

        self.output_var = tk.StringVar(
            value=BASE_DIR
        )

        self.status_var = tk.StringVar(
            value="Ready"
        )

        self.build_ui()

        self.root.after(
            100,
            self.process_logs
        )

    # =====================================================

    def build_ui(self):

        top = ttk.Frame(
            self.root,
            padding=10
        )

        top.pack(fill="x")

        # PROJECT ID

        ttk.Label(
            top,
            text="Project ID"
        ).grid(
            row=0,
            column=0,
            sticky="w",
            pady=5
        )

        ttk.Entry(
            top,
            textvariable=self.project_var,
            width=70
        ).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=5
        )

        # SERVICE ACCOUNT

        ttk.Label(
            top,
            text="Service Account"
        ).grid(
            row=1,
            column=0,
            sticky="w",
            pady=5
        )

        ttk.Entry(
            top,
            textvariable=self.service_var,
            width=70
        ).grid(
            row=1,
            column=1,
            sticky="ew",
            padx=5
        )

        ttk.Button(
            top,
            text="Browse",
            command=self.browse_service
        ).grid(
            row=1,
            column=2
        )

        # OUTPUT

        ttk.Label(
            top,
            text="Output Folder"
        ).grid(
            row=2,
            column=0,
            sticky="w",
            pady=5
        )

        ttk.Entry(
            top,
            textvariable=self.output_var,
            width=70
        ).grid(
            row=2,
            column=1,
            sticky="ew",
            padx=5
        )

        ttk.Button(
            top,
            text="Browse",
            command=self.browse_output
        ).grid(
            row=2,
            column=2
        )

        top.columnconfigure(
            1,
            weight=1
        )

        # BUTTONS

        btn_frame = ttk.Frame(
            self.root,
            padding=10
        )

        btn_frame.pack(fill="x")

        self.start_btn = ttk.Button(
            btn_frame,
            text="START",
            command=self.start
        )

        self.start_btn.pack(side="left")

        ttk.Label(
            btn_frame,
            textvariable=self.status_var
        ).pack(side="right")

        # PROGRESS

        progress_frame = ttk.Frame(
            self.root,
            padding=10
        )

        progress_frame.pack(fill="x")

        self.progress = ttk.Progressbar(
            progress_frame,
            mode="determinate"
        )

        self.progress.pack(fill="x")

        self.progress_label = ttk.Label(
            progress_frame,
            text="Waiting..."
        )

        self.progress_label.pack(
            anchor="w"
        )

        # LOGS

        log_frame = ttk.Frame(
            self.root,
            padding=10
        )

        log_frame.pack(
            fill="both",
            expand=True
        )

        self.log_text = ScrolledText(
            log_frame
        )

        self.log_text.pack(
            fill="both",
            expand=True
        )

    # =====================================================

    def browse_service(self):

        path = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json")]
        )

        if path:
            self.service_var.set(path)

    # =====================================================

    def browse_output(self):

        path = filedialog.askdirectory()

        if path:
            self.output_var.set(path)

    # =====================================================

    def process_logs(self):

        while not log_queue.empty():

            msg = log_queue.get()

            self.log_text.insert(
                "end",
                msg + "\n"
            )

            self.log_text.see("end")

        self.root.after(
            100,
            self.process_logs
        )

    # =====================================================

    def update_progress(
        self,
        current,
        total,
        name
    ):

        self.progress["maximum"] = total

        self.progress["value"] = current

        self.progress_label.config(
            text=f"{current}/{total} completed → {name}"
        )

        self.root.update_idletasks()

    # =====================================================

    def start(self):

        global PROJECT_ID
        global SERVICE_ACCOUNT_FILE
        global BASE_DIR
        global CONFIG_DIR

        PROJECT_ID = self.project_var.get().strip()

        SERVICE_ACCOUNT_FILE = self.service_var.get().strip()

        BASE_DIR = self.output_var.get().strip()

        CONFIG_DIR = os.path.join(
            BASE_DIR,
            "configs"
        )

        os.makedirs(BASE_DIR, exist_ok=True)

        os.makedirs(CONFIG_DIR, exist_ok=True)

        # Ensure sibling folders exist in the project directory when backup starts
        parent_dir = os.path.dirname(BASE_DIR)
        if parent_dir:
            backup_config.init_folders(os.path.basename(parent_dir))

        if not PROJECT_ID:

            messagebox.showerror(
                "Error",
                "Project ID required"
            )

            return

        if not os.path.isfile(
            SERVICE_ACCOUNT_FILE
        ):

            messagebox.showerror(
                "Error",
                "Invalid service account"
            )

            return

        self.start_btn.config(
            state="disabled"
        )

        thread = threading.Thread(
            target=self.run_task,
            daemon=True
        )

        thread.start()

    # =====================================================

    def run_task(self):

        try:

            self.status_var.set("Running")

            log("🚀 Starting Downloader")

            set_gcloud_project(PROJECT_ID)

            regions = list_regions(PROJECT_ID)

            log(f"🌍 Regions Found: {len(regions)}")

            all_functions = []

            for region in regions:

                funcs = fetch_functions(
                    PROJECT_ID,
                    region
                )

                if funcs:

                    log(
                        f"📦 {region}: {len(funcs)} functions"
                    )

                    all_functions.extend(funcs)

            if not all_functions:

                log("❌ No functions found")

                self.status_var.set(
                    "No Functions"
                )

                return

            metadata = process_all_functions(
                all_functions,
                progress_callback=lambda c, t, n:
                self.root.after(
                    0,
                    lambda:
                    self.update_progress(
                        c,
                        t,
                        n
                    )
                )
            )

            # =================================================
            #          SAVE HTTP + FIRESTORE CONFIGS
            # =================================================

            # ---------------- HTTP ----------------

            http_functions = {
                k: v
                for k, v in metadata.items()
                if v["trigger_type"] == "http"
            }

            http_entry_points = {
                k: v["entry_point"]
                for k, v in http_functions.items()
            }

            save_json(
                http_functions,
                f"{PROJECT_ID}_http_functions.json"
            )

            save_json(
                http_entry_points,
                f"{PROJECT_ID}_http_entry_points.json"
            )

            # ---------------- FIRESTORE ----------------

            firestore_functions = {
                k: v
                for k, v in metadata.items()
                if v["trigger_type"] == "firestore"
            }

            firestore_entry_points = {
                k: v["entry_point"]
                for k, v in firestore_functions.items()
            }

            save_json(
                firestore_functions,
                f"{PROJECT_ID}_firestore_functions.json"
            )

            save_json(
                firestore_entry_points,
                f"{PROJECT_ID}_firestore_entry_points.json"
            )

            log("\n🎉 ALL DONE")

            self.status_var.set(
                "Completed"
            )

        except Exception as e:

            log(f"\n❌ Fatal Error: {e}")

            self.status_var.set("Error")

        finally:

            self.start_btn.config(
                state="normal"
            )


# =========================================================
#                       MAIN
# =========================================================

def main():

    root = tk.Tk()

    App(root)

    root.mainloop()


if __name__ == "__main__":
    main()

