"""
PFR Sentinel - PySide6 Fluent UI Entry Point
Run this to launch the new modern UI

Supports command-line flags:
  python main_pyside.py                         # Normal GUI mode
  python main_pyside.py --auto-start            # Start capture automatically
  python main_pyside.py --auto-stop 3600        # Stop after 1 hour
  python main_pyside.py --headless              # No GUI (headless mode)
  python main_pyside.py --tray                  # Start minimized to system tray
"""
import sys
import os
import argparse

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QFont, QPixmap, QIcon
from qfluentwidgets import SplashScreen, FluentIcon

from ui.main_window import MainWindow
from ui.theme import apply_theme
from services.logger import app_logger
from version import __version__
from app_config import APP_DISPLAY_NAME, APP_SUBTITLE


def main():
    """Launch PFR Sentinel with PySide6 Fluent UI"""
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description=f'{APP_DISPLAY_NAME} - {APP_SUBTITLE} (PySide6 UI)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python main_pyside.py                         # Normal GUI mode
  python main_pyside.py --auto-start            # Start capture automatically
  python main_pyside.py --auto-stop 3600        # Stop after 1 hour
  python main_pyside.py --auto-start --auto-stop 3600  # Capture for 1 hour then stop
  python main_pyside.py --headless              # Headless mode (no GUI)
  python main_pyside.py --tray                  # Start minimized to system tray
        """)
    
    parser.add_argument('--auto-start', action='store_true',
                       help='Automatically start camera capture on launch')
    parser.add_argument('--auto-stop', type=int, metavar='SECONDS', nargs='?', const=0,
                       help='Automatically stop capture after N seconds (0 = run until closed)')
    parser.add_argument('--headless', action='store_true',
                       help='Run without GUI - captures images based on saved config')
    parser.add_argument('--tray', action='store_true',
                       help='Start minimized to system tray (requires pystray)')
    
    args = parser.parse_args()
    
    # Headless mode - no GUI at all
    if args.headless:
        from services.headless_runner import run_headless
        success = run_headless(auto_stop=args.auto_stop)
        sys.exit(0 if success else 1)
    
    # Enable high DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    
    # Create application
    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setApplicationVersion(__version__)
    
    # Set default font
    font = QFont("Segoe UI", 10)
    app.setFont(font)
    
    # Apply theme
    apply_theme()
    
    # Create splash screen FIRST (before heavy window creation)
    splash_icon = QIcon('assets/app_icon.png')
    splash = SplashScreen(splash_icon, None)
    splash.setIconSize(QSize(200, 200))
    splash.titleBar.hide()  # Hide title bar for cleaner look
    splash.resize(1400, 900)  # Match main window size
    splash.show()
    QApplication.processEvents()  # Force splash to render immediately
    
    app_logger.info(f"Starting {APP_DISPLAY_NAME} v{__version__} (PySide6 UI)")
    
    # Create main window (this takes time - splash stays visible)
    window = MainWindow()
    QApplication.processEvents()
    
    # Check if tray mode should be enabled (from config or --tray argument)
    tray_enabled = args.tray or window.config.get('tray_mode_enabled', False)
    
    # System tray mode - start minimized to tray
    if tray_enabled:
        try:
            from ui.system_tray_qt import SystemTrayQt
            tray = SystemTrayQt(window, app, auto_start=args.auto_start, auto_stop=args.auto_stop)
            window.system_tray = tray  # Store reference so window knows it's in tray mode
            
            # If --tray was explicitly provided, save it to config
            if args.tray:
                window.config.set('tray_mode_enabled', True)
                window.config.save()
            
            # Close splash when entering tray mode
            splash.finish()
            
            # Window will be shown by tray when user clicks "Show Window"
        except ImportError as e:
            app_logger.error(f"System tray mode requires pystray: {e}")
            print(f"Error: Install pystray with: pip install pystray", file=sys.stderr)
            sys.exit(1)
    else:
        # Show main window and close splash
        window.show()
        splash.finish()
    
    # Load configuration
    window.load_config()
    
    # Auto-start capture if requested
    if args.auto_start and not args.tray:
        # Delay start to allow UI to initialize
        from PySide6.QtCore import QTimer
        QTimer.singleShot(2000, lambda: window.start_capture())
        
        # Auto-stop after timeout if specified
        if args.auto_stop and args.auto_stop > 0:
            QTimer.singleShot(args.auto_stop * 1000, lambda: window.stop_capture())
    
    # Run event loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
