#!/usr/bin/env python3
"""
Test Reolink Motion Detection via Baichuan Protocol
Interactive test script for ipython

Usage:
    ipython -i test_reolink_motion.py
    # Then run: await test_motion_detection()
"""

import asyncio
import logging
import os
from datetime import datetime
from reolink_aio.api import Host

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
CAMERA_IP = "192.168.10.88"  # Reolink_Driveway
USERNAME = os.getenv("REOLINK_USERNAME", "admin")
PASSWORD = os.getenv("REOLINK_PASSWORD")

if not PASSWORD:
    print("WARNING: REOLINK_PASSWORD not set in environment")
    print("Set it with: export REOLINK_PASSWORD='your_password'")
    PASSWORD = input("Enter Reolink password: ")

# Global host instance for interactive use
host = None
motion_events = []


callback_count = 0

def motion_callback():
    """
    Callback function called on ANY Baichuan push event.
    State variables in host object are automatically updated.
    """
    global callback_count
    callback_count += 1
    
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    
    # DEBUG: Log that callback fired
    print(f"[{timestamp}] 🔔 Callback #{callback_count} fired")
    
    # Check motion state for channel 0
    if host and host.motion_detected(0):
        event = f"[{timestamp}] ⚠️  MOTION DETECTED on channel 0"
        print(event)
        motion_events.append(event)
        logger.info(f"Motion detected - total events: {len(motion_events)}")
    else:
        # DEBUG: Show when callback fires but no motion
        print(f"[{timestamp}]    No motion (callback fired but motion_detected=False)")
    
    # Check AI detection if supported
    if host:
        if host.ai_detected(0, "person"):
            event = f"[{timestamp}] 👤 PERSON DETECTED on channel 0"
            print(event)
            motion_events.append(event)
        
        if host.ai_detected(0, "vehicle"):
            event = f"[{timestamp}] 🚗 VEHICLE DETECTED on channel 0"
            print(event)
            motion_events.append(event)
        
        if host.ai_detected(0, "pet"):
            event = f"[{timestamp}] 🐕 PET DETECTED on channel 0"
            print(event)
            motion_events.append(event)


async def test_motion_detection(duration: int = 60):
    """
    Test motion detection for specified duration.
    
    Args:
        duration: How long to monitor in seconds (default 60)
    """
    global host, motion_events, callback_count
    
    callback_count = 0  # Reset counter
    
    print(f"\n{'='*60}")
    print(f"Reolink Motion Detection Test")
    print(f"{'='*60}")
    print(f"Camera: {CAMERA_IP}")
    print(f"Username: {USERNAME}")
    print(f"Duration: {duration}s")
    print(f"{'='*60}\n")
    
    try:
        # Initialize host
        print(f"[1/5] Connecting to camera...")
        host = Host(host=CAMERA_IP, username=USERNAME, password=PASSWORD)
        
        # Get device info
        print(f"[2/5] Fetching device capabilities...")
        await host.get_host_data()
        
        print(f"[3/5] Device Info:")
        print(f"      Model: {host.camera_model(0) if hasattr(host, 'camera_model') else 'Unknown'}")
        print(f"      Name: {host.camera_name(0) if hasattr(host, 'camera_name') else 'Unknown'}")
        print(f"      Channels: {host.num_channels if hasattr(host, 'num_channels') else 'Unknown'}")
        print(f"      Firmware: {host.camera_sw_version(0) if hasattr(host, 'camera_sw_version') else 'Unknown'}")
        
        # Register callback and subscribe
        print(f"\n[4/5] Registering callback and subscribing to Baichuan events...")
        host.baichuan.register_callback("test_motion", motion_callback)
        await host.baichuan.subscribe_events()
        print(f"      ✅ Subscribed successfully")
        
        # Check motion detection settings
        print(f"\n[4.5/5] Checking motion detection configuration...")
        try:
            motion_state = await host.get_motion_state(0)
            print(f"      Motion State: {motion_state}")
            print(f"      MD Sensitivity: {host.md_sensitivity(0)}")
            print(f"      Currently Detecting Motion: {host.motion_detected(0)}")
        except Exception as e:
            print(f"      ⚠️  Could not retrieve motion state: {e}")
        
        # Monitor for motion
        print(f"\n[5/5] Monitoring for motion events...")
        print(f"      Duration: {duration}s")
        print(f"      Move in front of camera to trigger motion detection")
        print(f"      {'='*60}\n")
        
        # Wait for events
        motion_events.clear()
        start_time = datetime.now()
        
        for remaining in range(duration, 0, -1):
            await asyncio.sleep(1)
            if remaining % 10 == 0:
                elapsed = (datetime.now() - start_time).seconds
                print(f"[{elapsed}s] Still monitoring... ({len(motion_events)} events detected)")
        
        # Results
        print(f"\n{'='*60}")
        print(f"Test Complete")
        print(f"{'='*60}")
        print(f"Duration: {duration}s")
        print(f"Callback Invocations: {callback_count}")
        print(f"Motion Events Detected: {len(motion_events)}")
        
        if callback_count == 0:
            print(f"\n⚠️  Callback NEVER fired - Baichuan events not being received")
            print(f"   Possible causes:")
            print(f"   - Camera firmware doesn't send Baichuan pushes")
            print(f"   - Motion detection disabled in camera")
            print(f"   - Network/firewall blocking TCP push events")
        elif motion_events:
            print(f"\nEvent Timeline:")
            for event in motion_events:
                print(f"  {event}")
        else:
            print(f"\n⚠️  Callbacks fired but no motion events")
            print(f"   - Callback was invoked {callback_count} times")
            print(f"   - But motion_detected() always returned False")
            print(f"   Recommendations:")
            print(f"   - Verify motion detection is enabled in camera settings")
            print(f"   - Ensure sensitivity is not too low")
            print(f"   - Try moving in front of the camera")
        
        print(f"{'='*60}\n")
        
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        print(f"\n❌ ERROR: {e}")
        
    finally:
        # Cleanup
        if host:
            print(f"Unsubscribing and disconnecting...")
            try:
                await host.baichuan.unsubscribe_events()
                await host.logout()
                print(f"✅ Disconnected")
            except Exception as e:
                logger.error(f"Cleanup error: {e}")


async def quick_status_check():
    """Quick connection test without monitoring"""
    global host
    
    try:
        print(f"Connecting to {CAMERA_IP}...")
        host = Host(host=CAMERA_IP, username=USERNAME, password=PASSWORD)
        await host.get_host_data()
        
        print(f"\n✅ Connection successful!")
        print(f"Camera: {host.camera_name(0) if hasattr(host, 'camera_name') else 'Unknown'}")
        print(f"Model: {host.camera_model(0) if hasattr(host, 'camera_model') else 'Unknown'}")
        print(f"Firmware: {host.camera_sw_version(0) if hasattr(host, 'camera_sw_version') else 'Unknown'}")
        
        await host.logout()
        
    except Exception as e:
        print(f"❌ Connection failed: {e}")


def list_host_methods():
    """List available methods on Host object for debugging"""
    if not host:
        print("No host connected. Run await quick_status_check() first.")
        return
    
    print("\nHost Object Methods (motion/detection related):")
    methods = [m for m in dir(host) if 'motion' in m.lower() or 'detect' in m.lower() or 'ai' in m.lower()]
    for method in sorted(methods):
        if not method.startswith('_'):
            print(f"  - {method}")
    
    print("\nAll non-private methods:")
    all_methods = [m for m in dir(host) if not m.startswith('_') and callable(getattr(host, m, None))]
    for method in sorted(all_methods):
        print(f"  - {method}")


# Interactive usage instructions
if __name__ == "__main__":
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  Reolink Motion Detection Test - Interactive Mode           ║
╚══════════════════════════════════════════════════════════════╝

Available commands:

  await test_motion_detection()         # Run 60s motion test
  await test_motion_detection(120)      # Run 120s motion test
  await quick_status_check()            # Quick connection test
  list_host_methods()                   # List available Host methods (after connection)

Environment:
  Camera: {CAMERA_IP}
  Username: {USERNAME}
  Password: {"***" if PASSWORD else "NOT SET"}

Note: Run commands with 'await' prefix in ipython/asyncio REPL
""")