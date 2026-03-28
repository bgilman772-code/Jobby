"""Import local resume and cover-template files into the app uploads folder.

Usage:
  python import_files.py --resume PATH_TO_RESUME [--cover PATH_TO_COVER_TEMPLATE]

This copies the files into the `uploads/` directory with prefixes the app expects.
"""
import argparse
import shutil
import os


def main():
    parser = argparse.ArgumentParser(description='Import resume and cover template into app uploads')
    parser.add_argument('--resume', required=True, help='Path to your resume file')
    parser.add_argument('--cover', help='Path to your cover template file (optional)')
    args = parser.parse_args()

    base_dir = os.path.dirname(__file__)
    uploads = os.path.join(base_dir, 'uploads')
    os.makedirs(uploads, exist_ok=True)

    def copy_with_prefix(path, prefix):
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        name = os.path.basename(path)
        dest = os.path.join(uploads, f"{prefix}_{name}")
        shutil.copy2(path, dest)
        return dest

    resume_dest = copy_with_prefix(args.resume, 'resume')
    print(f'Copied resume to: {resume_dest}')

    if args.cover:
        cover_dest = copy_with_prefix(args.cover, 'cover_template')
        print(f'Copied cover template to: {cover_dest}')


if __name__ == '__main__':
    main()
