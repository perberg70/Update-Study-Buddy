import argparse
import sys
import tarfile

from config import resolve_tar_path


def analyze_tar(file_path):
    print(f"Opening {file_path}...")
    with tarfile.open(file_path, "r:gz") as tar:
        # List all files and save to a text file for inspection
        with open("tar_contents.txt", "w", encoding="utf-8") as f:
            for member in tar.getmembers():
                f.write(f"{member.name}\n")
    print("Files listed in tar_contents.txt")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="List contents of edX course .tar.gz")
    parser.add_argument(
        "--tar",
        dest="tar_path",
        default=None,
        help="Path to .tar.gz (default: EDX_TAR_PATH or newest course*.tar.gz in project)",
    )
    args = parser.parse_args()
    try:
        path = resolve_tar_path(args.tar_path)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    analyze_tar(path)
