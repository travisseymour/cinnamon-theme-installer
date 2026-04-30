"""
Cinnamon Theme Installer - A PySide6 GUI application for installing Cinnamon themes.
"""

import sys
from importlib.metadata import version as get_pkg_version, PackageNotFoundError
from importlib.resources import files
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QPushButton,
    QMessageBox,
    QHeaderView,
    QAbstractItemView,
)

from .apputils import (
    analyze_archive_full,
    install_theme,
    revert_to_defaults,
    is_valid_archive,
    ThemeComponent,
)


def get_version() -> str:
    """Get the application version from package metadata or pyproject.toml."""
    try:
        return get_pkg_version("Cinnamon_Theme_Installer")
    except PackageNotFoundError:
        # Fallback: try to read from pyproject.toml during development
        try:
            pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
            if pyproject_path.exists():
                content = pyproject_path.read_text()
                for line in content.splitlines():
                    if line.strip().startswith("version"):
                        # Parse: version = "x.y.z"
                        return line.split("=", 1)[1].strip().strip('"')
        except Exception:
            pass
        return "dev"


class ThemeInstallerWindow(QMainWindow):
    """Main window for the Cinnamon Theme Installer."""

    def __init__(self):
        super().__init__()
        self.current_archive: Path | None = None
        self.is_installable = False
        self.setup_ui()

    def setup_ui(self):
        """Initialize the user interface."""
        self.setWindowTitle(f"Cinnamon Theme Installer ver. {get_version()}")
        self.resize(925, 573)
        self.setMinimumSize(700, 450)
        self.center_on_screen()
        self.setAcceptDrops(True)

    def center_on_screen(self):
        """Center the window on the primary screen."""
        screen = QApplication.primaryScreen()
        if screen:
            screen_geometry = screen.availableGeometry()
            window_geometry = self.frameGeometry()
            center_point = screen_geometry.center()
            window_geometry.moveCenter(center_point)
            self.move(window_geometry.topLeft())

        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # Title
        title_label = QLabel("Cinnamon Theme Installer")
        title_label.setStyleSheet("font-size: 20px; font-weight: bold;")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)

        # Instruction label
        self.instruction_label = QLabel("(drag/drop theme archive on window to inspect)")
        self.instruction_label.setStyleSheet("color: gray; font-style: italic;")
        self.instruction_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.instruction_label)

        # Security warning label (hidden by default)
        self.security_label = QLabel()
        self.security_label.setStyleSheet("color: white; background-color: #d32f2f; padding: 10px; border-radius: 5px;")
        self.security_label.setWordWrap(True)
        self.security_label.setVisible(False)
        layout.addWidget(self.security_label)

        # Contents section
        contents_label = QLabel("Contents")
        contents_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(contents_label)

        # Table with 5 columns now
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["FOLDER", "Role", "Included", "Usable", "Valid"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        # Button layout
        button_layout = QHBoxLayout()

        # Install button
        self.install_button = QPushButton("INSTALL THEME")
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.on_install_clicked)
        self.install_button.setStyleSheet("""
            QPushButton {
                padding: 10px 20px;
                font-weight: bold;
            }
            QPushButton:enabled {
                background-color: #4CAF50;
                color: white;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666666;
            }
        """)
        button_layout.addWidget(self.install_button)

        # Cancel button
        self.cancel_button = QPushButton("CANCEL")
        self.cancel_button.clicked.connect(self.close)
        self.cancel_button.setStyleSheet("""
            QPushButton {
                padding: 10px 20px;
                font-weight: bold;
            }
        """)
        button_layout.addWidget(self.cancel_button)

        # Spacer
        button_layout.addStretch()

        # Revert button
        self.revert_button = QPushButton("REVERT")
        self.revert_button.clicked.connect(self.on_revert_clicked)
        self.revert_button.setStyleSheet("""
            QPushButton {
                padding: 10px 20px;
                font-weight: bold;
                background-color: #ff9800;
                color: white;
            }
        """)
        button_layout.addWidget(self.revert_button)

        layout.addLayout(button_layout)

    def dragEnterEvent(self, event: QDragEnterEvent):
        """Handle drag enter events."""
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            if urls and urls[0].isLocalFile():
                file_path = urls[0].toLocalFile()
                if is_valid_archive(file_path):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        """Handle drop events."""
        urls = event.mimeData().urls()
        if urls and urls[0].isLocalFile():
            file_path = Path(urls[0].toLocalFile())
            if is_valid_archive(file_path):
                self.load_archive(file_path)
                event.acceptProposedAction()
                return
        event.ignore()

    def load_archive(self, archive_path: Path):
        """Load and analyze a theme archive."""
        self.reset_state()
        self.current_archive = archive_path

        try:
            analysis = analyze_archive_full(archive_path)
            self.is_installable = analysis.is_installable
            self.populate_table(analysis.components)

            # Update instruction label with theme name
            self.instruction_label.setText(f"Theme: {analysis.theme_name}")

            # Show security warnings if any
            if analysis.has_security_issues:
                warning_text = "Security Issues Detected:\n" + "\n".join(
                    f"  - {issue}" for issue in analysis.security_issues
                )
                self.security_label.setText(warning_text)
                self.security_label.setVisible(True)
            else:
                self.security_label.setVisible(False)

            self.install_button.setEnabled(analysis.is_installable)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to analyze archive:\n{str(e)}")

    def reset_state(self):
        """Reset the application state for a new archive."""
        self.table.setRowCount(0)
        self.current_archive = None
        self.is_installable = False
        self.install_button.setEnabled(False)
        self.instruction_label.setText("(drag/drop theme archive on window to inspect)")
        self.security_label.setVisible(False)

    def populate_table(self, components: list[ThemeComponent]):
        """Populate the table with component information."""
        self.table.setRowCount(len(components))

        for row, comp in enumerate(components):
            # Folder name
            folder_item = QTableWidgetItem(comp.name)
            self.table.setItem(row, 0, folder_item)

            # Role
            role_item = QTableWidgetItem(comp.role)
            self.table.setItem(row, 1, role_item)

            # Included
            included_item = QTableWidgetItem("Yes" if comp.included else "No")
            included_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if comp.included:
                included_item.setForeground(Qt.GlobalColor.darkGreen)
            else:
                included_item.setForeground(Qt.GlobalColor.gray)
            self.table.setItem(row, 2, included_item)

            # Usable
            usable_item = QTableWidgetItem("Yes" if comp.usable else "No")
            usable_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if comp.usable:
                usable_item.setForeground(Qt.GlobalColor.darkGreen)
            else:
                usable_item.setForeground(Qt.GlobalColor.red)
            self.table.setItem(row, 3, usable_item)

            # Valid column
            if not comp.included:
                # Not applicable if not included
                valid_item = QTableWidgetItem("-")
                valid_item.setForeground(Qt.GlobalColor.gray)
            elif comp.valid:
                valid_item = QTableWidgetItem("Yes")
                valid_item.setForeground(Qt.GlobalColor.darkGreen)
            else:
                valid_item = QTableWidgetItem("No")
                valid_item.setForeground(Qt.GlobalColor.red)
                if comp.validation_error:
                    valid_item.setToolTip(comp.validation_error)
            valid_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, 4, valid_item)

            # Gray out rows for components not included
            if not comp.included:
                for col in range(5):
                    item = self.table.item(row, col)
                    if item:
                        item.setForeground(Qt.GlobalColor.gray)

    def on_install_clicked(self):
        """Handle install button click."""
        if not self.current_archive:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Installation",
            f"Install theme from:\n{self.current_archive.name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            success, message = install_theme(self.current_archive)
            if success:
                QMessageBox.information(self, "Success", message)
            else:
                QMessageBox.warning(self, "Installation Failed", message)

    def on_revert_clicked(self):
        """Handle revert button click."""
        reply = QMessageBox.question(
            self,
            "Confirm Revert",
            "Revert to default themes?\n\n"
            "- Mouse: Bibata-Modern-Classic\n"
            "- App theme: Mint-Y-Dark-Aqua\n"
            "- Icon theme: Mint-Y-Sand\n"
            "- Desktop theme: Mint-Y-Dark-Aqua",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            success, message = revert_to_defaults()
            if success:
                QMessageBox.information(self, "Success", message)
            else:
                QMessageBox.warning(self, "Revert Failed", message)


def main():
    """Main entry point for the application."""
    # Check if running on Linux
    if sys.platform != "linux":
        print("Error: Cinnamon Theme Installer only runs on Linux.", file=sys.stderr)
        sys.exit(1)

    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    # Set application icon
    icon_path = files("cinnamon_theme_installer").joinpath("Icon.png")
    app.setWindowIcon(QIcon(str(icon_path)))

    window = ThemeInstallerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
