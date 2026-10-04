"""
Generates a SHA-256 password hash to put in your .env file as
ADMIN_PASSWORD_HASH. This means your real admin password is never
written anywhere in plaintext — not in .env, not in the repo.

Usage:
    python -m scripts.generate_admin_password_hash
    (then paste the printed hash into your .env file)
"""

import getpass
import hashlib


def main():
    password = getpass.getpass("Enter the admin password to hash (input hidden): ")
    if not password:
        print("No password entered — nothing generated.")
        return

    hashed = hashlib.sha256(password.encode("utf-8")).hexdigest()
    print("\nAdd this line to your .env file:")
    print(f"ADMIN_PASSWORD_HASH={hashed}")


if __name__ == "__main__":
    main()
