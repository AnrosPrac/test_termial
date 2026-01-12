import os
import shutil
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from git import Repo
import threading

app = FastAPI()

# Configuration (Use Render Environment Variables)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_URL = os.getenv("GITHUB_REPO_URL")
LOCAL_REPO_DIR = "./vault_storage"
# Insert token into URL for auth
AUTH_REPO_URL = REPO_URL.replace("https://", f"https://{GITHUB_TOKEN}@")

# Global Lock to prevent concurrent Git operations (100-student safety)
git_lock = threading.Lock()

def setup_repo():
    if not os.path.exists(LOCAL_REPO_DIR):
        print("[*] Cloning Master Vault...")
        Repo.clone_from(AUTH_REPO_URL, LOCAL_REPO_DIR)
    else:
        print("[*] Vault already exists.")

setup_repo()

@app.post("/sync/push")
async def student_push(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    student_id = data.get("student_id")
    files = data.get("files") # Dict of {path: content}

    if not student_id or not files:
        raise HTTPException(status_code=400, detail="Invalid payload")

    # Start the Sync Worker in the background to keep the CLI responsive
    background_tasks.add_task(commit_to_github, student_id, files)
    
    return {"status": "queued", "message": "Sync initiated"}

def commit_to_github(student_id, files):
    with git_lock:
        try:
            repo = Repo(LOCAL_REPO_DIR)
            origin = repo.remote(name='origin')
            
            # 1. Pull latest to stay in sync with other students' pushes
            origin.pull()

            # 2. Define Student Namespace (Isolation)
            student_folder = os.path.join(LOCAL_REPO_DIR, "vault", f"student_{student_id[:8]}")
            
            # 3. Clean and Write (State-based Sync)
            if os.path.exists(student_folder):
                shutil.rmtree(student_folder)
            os.makedirs(student_folder, exist_ok=True)

            total_size = 0
            for file_path, content in files.items():
                # Prevent path traversal attacks
                safe_path = os.path.normpath(file_path).lstrip(os.sep)
                full_path = os.path.join(student_folder, safe_path)
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(content)
                total_size += len(content.encode('utf-8'))

            # 10MB Enforcement check
            if total_size > 10 * 1024 * 1024:
                print(f"[!] Student {student_id} exceeded 10MB. Aborting.")
                return

            # 4. Commit and Push
            repo.git.add(student_folder)
            if repo.is_dirty():
                repo.index.commit(f"Sync: {student_id[:8]}")
                origin.push()
                print(f"[✔] Pushed updates for {student_id[:8]}")
            
        except Exception as e:
            print(f"[X] Git Error: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)