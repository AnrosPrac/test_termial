from pymongo import MongoClient
import uuid
client = MongoClient("mongodb+srv://arshad:sidhi6532@parseonix.hyufpej.mongodb.net/")  # or your URI

source_col = client["sheepswag"]["training_samples_filtered"]   # change if needed
target_col = client["lumetrics_db"]["course_questions"]

count = 0

for doc in source_col.find():
    doc.pop("_id", None)

    # ✅ Generate unique question_id if missing or null
    if not doc.get("question_id"):
        doc["question_id"] = f"Q_{uuid.uuid4().hex}"

    target_col.insert_one(doc)
    count += 1

print(f"✅ Migrated {count} documents with new _id")
