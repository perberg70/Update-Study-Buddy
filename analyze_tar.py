import tarfile
import os

def analyze_tar(file_path):
    print(f"Opening {file_path}...")
    with tarfile.open(file_path, "r:gz") as tar:
        # List all files and save to a text file for inspection
        with open("tar_contents.txt", "w", encoding="utf-8") as f:
            for member in tar.getmembers():
                f.write(f"{member.name}\n")
    print("Files listed in tar_contents.txt")

if __name__ == "__main__":
    analyze_tar("course.6gehwzol.tar.gz")
