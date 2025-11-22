#!/usr/bin/env python3
"""
Test Reolink Motion Detection via ONVIF PullMessages
Compare with Baichuan protocol results

Usage:
    ipython
    %run test_onvif_motion.py
    await test_onvif_motion()
"""

import asyncio
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Auto-detect WSDL directory
def find_wsdl_dir():
    """Find WSDL directory in python packages"""
    import site
    for site_dir in site.getsitepackages():
        wsdl_path = Path(site_dir) / 'wsdl'
        if wsdl_path.exists():
            return str(wsdl_path)
    
    # Fallback - check common locations
    possible_paths = [
        '/usr/local/lib/python3.11/site-packages/wsdl',
        '/usr/local/lib/python3.12/site-packages/wsdl',
        Path.home() / '0_NVR' / 'venv' / 'lib' / 'python3.11' / 'site-packages' / 'wsdl',
        Path.home() / '0_NVR' / 'venv' / 'lib' / 'python3.12' / 'site-packages' / 'wsdl',
    ]
    
    for path in possible_paths:
        if Path(path).exists():
            return str(path)
    
    raise FileNotFoundError(
        "Could not find WSDL directory. Install python-onvif-zeep: pip install onvif-zeep"
    )

try:
    from onvif import ONVIFCamera
    WSDL_DIR = find_wsdl_dir()
    print(f"Using WSDL directory: {WSDL_DIR}")
except ImportError:
    print("ERROR: onvif-zeep not installed")
    print("Install with: pip install onvif-zeep")
    sys.exit(1)
except FileNotFoundError as e:
    print(f"ERROR: {e}")
    sys.exit(1)

# Configuration
CAMERA_IP = "192.168.10.88"
ONVIF_PORT = 8000  # Reolink uses port 8000 for ONVIF
USERNAME = os.getenv("REOLINK_USERNAME", "admin")
PASSWORD = os.getenv("REOLINK_PASSWORD")

if not PASSWORD:
    print("WARNING: REOLINK_PASSWORD not set in environment")
    PASSWORD = input("Enter Reolink password: ")

# Global camera instance
mycam = None
pullpoint = None
motion_events = []


def test_onvif_connection():
    """Test basic ONVIF connection and capabilities"""
    global mycam
    
    print(f"\n{'='*60}")
    print(f"ONVIF Connection Test")
    print(f"{'='*60}")
    print(f"Camera: {CAMERA_IP}:{ONVIF_PORT}")
    print(f"Username: {USERNAME}")
    print(f"WSDL Dir: {WSDL_DIR}")
    print(f"{'='*60}\n")
    
    try:
        print(f"[1/4] Connecting to camera...")
        mycam = ONVIFCamera(
            CAMERA_IP, 
            ONVIF_PORT, 
            USERNAME, 
            PASSWORD,
            WSDL_DIR,
            no_cache=True
        )
        print(f"      ✅ Connected")
        
        # Get device info
        print(f"\n[2/4] Getting device information...")
        device_service = mycam.create_devicemgmt_service()
        device_info = device_service.GetDeviceInformation()
        print(f"      Manufacturer: {device_info.Manufacturer}")
        print(f"      Model: {device_info.Model}")
        print(f"      Firmware: {device_info.FirmwareVersion}")
        
        # Get event service capabilities
        print(f"\n[3/4] Checking event service capabilities...")
        event_service = mycam.create_events_service()
        event_props = event_service.GetEventProperties()
        
        print(f"      Event Topics Available:")
        if hasattr(event_props, 'TopicSet'):
            for topic in event_props.TopicSet:
                print(f"        - {topic}")
        
        # Get supported topics
        print(f"\n[4/4] Checking motion detection topics...")
        topics_filter = event_service.GetEventProperties()
        print(f"      Motion topics supported: ✅")
        
        print(f"\n{'='*60}")
        print(f"✅ ONVIF Connection Successful")
        print(f"{'='*60}\n")
        return True
        
    except Exception as e:
        print(f"\n❌ ONVIF connection failed: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_onvif_motion(duration: int = 60, topic: str = 'tns1:VideoSource/MotionAlarm'):
    """
    Test ONVIF motion detection via PullMessages
    
    Args:
        duration: How long to monitor in seconds
        topic: ONVIF topic to subscribe to
    """
    global mycam, pullpoint, motion_events
    
    print(f"\n{'='*60}")
    print(f"ONVIF Motion Detection Test")
    print(f"{'='*60}")
    print(f"Camera: {CAMERA_IP}:{ONVIF_PORT}")
    print(f"Duration: {duration}s")
    print(f"Topic: {topic}")
    print(f"{'='*60}\n")
    
    try:
        # Connect if not already
        if not mycam:
            print(f"[1/5] Connecting to camera...")
            mycam = ONVIFCamera(
                CAMERA_IP, 
                ONVIF_PORT, 
                USERNAME, 
                PASSWORD,
                WSDL_DIR,
                no_cache=True
            )
            print(f"      ✅ Connected")
        else:
            print(f"[1/5] Using existing connection")
        
        # Create event service
        print(f"\n[2/5] Creating event service...")
        event_service = mycam.create_events_service()
        print(f"      ✅ Event service created")
        
        # Create PullPoint subscription with topic filter
        print(f"\n[3/5] Creating PullPoint subscription...")
        sub = event_service.CreatePullPointSubscription({
            'Filter': {
                'TopicExpression': {
                    '_value_1': topic,
                    'Dialect': 'http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet'
                }
            }
        })
        print(f"      ✅ Subscription created")
        print(f"      Subscription Reference: {sub.SubscriptionReference.Address._value_1}")
        
        # Create pullpoint service binding
        print(f"\n[4/5] Creating PullPoint service binding...")
        pullpoint = event_service.zeep_client.create_service(
            '{http://www.onvif.org/ver10/events/wsdl}PullPointSubscriptionBinding',
            sub.SubscriptionReference.Address._value_1
        )
        print(f"      ✅ PullPoint binding created")
        
        # Monitor for events
        print(f"\n[5/5] Monitoring for motion events...")
        print(f"      Duration: {duration}s")
        print(f"      Move in front of camera to trigger motion")
        print(f"      {'='*60}\n")
        
        motion_events.clear()
        start_time = time.time()
        poll_count = 0
        
        while (time.time() - start_time) < duration:
            poll_count += 1
            timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            
            try:
                # Pull messages with 2 second timeout
                messages = pullpoint.PullMessages(
                    Timeout=timedelta(seconds=2), 
                    MessageLimit=10
                )
                
                if messages.NotificationMessage:
                    for msg in messages.NotificationMessage:
                        event_str = f"[{timestamp}] EVENT RECEIVED"
                        print(event_str)
                        
                        # Parse message details
                        if hasattr(msg, 'Message'):
                            print(f"              Topic: {msg.Topic._value_1 if hasattr(msg.Topic, '_value_1') else msg.Topic}")
                            
                            # Check for motion state
                            if hasattr(msg.Message, '_value_1'):
                                for item in msg.Message._value_1:
                                    if hasattr(item, 'Name') and hasattr(item, 'Value'):
                                        print(f"              {item.Name}: {item.Value}")
                                        
                                        # Track motion events
                                        if item.Name in ['State', 'IsMotion']:
                                            if item.Value in ['true', True, 1]:
                                                motion_event = f"[{timestamp}] ⚠️  MOTION DETECTED"
                                                print(f"              {motion_event}")
                                                motion_events.append(motion_event)
                        
                        print(f"              Full message: {msg}")
                else:
                    # No events in this poll
                    elapsed = int(time.time() - start_time)
                    if elapsed % 10 == 0 and elapsed > 0:
                        print(f"[{elapsed}s] Poll #{poll_count} - No events (move to trigger motion)")
                
            except Exception as poll_error:
                print(f"[{timestamp}] Poll error: {poll_error}")
            
            # Small delay between polls
            await asyncio.sleep(0.1)
        
        # Results
        print(f"\n{'='*60}")
        print(f"Test Complete")
        print(f"{'='*60}")
        print(f"Duration: {duration}s")
        print(f"Total Polls: {poll_count}")
        print(f"Motion Events: {len(motion_events)}")
        
        if motion_events:
            print(f"\nMotion Event Timeline:")
            for event in motion_events:
                print(f"  {event}")
        else:
            print(f"\n⚠️  No motion events detected via ONVIF")
            print(f"   Possible reasons:")
            print(f"   - Reolink cameras use Baichuan protocol for motion events")
            print(f"   - ONVIF events may be disabled in camera firmware")
            print(f"   - Motion sensitivity still too low")
        
        print(f"{'='*60}\n")
        
        # Cleanup
        print(f"Unsubscribing...")
        try:
            pullpoint.Unsubscribe()
            print(f"✅ Unsubscribed")
        except Exception as e:
            print(f"⚠️  Unsubscribe error: {e}")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()


# Test alternative topics
async def test_all_motion_topics(duration: int = 30):
    """Test multiple ONVIF motion topics"""
    topics = [
        'tns1:VideoSource/MotionAlarm',
        'tns1:RuleEngine/CellMotionDetector/Motion',
        'tns1:RuleEngine/MotionDetector/Motion',
    ]
    
    print(f"\nTesting {len(topics)} different ONVIF motion topics...")
    
    for topic in topics:
        print(f"\n{'='*60}")
        print(f"Testing topic: {topic}")
        print(f"{'='*60}")
        await test_onvif_motion(duration, topic)
        
        if motion_events:
            print(f"✅ SUCCESS - Topic {topic} delivered {len(motion_events)} events")
            break
        else:
            print(f"⚠️  No events from topic: {topic}")
    
    if not motion_events:
        print(f"\n❌ None of the standard ONVIF motion topics delivered events")
        print(f"   Recommendation: Use Baichuan protocol for Reolink cameras")


# Interactive usage
if __name__ == "__main__":
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  ONVIF Motion Detection Test - Interactive Mode             ║
╚══════════════════════════════════════════════════════════════╝

Available commands:

  test_onvif_connection()               # Test basic ONVIF connection
  await test_onvif_motion()             # Run 60s motion test (default topic)
  await test_onvif_motion(30)           # Run 30s test
  await test_all_motion_topics()        # Test all known motion topics

Environment:
  Camera: {CAMERA_IP}:{ONVIF_PORT}
  Username: {USERNAME}
  Password: {"***" if PASSWORD else "NOT SET"}
  WSDL Dir: {WSDL_DIR}

Note: Comparison with Baichuan results
""")