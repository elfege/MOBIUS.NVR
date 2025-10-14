#!/usr/bin/env python3
"""
UniFi Service Resource Monitor
Monitors for "too many open files" errors and provides automatic recovery
"""

import logging
import time
import threading
import subprocess
import sys
import os
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class UniFiServiceResourceMonitor:
    """Monitor UniFi camera services for resource exhaustion and handle recovery"""
    
    def __init__(self, unifi_cameras_dict, app_restart_callback=None):
        """
        Initialize the resource monitor
        
        Args:
            unifi_cameras_dict: Dictionary of camera_id -> UniFiCameraService instances
            app_restart_callback: Optional callback function for app restart
        """
        self.unifi_cameras = unifi_cameras_dict
        self.app_restart_callback = app_restart_callback
        self.monitoring = False
        self.monitor_thread = None
        
        # Error tracking
        self.error_counts = {}
        self.last_errno24_time = None
        self.restart_count = 0
        self.max_restarts_per_hour = 3
        self.restart_times = []
        
        # File descriptor monitoring
        self.fd_check_interval = 300  # Check every 5 minutes
        self.fd_warning_threshold = 800  # Warn at 800 FDs (limit is typically 1024)
        self.fd_critical_threshold = 950  # Critical at 950 FDs
        
        logger.info(f"UniFi Resource Monitor initialized for {len(self.unifi_cameras)} cameras")
        
    def start_monitoring(self):
        """Start the error monitoring system"""
        if self.monitoring:
            logger.warning("Monitoring already started")
            return
            
        logger.info("Starting UniFi resource monitoring system")
        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
    
    def stop_monitoring(self):
        """Stop the error monitoring system"""
        logger.info("Stopping UniFi resource monitoring system")
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
    
    def _monitor_loop(self):
        """Main monitoring loop"""
        logger.info("Resource monitoring loop started")
        
        while self.monitoring:
            try:
                # Check file descriptors
                self._check_file_descriptors()
                
                # Check camera health
                self._check_camera_health()
                
                # Clean up old restart times
                self._cleanup_restart_history()
                
                time.sleep(self.fd_check_interval)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                time.sleep(60)  # Wait before retrying
                
        logger.info("Resource monitoring loop stopped")
    
    def _check_file_descriptors(self):
        """Check current file descriptor usage"""
        try:
            pid = os.getpid()
            
            # Count open file descriptors
            fd_dir = f"/proc/{pid}/fd"
            if os.path.exists(fd_dir):
                fd_count = len(os.listdir(fd_dir))
                
                if fd_count >= self.fd_critical_threshold:
                    logger.critical(f"CRITICAL: File descriptor usage at {fd_count}/1024 - triggering emergency cleanup")
                    self._emergency_fd_cleanup()
                elif fd_count >= self.fd_warning_threshold:
                    logger.warning(f"WARNING: High file descriptor usage: {fd_count}/1024")
                else:
                    logger.debug(f"File descriptor usage: {fd_count}/1024")
                    
        except Exception as e:
            logger.debug(f"Could not check file descriptors: {e}")
    
    def _check_camera_health(self):
        """Check health of all UniFi cameras"""
        for camera_id, camera in self.unifi_cameras.items():
            try:
                stats = camera.get_stats()
                auth_failures = stats.get('auth_failure_count', 0)
                
                if auth_failures >= 3:
                    logger.warning(f"Camera {camera_id} has {auth_failures} auth failures - may need attention")
                elif auth_failures >= 5:
                    logger.error(f"Camera {camera_id} has {auth_failures} auth failures - forcing session recycle")
                    camera._force_session_recycle()
                    
            except Exception as e:
                logger.debug(f"Could not check health for camera {camera_id}: {e}")
    
    def _emergency_fd_cleanup(self):
        """Emergency cleanup when file descriptors are critically high"""
        logger.warning("Starting emergency file descriptor cleanup")
        
        # Force recycle all UniFi camera sessions
        recycled_count = 0
        for camera_id, camera in self.unifi_cameras.items():
            try:
                logger.info(f"Force recycling session for camera {camera_id}")
                camera._force_session_recycle()
                recycled_count += 1
            except Exception as e:
                logger.error(f"Error recycling session for {camera_id}: {e}")
        
        logger.info(f"Emergency cleanup recycled {recycled_count} camera sessions")
        
        # Give time for cleanup
        time.sleep(5)
        
        # Check if we need to restart the app
        try:
            pid = os.getpid()
            fd_dir = f"/proc/{pid}/fd"
            if os.path.exists(fd_dir):
                fd_count = len(os.listdir(fd_dir))
                logger.info(f"File descriptor count after cleanup: {fd_count}")
                
                if fd_count >= self.fd_critical_threshold:
                    logger.critical("Emergency cleanup didn't help - requesting app restart")
                    self._request_app_restart("File descriptor limit still critical after cleanup")
                else:
                    logger.info("Emergency cleanup successful - file descriptors below critical threshold")
        except Exception as e:
            logger.error(f"Could not verify cleanup success: {e}")
    
    def _cleanup_restart_history(self):
        """Remove restart times older than 1 hour"""
        one_hour_ago = datetime.now() - timedelta(hours=1)
        old_count = len(self.restart_times)
        self.restart_times = [t for t in self.restart_times if t > one_hour_ago]
        
        if len(self.restart_times) != old_count:
            logger.debug(f"Cleaned up {old_count - len(self.restart_times)} old restart records")
    
    def _request_app_restart(self, reason):
        """Request application restart with rate limiting"""
        now = datetime.now()
        
        # Check if we've restarted too many times recently
        if len(self.restart_times) >= self.max_restarts_per_hour:
            logger.error(f"Too many restarts in the last hour ({len(self.restart_times)}), not restarting for: {reason}")
            return False
        
        logger.critical(f"Requesting application restart: {reason}")
        self.restart_times.append(now)
        
        if self.app_restart_callback:
            try:
                self.app_restart_callback(reason)
                return True
            except Exception as e:
                logger.error(f"Error in restart callback: {e}")
        
        # Fallback: try to restart via systemd or direct process restart
        self._fallback_restart(reason)
        return True
    
    def _fallback_restart(self, reason):
        """Fallback restart mechanism"""
        logger.warning(f"Attempting fallback restart: {reason}")
        
        # Try systemd first (common service names)
        service_names = ['camera-nvr', 'unifi-camera-service', 'nvr-service']
        
        for service_name in service_names:
            try:
                result = subprocess.run(
                    ['systemctl', 'is-enabled', service_name], 
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    logger.info(f"Restarting via systemd service: {service_name}")
                    subprocess.run(['sudo', 'systemctl', 'restart', service_name], timeout=10)
                    return
            except Exception as e:
                logger.debug(f"Systemd restart failed for {service_name}: {e}")
        
        # Fall back to process restart
        logger.warning("No systemd service found - performing emergency exit for process restart")
        logger.warning("Process manager or supervisor should restart the application")
        time.sleep(2)  # Give logs time to flush
        os._exit(1)  # Force exit, allowing process manager to restart
    
    def handle_errno24_error(self, camera_id, error):
        """Handle specific errno 24 (too many open files) error"""
        logger.error(f"Handling errno 24 error for camera {camera_id}: {error}")
        
        self.last_errno24_time = time.time()
        
        # Track errors per camera
        if camera_id not in self.error_counts:
            self.error_counts[camera_id] = 0
        self.error_counts[camera_id] += 1
        
        # If this camera has had repeated errors, take action
        if self.error_counts[camera_id] >= 3:
            logger.warning(f"Camera {camera_id} has had {self.error_counts[camera_id]} errno 24 errors - force recycling")
            if camera_id in self.unifi_cameras:
                try:
                    self.unifi_cameras[camera_id]._force_session_recycle()
                    self.error_counts[camera_id] = 0  # Reset counter after recycling
                    logger.info(f"Successfully recycled session for camera {camera_id}")
                except Exception as e:
                    logger.error(f"Error force recycling camera {camera_id}: {e}")
        
        # Check if we should restart the whole app
        total_errors = sum(self.error_counts.values())
        if total_errors >= 10:
            self._request_app_restart(f"Total errno 24 errors reached {total_errors}")
        
        return True  # Indicate error was handled
    
    def add_camera(self, camera_id, camera_service):
        """Add a new camera to monitoring"""
        self.unifi_cameras[camera_id] = camera_service
        logger.info(f"Added camera {camera_id} to resource monitoring")
    
    def remove_camera(self, camera_id):
        """Remove a camera from monitoring"""
        if camera_id in self.unifi_cameras:
            del self.unifi_cameras[camera_id]
            logger.info(f"Removed camera {camera_id} from resource monitoring")
        
        # Clean up error tracking
        if camera_id in self.error_counts:
            del self.error_counts[camera_id]
    
    def get_status(self):
        """Get detailed monitoring status"""
        try:
            pid = os.getpid()
            fd_dir = f"/proc/{pid}/fd"
            fd_count = len(os.listdir(fd_dir)) if os.path.exists(fd_dir) else "unknown"
        except:
            fd_count = "unknown"
        
        # Get camera stats
        camera_stats = {}
        for camera_id, camera in self.unifi_cameras.items():
            try:
                camera_stats[camera_id] = camera.get_stats()
            except Exception as e:
                camera_stats[camera_id] = {"error": str(e)}
        
        return {
            "monitoring_active": self.monitoring,
            "cameras_monitored": len(self.unifi_cameras),
            "file_descriptors": {
                "current": fd_count,
                "warning_threshold": self.fd_warning_threshold,
                "critical_threshold": self.fd_critical_threshold
            },
            "error_tracking": {
                "error_counts": dict(self.error_counts),
                "last_errno24": self.last_errno24_time,
                "total_errors": sum(self.error_counts.values())
            },
            "restart_management": {
                "restart_count_last_hour": len(self.restart_times),
                "max_restarts_per_hour": self.max_restarts_per_hour,
                "restart_times": [t.isoformat() for t in self.restart_times]
            },
            "camera_stats": camera_stats
        }
    
    def get_summary(self):
        """Get brief monitoring summary"""
        try:
            pid = os.getpid()
            fd_dir = f"/proc/{pid}/fd"
            fd_count = len(os.listdir(fd_dir)) if os.path.exists(fd_dir) else 0
        except:
            fd_count = 0
        
        total_errors = sum(self.error_counts.values())
        
        return {
            "status": "healthy" if fd_count < self.fd_warning_threshold and total_errors < 5 else "warning",
            "fd_usage": f"{fd_count}/{self.fd_critical_threshold}",
            "total_errors": total_errors,
            "cameras": len(self.unifi_cameras),
            "monitoring": self.monitoring
        }