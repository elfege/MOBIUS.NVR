#!/usr/bin/env python3
"""
Process Reaper Utility
Handles proper process termination and zombie prevention
"""

import os
import signal
import subprocess
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


def reap_child_processes() -> int:
    """
    Reap all zombie child processes (non-blocking).
    
    Returns:
        int: Number of zombies reaped
    """
    reaped_count = 0
    while True:
        try:
            # WNOHANG = don't block if no zombies available
            pid, status = os.waitpid(-1, os.WNOHANG)
            if pid == 0:
                # No more zombies to reap
                break
            reaped_count += 1
            logger.debug(f"Reaped zombie process PID {pid} with status {status}")
        except ChildProcessError:
            # No child processes exist
            break
        except Exception as e:
            logger.error(f"Error reaping zombie: {e}")
            break
    
    return reaped_count


def terminate_process_gracefully(process: subprocess.Popen, timeout: int = 5, process_name: str = "process") -> bool:
    """
    Terminate a process gracefully with proper zombie reaping.
    
    Args:
        process: The subprocess.Popen object to terminate
        timeout: Seconds to wait for graceful termination before force kill
        process_name: Human-readable name for logging
    
    Returns:
        bool: True if successfully terminated and reaped
    """
    if not process:
        logger.warning(f"Cannot terminate {process_name} - no process handle")
        return False
    
    try:
        # Check if process already dead
        exit_code = process.poll()
        if exit_code is not None:
            logger.info(f"{process_name} already terminated (exit code: {exit_code})")
            # Reap the zombie by calling wait()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass  # Already dead, wait timed out
            return True
        
        # Process is alive - terminate it gracefully first
        pid = process.pid
        logger.info(f"Terminating {process_name} (PID: {pid})")
        process.terminate()  # Send SIGTERM
        
        try:
            # Wait for graceful shutdown
            exit_code = process.wait(timeout=timeout)
            logger.info(f"✅ {process_name} terminated gracefully (exit code: {exit_code})")
            return True
        except subprocess.TimeoutExpired:
            # Didn't die gracefully - force kill
            logger.warning(f"{process_name} didn't terminate gracefully, force killing")
            process.kill()  # Send SIGKILL
            exit_code = process.wait()  # This MUST succeed after SIGKILL
            logger.info(f"⚠️ {process_name} force-killed (exit code: {exit_code})")
            return True
            
    except Exception as e:
        logger.error(f"Error terminating {process_name}: {e}")
        return False


def kill_processes_by_pattern(pattern: str, signal_type: int = signal.SIGTERM, verify: bool = True) -> bool:
    """
    Kill processes matching a pattern using pkill, then verify they're dead.
    This is a FALLBACK method - prefer terminate_process_gracefully() when you have a process handle.
    
    Args:
        pattern: Pattern to match (used with pkill -f)
        signal_type: Signal to send (default SIGTERM, use signal.SIGKILL for force)
        verify: Whether to verify processes are dead after killing
    
    Returns:
        bool: True if killed successfully (or no processes found)
    """
    try:
        # Check if any processes exist
        check = subprocess.run(
            ['pgrep', '-f', pattern],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        if check.returncode != 0:
            # No processes found - nothing to kill
            logger.debug(f"No processes matching '{pattern}' found")
            return True
        
        # Kill all matching processes
        signal_name = "SIGTERM" if signal_type == signal.SIGTERM else "SIGKILL"
        logger.info(f"Killing processes matching '{pattern}' with {signal_name}")
        
        subprocess.run(
            ['pkill', f'-{signal_type}', '-f', pattern],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # Give processes time to die
        time.sleep(1)
        
        # Reap any zombies created
        reaped = reap_child_processes()
        if reaped > 0:
            logger.debug(f"Reaped {reaped} zombie(s) after pkill")
        
        # Verify they're dead if requested
        if verify:
            verify_result = subprocess.run(
                ['pgrep', '-f', pattern],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            
            if verify_result.returncode == 0:
                logger.error(f"Failed to kill all processes matching '{pattern}'")
                return False
            
            logger.info(f"✅ All processes matching '{pattern}' killed")
        
        return True
        
    except Exception as e:
        logger.error(f"Error killing processes matching '{pattern}': {e}")
        return False


def install_sigchld_handler():
    """
    Install global SIGCHLD handler to automatically reap zombies.
    Call this once during application startup.
    """
    def sigchld_handler(signum, frame):
        """Signal handler called when any child process dies"""
        reap_child_processes()
    
    signal.signal(signal.SIGCHLD, sigchld_handler)
    logger.info("✅ SIGCHLD handler installed - automatic zombie reaping enabled")