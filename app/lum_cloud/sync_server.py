import os
import shutil
from git import Repo
import threading

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_URL = os.getenv("GITHUB_REPO_URL")
LOCAL_REPO_DIR = os.path.abspath("./vault_storage")
AUTH_REPO_URL = REPO_URL.replace("https://", f"https://{GITHUB_TOKEN}@")

git_lock = threading.Lock()

def setup_repo():
    if not os.path.exists(LOCAL_REPO_DIR):
        repo = Repo.clone_from(AUTH_REPO_URL, LOCAL_REPO_DIR)
        with repo.config_writer() as cw:
            cw.set_value("user", "name", "Lumetrics Engine")
            cw.set_value("user", "email", "engine@lumetrics.ai")

def commit_to_github(sid_id, files):
    with git_lock:
        try:
            repo = Repo(LOCAL_REPO_DIR)
            origin = repo.remote(name='origin')
            origin.pull()

            # Folder naming: user_sidhilynxuserid
            student_rel_path = os.path.join("vault", f"user_{sid_id}")
            student_full_path = os.path.join(LOCAL_REPO_DIR, student_rel_path)
            
            if os.path.exists(student_full_path):
                shutil.rmtree(student_full_path)
            os.makedirs(student_full_path, exist_ok=True)

            for file_path, content in files.items():
                safe_path = os.path.normpath(file_path).lstrip(os.sep)
                full_path = os.path.join(student_full_path, safe_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)

            repo.git.add(student_rel_path)
            if repo.is_dirty(untracked_files=True):
                repo.index.commit(f"Sync: {sid_id}")
                origin.push()
        except Exception as e:
            print(f"[X] Git Critical Error: {e}")