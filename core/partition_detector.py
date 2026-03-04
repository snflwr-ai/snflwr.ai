"""
Partition Detector for USB Device Management
Detects and manages USB partitions for offline data storage
"""

import platform
from pathlib import Path
from typing import Optional, Dict, List
from utils.logger import get_logger

logger = get_logger(__name__)


class PartitionDetector:
    """
    Detects USB partitions and manages offline data storage

    Supports Windows, macOS, and Linux USB device detection
    Used for privacy-focused offline data storage
    """

    def __init__(self):
        """Initialize partition detector"""
        self.platform = platform.system()
        logger.info(f"Partition detector initialized for platform: {self.platform}")

    def detect_usb_mounts(self) -> List[Path]:
        """
        Detect mounted USB devices

        Returns:
            List of Path objects for mounted USB devices
        """
        usb_mounts = []

        try:
            if self.platform == 'Windows':
                usb_mounts = self._detect_windows_usb()
            elif self.platform == 'Darwin':  # macOS
                usb_mounts = self._detect_macos_usb()
            elif self.platform == 'Linux':
                usb_mounts = self._detect_linux_usb()
            else:
                logger.warning(f"Unsupported platform: {self.platform}")

        except OSError as e:
            logger.error(f"Failed to detect USB mounts: {e}")

        return usb_mounts

    def _detect_windows_usb(self) -> List[Path]:
        """
        Detect USB devices on Windows

        Returns:
            List of drive letters for USB devices
        """
        usb_drives = []

        try:
            import string
            import ctypes

            # Check all drive letters
            for drive_letter in string.ascii_uppercase:
                drive_path = Path(f"{drive_letter}:\\")

                # Check if drive exists
                if drive_path.exists():
                    # Check drive type (DRIVE_REMOVABLE = 2)
                    drive_type = ctypes.windll.kernel32.GetDriveTypeW(str(drive_path))
                    if drive_type == 2:  # Removable drive
                        usb_drives.append(drive_path)
                        logger.info(f"Found USB drive: {drive_path}")

        except (ImportError, OSError, AttributeError) as e:
            logger.error(f"Error detecting Windows USB: {e}")

        return usb_drives

    def _detect_macos_usb(self) -> List[Path]:
        """
        Detect USB devices on macOS

        Returns:
            List of paths to USB mount points
        """
        usb_mounts = []

        try:
            volumes_path = Path("/Volumes")
            if volumes_path.exists():
                # Exclude macOS system volumes
                for volume in volumes_path.iterdir():
                    if volume.is_dir() and volume.name not in ["Macintosh HD", "Preboot", "Recovery", "VM"]:
                        usb_mounts.append(volume)
                        logger.info(f"Found USB mount: {volume}")

        except OSError as e:
            logger.error(f"Error detecting macOS USB: {e}")

        return usb_mounts

    def _detect_linux_usb(self) -> List[Path]:
        """
        Detect USB devices on Linux

        Returns:
            List of paths to USB mount points
        """
        usb_mounts = []

        try:
            # Check common mount points
            media_path = Path("/media")
            mnt_path = Path("/mnt")

            # Check /media/<user>/ directories
            if media_path.exists():
                for user_dir in media_path.iterdir():
                    if user_dir.is_dir():
                        for mount in user_dir.iterdir():
                            if mount.is_dir():
                                usb_mounts.append(mount)
                                logger.info(f"Found USB mount: {mount}")

            # Check /mnt/ directories
            if mnt_path.exists():
                for mount in mnt_path.iterdir():
                    if mount.is_dir() and mount.name != "wsl":  # Exclude WSL
                        usb_mounts.append(mount)
                        logger.info(f"Found USB mount: {mount}")

        except OSError as e:
            logger.error(f"Error detecting Linux USB: {e}")

        return usb_mounts

    def find_snflwr_usb(self) -> Optional[Path]:
        """
        Find USB device with snflwr.ai data

        Looks for USB device containing snflwr.db or .snflwr marker

        Returns:
            Path to Snflwr USB device, or None if not found
        """
        usb_mounts = self.detect_usb_mounts()

        for mount in usb_mounts:
            # Check for database file
            db_path = mount / "snflwr.db"
            marker_path = mount / ".snflwr"

            if db_path.exists() or marker_path.exists():
                logger.info(f"Found Snflwr USB device: {mount}")
                return mount

        logger.warning("No Snflwr USB device found")
        return None

    def find_local_install(self) -> Optional[Path]:
        """
        Find local snflwr.ai data directory (non-USB installs).

        Checks the configured APP_DATA_DIR for an existing database,
        marker file, or simply an existing data directory (fresh install).

        Returns:
            Path to data directory if found, or None
        """
        from config import system_config
        data_dir = system_config.APP_DATA_DIR

        if data_dir.exists():
            logger.info(f"Found local installation: {data_dir}")
            return data_dir

        logger.info("No local installation found")
        return None

    def is_writable(self, path: Path) -> bool:
        """
        Check if USB mount is writable

        Args:
            path: Path to check

        Returns:
            True if writable, False otherwise
        """
        try:
            test_file = path / ".write_test"
            test_file.touch()
            test_file.unlink()
            return True
        except OSError:
            return False


# Singleton instance
partition_detector = PartitionDetector()
