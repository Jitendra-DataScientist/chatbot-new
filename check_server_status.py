"""
Check if Flask server is running and on which port
"""

import socket
import subprocess
import sys

def check_port(port):
    """Check if a port is open"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    result = sock.connect_ex(('localhost', port))
    sock.close()
    return result == 0

def find_flask_process():
    """Find Flask/Python processes running"""
    try:
        if sys.platform == 'win32':
            # Windows
            result = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
            lines = result.stdout.split('\n')

            print("Active connections on common Flask ports:")
            print("-" * 80)
            for line in lines:
                if 'LISTENING' in line and ('5000' in line or '8080' in line or '3000' in line):
                    print(line.strip())

            print("\n" + "=" * 80)
            print("Python processes:")
            print("-" * 80)
            result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq python.exe'], capture_output=True, text=True)
            print(result.stdout)

    except Exception as e:
        print(f"Error checking processes: {e}")

def main():
    print("=" * 80)
    print("SERVER STATUS CHECK")
    print("=" * 80)
    print()

    # Check common Flask ports
    ports = [5000, 8080, 3000, 8000, 5001]

    print("Checking common Flask ports:")
    print("-" * 80)

    open_ports = []
    for port in ports:
        is_open = check_port(port)
        status = "✅ OPEN" if is_open else "❌ CLOSED"
        print(f"Port {port}: {status}")
        if is_open:
            open_ports.append(port)

    print()

    if open_ports:
        print(f"✅ Found server(s) running on port(s): {open_ports}")
        print()
        print("To test with correct port, update test_phase2_api.py:")
        print(f'   BASE_URL = "http://localhost:{open_ports[0]}"')
    else:
        print("❌ No Flask server found on common ports")
        print()
        print("To start the server:")
        print("   1. Open a new terminal")
        print("   2. Activate venv: .\\venv\\Scripts\\activate")
        print("   3. Run: python app.py")
        print("   4. Look for output like: 'Running on http://127.0.0.1:5000'")

    print()
    print("=" * 80)
    find_flask_process()
    print("=" * 80)

if __name__ == "__main__":
    main()
