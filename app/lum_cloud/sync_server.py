import os
import shutil
from git import Repo
import threading

# Configuration
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_URL = os.getenv("GITHUB_REPO_URL")
LOCAL_REPO_DIR = os.path.abspath("./vault_storage")
AUTH_REPO_URL = REPO_URL.replace("https://", f"https://{GITHUB_TOKEN}@")

git_lock = threading.Lock()

def setup_repo():
    if not os.path.exists(LOCAL_REPO_DIR):
        print("[*] Cloning Master Vault...")
        repo = Repo.clone_from(AUTH_REPO_URL, LOCAL_REPO_DIR)
        
        # --- CTO FIX: Set Git Identity so commits don't fail ---
        with repo.config_writer() as cw:
            cw.set_value("user", "name", "Lumetrics Engine")
            cw.set_value("user", "email", "engine@lumetrics.ai")
    else:
        print("[*] Vault already exists.")

def commit_to_github(student_id, files):
    """Worker function to handle the Git lifecycle."""
    with git_lock:
        try:
            repo = Repo(LOCAL_REPO_DIR)
            origin = repo.remote(name='origin')
            
            # 1. Pull latest to avoid conflicts
            origin.pull()

            # --- CTO FIX: Use relative paths for Git operations ---
            student_rel_path = os.path.join("vault", f"student_{student_id[:8]}")
            student_full_path = os.path.join(LOCAL_REPO_DIR, student_rel_path)
            
            # 2. Clean and Write (State-based Sync)
            if os.path.exists(student_full_path):
                shutil.rmtree(student_full_path)
            os.makedirs(student_full_path, exist_ok=True)

            total_size = 0
            for file_path, content in files.items():
                safe_path = os.path.normpath(file_path).lstrip(os.sep)
                full_path = os.path.join(student_full_path, safe_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                total_size += len(content.encode('utf-8'))

            # 10MB Enforcement
            if total_size > 10 * 1024 * 1024:
                print(f"[!] Student {student_id[:8]} exceeded 10MB. Aborting.")
                return

            # 3. Commit and Push using RELATIVE path
            repo.git.add(student_rel_path)
            
            if repo.is_dirty(untracked_files=True):
                repo.index.commit(f"Sync: {student_id[:8]}")
                origin.push()
                print(f"[✔] Pushed updates for {student_id[:8]}")
            else:
                print(f"[*] No changes detected for {student_id[:8]}")
            
        except Exception as e:
            print(f"[X] Git Critical Error: {e}")