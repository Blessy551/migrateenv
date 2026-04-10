#!/usr/bin/env python3
"""
Quick diagnostic script to test MigrateEnv server connectivity.
Run this BEFORE running inference.py to identify connection issues.

Usage:
    python test_connection.py --host https://blessy-karen-migrateenv.hf.space
"""
import sys
import argparse
import httpx
from urllib.parse import urljoin

def test_endpoint(host: str, endpoint: str, method: str = "GET", timeout: float = 10.0) -> tuple[bool, str]:
    """Test if an endpoint is accessible."""
    url = urljoin(host, endpoint)
    try:
        if method == "GET":
            r = httpx.get(url, timeout=timeout)
        else:
            r = httpx.post(url, json={}, timeout=timeout)
        return True, f"✓ {method} {endpoint} → {r.status_code}"
    except httpx.ConnectError as e:
        return False, f"✗ {method} {endpoint} → Connection refused: {e}"
    except httpx.ReadTimeout:
        return False, f"✗ {method} {endpoint} → Timeout after {timeout}s"
    except Exception as e:
        return False, f"✗ {method} {endpoint} → {type(e).__name__}: {e}"

def main():
    parser = argparse.ArgumentParser(description="Test MigrateEnv server connectivity")
    parser.add_argument("--host", default="http://localhost:8000", help="Server URL")
    parser.add_argument("--timeout", type=float, default=10.0, help="Request timeout in seconds")
    args = parser.parse_args()

    host = args.host.rstrip("/")
    
    print(f"\n{'='*70}")
    print(f"  MigrateEnv Connection Diagnostic")
    print(f"{'='*70}")
    print(f"\nTesting server at: {host}\n")

    # Test endpoints in order
    endpoints = [
        ("GET", "/health"),
        ("GET", "/tasks"),
        ("POST", "/reset"),
        ("POST", "/step"),
    ]

    results = []
    for method, endpoint in endpoints:
        success, message = test_endpoint(host, endpoint, method, args.timeout)
        results.append(success)
        print(message)

    # Summary
    print(f"\n{'='*70}")
    if all(results):
        print("✓ All endpoints are accessible!")
        print("\nYou can now run inference:")
        print(f"  python inference.py --host {host}\n")
    else:
        print("✗ Some endpoints are not accessible.")
        print("\nTroubleshooting steps:")
        print(f"  1. Check the URL is correct: {host}")
        print(f"  2. Verify the server is running")
        print(f"  3. For HF Spaces, ensure it's not in sleep mode (watch the Space URL in a browser)")
        print(f"  4. Check your network connection: curl -I {host}")
        print(f"  5. If using HF Spaces with auth, ensure HF_TOKEN is set")
        print(f"  6. Increase timeout with --timeout 30 if network is slow\n")
        sys.exit(1)

    print(f"{'='*70}\n")

if __name__ == "__main__":
    main()
