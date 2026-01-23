from threading import Thread
import os
import time
import signal
import subprocess
import socket
import traceback
from services.camera_repository import CameraRepository
from low_level_handlers.process_reaper import kill_processes_by_pattern, reap_child_processes


"""
    Orchestrates services and low-level system cleanup
    Elegant terminations
    Then Nuking in case some stuff didn't stop.
"""

######################### -#########################
#              ⚙️SERVICES STOPPERS⚙️
######################### -#########################
def stop_all_services(stream_manager,
                      bridge_watchdog,
                      eufy_bridge,
                      unifi_cameras,
                      unifi_resource_monitor,
                      unifi_mjpeg_capture_service,
                      reolink_mjpeg_capture_service=None,
                      amcrest_mjpeg_capture_service=None):
    try:
        stream_manager.s = Thread(
            target=stop_all_streaming_watchdogs, args=(stream_manager,))
        stream_manager.s.start()

        resource_monitor_thread = Thread(
            target=stop_resource_monitor, args=(unifi_cameras, unifi_resource_monitor))
        resource_monitor_thread.start()

        # best to wait for these threads to be joined before moving on
        stream_manager.s.join(timeout=20)
        resource_monitor_thread.join(timeout=20)

        # free bridge port (only if eufy_bridge is configured)
        if eufy_bridge:
            Thread(target=free_eufy_bridge_port, args=(eufy_bridge,)).start()

        # stop remaining services
        Thread(target=stop_all_streams, args=(stream_manager,)).start()
        Thread(target=stop_mjpeg_capture, args=(
            unifi_mjpeg_capture_service,)).start()
        Thread(target=stop_unifiy_cameras_session,
               args=(unifi_cameras,)).start()
    except:
        print(traceback.print_exc())
        raise Exception(f"❌ stop_all_services error")
######################### -#########################


def stop_all_streaming_watchdogs(stream_manager):
    print("[[[[[[stop_all_streaming_watchdogs]]]]]]")
    for camera_serial in list(stream_manager.watchdogs.keys()):

        try:
            stream_manager.stop_watchdog = True
        except:
            print(traceback.print_exc())
            raise Exception(
                f"❌ FAILED to stop watchdog for stream:{camera_id}")

        print(f"✅ watchdog for stream {camera_serial} stopped")


def free_eufy_bridge_port(eufy_bridge):
    print("[[[[[[free_eufy_bridge_port]]]]]]")
    if not eufy_bridge:
        print("⏭️  No eufy_bridge configured, skipping port cleanup")
        return
    # Verify port is freed
    for attempt in range(50):
        print("[[[[[[free_eufy_bridge_port]]]]]]")
        try:
            bridge_port = getattr(eufy_bridge, 'port', 3000)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', bridge_port))
            sock.close()
            if result != 0:
                print(f"✅ Port {bridge_port} freed")
                break
            else:
                print(f"⚠️  Port {bridge_port} still in use, waiting...")
                time.sleep(1)
        except:
            break


def stop_resource_monitor(unifi_cameras, unifi_resource_monitor):
    print("[[[[[[stop_resource_monitor]]]]]]")
    try:
        if unifi_cameras:
            unifi_resource_monitor.stop_monitoring()
    except:
        print(traceback.print_exc())
        raise Exception(f"Resource monitoring Cleanup error")

    print("✅ unifi_resource_monitor stopped")


def stop_mjpeg_capture(unifi_mjpeg_capture_service):
    print("[[[[[[stop_mjpeg_capture]]]]]]")
    try:
        unifi_mjpeg_capture_service.cleanup()
    except:
        raise Exception
        print(f"Resource monitoring Cleanup error")

    print("✅ MJPEG capture service stopped")


def stop_unifiy_cameras_session(unifi_cameras):
    print("[[[[[[stop_unifiy_cameras_session]]]]]]")
    # Clean up UniFi camera sessions
    for camera_id, camera in unifi_cameras.items():
        try:
            camera.cleanup()
        except:
            print(traceback.print_exc())
            raise Exception(f"❌ Error cleaning up camera {camera_id}")

    print("✅ UniFi camera sessions cleaned up")


def stop_all_streams(stream_manager):
    print("[[[[[[stop_all_streams]]]]]]")
    # Stop all streams
    try:
        stream_manager.stop_all_streams()  # no longer nukes hls files.
    except:
        print(f"Error stopping streams")
        print(traceback.print_exc())
        raise Exception(f"❌ stop_all_streams failed")

    print("✅ All streams stopped")


def stop_bridge(eufy_bridge, bridge_watchdog):
    print("[[[[[[stop_bridge]]]]]]")
    # Stop bridge
    try:
        if bridge_watchdog:
            bridge_watchdog.stop()
        if eufy_bridge:
            eufy_bridge.stop()
    except:
        print(traceback.print_exc())
        raise Exception(f"❌ Error stopping bridge")

    print("✅ Bridge stopped")
######################### -#########################

######################### -#########################
#               ☢️PROCESS KILLERS☢️
######################### -#########################
def kill_all(eufy_bridge=None, stream_manager=None):
    try:
        if eufy_bridge:
            Thread(target=kill_eufy_bridge, args=(eufy_bridge,)).start()

        if stream_manager:
            try:
                # Using CameraRepository
                camera_repo = CameraRepository()
                all_cameras_serials = list(camera_repo.get_all_cameras().keys())
                print(all_cameras_serials)
                for camera_serial in all_cameras_serials:
                    print(f"terminating::::::::::::::::::::::::::::: {camera_serial}")
                    try:
                        if stream_manager._kill_all_ffmpeg_for_camera(camera_serial):
                            print(f"Stream {camera_serial} terminated.")
                        else:
                            print(f"Failed to stop stream for {camera_serial}")
                    except Exception as e:
                        print(traceback.print_exc())
                        print(e)

            except Exception as e:
                print(e)
                print(traceback.print_exc())
        else:
            print("*************************-*************************")
            print("                  NO STREAM MANAGER                ")
            print("*************************-*************************")

        # Nuke everything left out.
        print("*************************-*************************")
        print("                  ☢️💥☢️☢️💥☢️                  ")
        print("*************************-*************************")

        Thread(target=kill_ffmpeg).start()
    except Exception as e:
        print("######################### -#########################")
        print("            ☢️ NUKE WAS A DUD... 🥴                ")
        print("######################### -#########################")
        print(traceback.print_exc())
        sys.exit(1)
    finally:
        if stream_manager:

            stream_manager.cleanup_stream_files

######################### -#########################


def kill_eufy_bridge(eufy_bridge):
    # Force kill any remaining bridge processes
    for attempt in range(50):
        try:
            subprocess.run(['pkill', '-f', 'eufy-security-server'],
                           stderr=subprocess.DEVNULL)
        except:
            print(traceback.print_exc())
            raise Exception(f"❌ eufy-security-server Cleanup error")


def kill_ffmpeg():
    """Kill all FFmpeg processes using the reaper utility"""
    for attempt in range(50):
        print("☢️" * 11)
        
        if kill_processes_by_pattern('ffmpeg.*-rtsp', signal_type=signal.SIGKILL):
            print("✅ No ffmpeg processes left")
            # Reap any zombies
            reaped = reap_child_processes()
            if reaped > 0:
                print(f"✅ Reaped {reaped} zombie process(es)")
            break
        
        time.sleep(0.1)


######################### -#########################
