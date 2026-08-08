#!/usr/bin/env python3
"""
Test script to verify virtual environment deployment works correctly
"""

import subprocess
import sys
import tempfile
from pathlib import Path

def run_command(cmd, cwd=None):
    """Run a command and return success status"""
    try:
        result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)

def test_venv_creation():
    """Test virtual environment creation locally"""
    print("🧪 Testing Virtual Environment Creation")
    print("=" * 40)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"📁 Test directory: {temp_dir}")
        
        # Create requirements.txt
        req_file = Path(temp_dir) / "requirements.txt"
        req_file.write_text("flask\nwerkzeug\n")
        
        # Create virtual environment
        print("🐍 Creating virtual environment...")
        success, stdout, stderr = run_command("python3 -m venv venv", cwd=temp_dir)
        
        if not success:
            print(f"❌ Failed to create venv: {stderr}")
            return False
        
        print("✅ Virtual environment created")
        
        # Test activation and package installation
        print("📦 Installing packages...")
        activate_cmd = "source venv/bin/activate && pip install --upgrade pip && pip install -r requirements.txt"
        success, stdout, stderr = run_command(activate_cmd, cwd=temp_dir)
        
        if not success:
            print(f"❌ Failed to install packages: {stderr}")
            return False
        
        print("✅ Packages installed successfully")
        
        # Test package listing
        print("📋 Checking installed packages...")
        list_cmd = "source venv/bin/activate && pip list"
        success, stdout, stderr = run_command(list_cmd, cwd=temp_dir)
        
        if success and "flask" in stdout.lower():
            print("✅ Flask found in virtual environment")
            print("📦 Installed packages:")
            for line in stdout.split('\n')[:10]:  # Show first 10 lines
                if line.strip():
                    print(f"   {line}")
            return True
        else:
            print(f"❌ Flask not found: {stderr}")
            return False

def test_startup_script():
    """Test startup script creation"""
    print("\n🚀 Testing Startup Script")
    print("=" * 40)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a mock startup script
        start_script = Path(temp_dir) / "start.sh"
        start_script.write_text("""#!/bin/bash
cd $(dirname $0)

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "❌ Virtual environment not found!"
    exit 1
fi

# Activate virtual environment
source venv/bin/activate

# Test Python availability
python --version
echo "✅ Virtual environment activated successfully"
""")
        
        # Make executable
        start_script.chmod(0o755)
        
        # Create venv
        run_command("python3 -m venv venv", cwd=temp_dir)
        
        # Test script execution
        success, stdout, stderr = run_command("./start.sh", cwd=temp_dir)
        
        if success and "Virtual environment activated successfully" in stdout:
            print("✅ Startup script works correctly")
            print(f"📄 Script output: {stdout.strip()}")
            return True
        else:
            print(f"❌ Startup script failed: {stderr}")
            return False

def main():
    """Run all tests"""
    print("🧪 Virtual Environment Deployment Test Suite")
    print("=" * 50)
    
    tests = [
        ("Virtual Environment Creation", test_venv_creation),
        ("Startup Script", test_startup_script),
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ Test '{test_name}' crashed: {e}")
            results.append((test_name, False))
    
    # Summary
    print("\n📋 Test Results")
    print("=" * 40)
    
    passed = 0
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} {test_name}")
        if result:
            passed += 1
    
    print(f"\n🎯 Summary: {passed}/{len(results)} tests passed")
    
    if passed == len(results):
        print("\n🎉 All tests passed! Virtual environment deployment should work correctly.")
        print("\n🚀 Ready to deploy:")
        print("   ./tools/deployment/deploy.sh")
    else:
        print("\n⚠️  Some tests failed. Check your Python/venv setup.")
        return 1
    
    return 0

if __name__ == '__main__':
    sys.exit(main())
