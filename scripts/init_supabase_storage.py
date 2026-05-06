#!/usr/bin/env python3
"""
Initialize Supabase Storage for the Creative Automation Pipeline.

Creates the `creative-assets` bucket if it doesn't exist.

Usage:
    python3 scripts/init_supabase_storage.py

Requires .env with:
    SUPABASE_URL=https://cllqahmtyvdcbyyrouxx.supabase.co
    SUPABASE_SERVICE_ROLE_KEY=<service_role secret>
"""
import os
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

# Load .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_SERVICE_KEY", ""))
BUCKET_NAME = "creative-assets"


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
        sys.exit(1)

    if not SUPABASE_URL.startswith("https://"):
        print(f"ERROR: SUPABASE_URL looks wrong: {SUPABASE_URL!r}")
        print("       Expected: https://cllqahmtyvdcbyyrouxx.supabase.co")
        sys.exit(1)

    from supabase import create_client
    client = create_client(SUPABASE_URL, SUPABASE_KEY)

    # Check existing buckets
    try:
        buckets = client.storage.list_buckets()
        bucket_names = [b.name if hasattr(b, "name") else b.get("name", "") for b in (buckets or [])]
        print(f"Existing buckets: {bucket_names}")
    except Exception as e:
        print(f"ERROR listing buckets: {e}")
        sys.exit(1)

    if BUCKET_NAME in bucket_names:
        print(f"✓ Bucket '{BUCKET_NAME}' already exists — nothing to do.")
        return

    # Create bucket
    try:
        client.storage.create_bucket(BUCKET_NAME, options={"public": True})
        print(f"✓ Created bucket '{BUCKET_NAME}' (public=True)")
    except Exception as e:
        print(f"ERROR creating bucket: {e}")
        sys.exit(1)

    # Verify
    buckets_after = client.storage.list_buckets()
    names_after = [b.name if hasattr(b, "name") else b.get("name", "") for b in (buckets_after or [])]
    if BUCKET_NAME in names_after:
        print(f"✓ Verified: bucket '{BUCKET_NAME}' is live.")
    else:
        print(f"WARNING: bucket not found after creation. Check Supabase dashboard.")


if __name__ == "__main__":
    main()
