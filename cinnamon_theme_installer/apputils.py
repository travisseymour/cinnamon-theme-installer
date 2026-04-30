"""
Utility functions for Cinnamon Theme Installer.
Handles theme archive inspection and installation.
"""

import configparser
import os
import tarfile
import zipfile
import subprocess
from pathlib import Path, PurePosixPath
from dataclasses import dataclass, field


# Known theme components with their roles and usability on Cinnamon
KNOWN_COMPONENTS = {
    "cinnamon": {"role": "Desktop theme", "usable": True, "required_file": "cinnamon.css"},
    "gtk-2.0": {"role": "Older GTK apps", "usable": True, "required_file": "gtkrc"},
    "gtk-3.0": {"role": "App controls theme", "usable": True, "required_file": "gtk.css"},
    "gtk-4.0": {"role": "Newer GTK4 apps", "usable": True, "required_file": "gtk.css"},
    "metacity-1": {"role": "Window borders", "usable": True, "required_file": None},
    "xfwm4": {"role": "XFCE window manager borders", "usable": False, "required_file": None},
    "index.theme": {"role": "Installable theme package", "usable": True, "required_file": None},
    "gnome-shell": {"role": "GNOME Shell theme", "usable": False, "required_file": None},
    "plank": {"role": "Plank dock theme", "usable": True, "required_file": None},
}

# Required components for a valid Cinnamon theme
REQUIRED_COMPONENTS = {"cinnamon", "index.theme"}

# Default themes for revert
DEFAULT_THEMES = {
    "cursor": "Bibata-Modern-Classic",
    "gtk": "Mint-Y-Dark-Aqua",
    "icon": "Mint-Y-Sand",
    "cinnamon": "Mint-Y-Dark-Aqua",
}


@dataclass
class ThemeComponent:
    """Represents a component found in a theme archive."""

    name: str
    role: str
    included: bool
    usable: bool
    valid: bool = True  # Whether component passes validation
    validation_error: str = ""  # Description of validation failure


@dataclass
class ArchiveAnalysis:
    """Complete analysis of a theme archive."""

    theme_name: str  # From index.theme or folder name
    folder_name: str  # Actual folder name in archive
    components: list[ThemeComponent] = field(default_factory=list)
    is_installable: bool = False
    security_issues: list[str] = field(default_factory=list)
    has_security_issues: bool = False


def _check_path_traversal(path: str) -> bool:
    """Check if a path attempts directory traversal."""
    # Normalize and check for .. components
    normalized = PurePosixPath(path)
    try:
        # Check if path tries to escape
        normalized.relative_to(".")
        # Also check for absolute paths
        if path.startswith("/") or path.startswith("\\"):
            return True
        # Check for .. in any component
        if ".." in path.split("/"):
            return True
        return False
    except ValueError:
        return True


def _is_suspicious_file(name: str) -> bool:
    """Check if a file looks potentially malicious."""
    suspicious_extensions = {".exe", ".dll", ".bat", ".cmd", ".com", ".msi"}
    lower_name = name.lower()
    return any(lower_name.endswith(ext) for ext in suspicious_extensions)


def _get_all_archive_paths(archive_path: Path) -> tuple[list[str], list[tuple[str, str]]]:
    """
    Get all paths in an archive.

    Returns:
        tuple: (list of all paths, list of (symlink_path, target) tuples)
    """
    all_paths = []
    symlinks = []

    if archive_path.suffix == ".zip" or archive_path.name.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                all_paths.append(info.filename)
                # ZIP files store symlinks differently - check external_attr
                # Unix symlinks have (0o120000 << 16) in external_attr
                is_symlink = (info.external_attr >> 28) == 0xA
                if is_symlink:
                    # Read the target from the file content
                    try:
                        target = zf.read(info.filename).decode("utf-8")
                        symlinks.append((info.filename, target))
                    except Exception:
                        symlinks.append((info.filename, ""))
    else:
        with tarfile.open(archive_path, "r:*") as tf:
            for member in tf.getmembers():
                all_paths.append(member.name)
                if member.issym():
                    symlinks.append((member.name, member.linkname))
                elif member.islnk():
                    symlinks.append((member.name, member.linkname))

    return all_paths, symlinks


def _is_dangerous_symlink(symlink_path: str, target: str, root_folder: str) -> bool:
    """
    Check if a symlink is potentially dangerous.

    A symlink is dangerous if:
    - It points to an absolute path
    - It uses .. to escape the theme directory
    """
    # Absolute path symlinks are dangerous
    if target.startswith("/") or target.startswith("\\"):
        return True

    # Check if the symlink escapes the archive root using ..
    # Resolve the symlink relative to its location
    symlink_dir = str(PurePosixPath(symlink_path).parent)
    resolved = os.path.normpath(os.path.join(symlink_dir, target))

    # If resolved path starts with .. or doesn't start with root folder, it's escaping
    if resolved.startswith(".."):
        return True
    if not resolved.startswith(root_folder):
        return True

    return False


def _extract_file_content(archive_path: Path, file_path: str) -> str | None:
    """Extract and return the content of a specific file from the archive."""
    try:
        if archive_path.suffix == ".zip" or archive_path.name.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                with zf.open(file_path) as f:
                    return f.read().decode("utf-8", errors="replace")
        else:
            with tarfile.open(archive_path, "r:*") as tf:
                member = tf.getmember(file_path)
                f = tf.extractfile(member)
                if f:
                    return f.read().decode("utf-8", errors="replace")
    except (KeyError, tarfile.TarError, zipfile.BadZipFile):
        pass
    return None


def _parse_index_theme(content: str) -> dict[str, str]:
    """Parse index.theme content and return metadata."""
    config = configparser.ConfigParser()
    try:
        config.read_string(content)

        result = {}

        # Try different section names
        for section in ["Desktop Entry", "X-GNOME-Metatheme", "Icon Theme"]:
            if config.has_section(section):
                if config.has_option(section, "Name"):
                    result["name"] = config.get(section, "Name")
                if config.has_option(section, "Comment"):
                    result["comment"] = config.get(section, "Comment")
                if config.has_option(section, "Type"):
                    result["type"] = config.get(section, "Type")

        return result
    except configparser.Error:
        return {}


def get_archive_contents(archive_path: str | Path) -> tuple[str, list[str], list[str]]:
    """
    Extract the list of top-level items from a theme archive.

    Returns:
        tuple: (folder_name, list of top-level items, list of all paths)
    """
    archive_path = Path(archive_path)

    if not archive_path.exists():
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    items = set()
    folder_name = ""
    all_paths = []

    if archive_path.suffix == ".zip" or archive_path.name.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zf:
            for name in zf.namelist():
                all_paths.append(name)
                parts = name.split("/")
                if len(parts) >= 1 and parts[0]:
                    if not folder_name:
                        folder_name = parts[0]
                    if len(parts) >= 2 and parts[1]:
                        items.add(parts[1].rstrip("/"))
    else:
        # Assume tar-based archive (.tar.gz, .tar.xz, .tar.bz2, .tgz)
        with tarfile.open(archive_path, "r:*") as tf:
            for member in tf.getmembers():
                all_paths.append(member.name)
                parts = member.name.split("/")
                if len(parts) >= 1 and parts[0]:
                    if not folder_name:
                        folder_name = parts[0]
                    if len(parts) >= 2 and parts[1]:
                        items.add(parts[1].rstrip("/"))

    return folder_name, sorted(items), all_paths


def analyze_archive(archive_path: str | Path) -> tuple[str, list[ThemeComponent], bool]:
    """
    Analyze a theme archive and return component information.

    Returns:
        tuple: (theme_name, list of ThemeComponent, is_installable)

    Note: This is the legacy interface. Use analyze_archive_full() for complete analysis.
    """
    analysis = analyze_archive_full(archive_path)
    return analysis.theme_name, analysis.components, analysis.is_installable


def analyze_archive_full(archive_path: str | Path) -> ArchiveAnalysis:
    """
    Perform complete analysis of a theme archive including security checks.

    Returns:
        ArchiveAnalysis object with all validation information.
    """
    archive_path = Path(archive_path)
    folder_name, items, all_paths = get_archive_contents(archive_path)

    analysis = ArchiveAnalysis(
        theme_name=folder_name,  # Will be updated if index.theme has Name
        folder_name=folder_name,
    )

    # Security checks
    all_archive_paths, symlinks = _get_all_archive_paths(archive_path)

    # Check for dangerous symlinks (only those that escape the theme directory)
    for sym_path, target in symlinks:
        if _is_dangerous_symlink(sym_path, target, folder_name):
            analysis.security_issues.append(f"Dangerous symlink: {sym_path} -> {target}")

    # Check for path traversal
    for path in all_archive_paths:
        if _check_path_traversal(path):
            analysis.security_issues.append(f"Path traversal attempt: {path}")

    # Check for suspicious files
    for path in all_archive_paths:
        if _is_suspicious_file(path):
            analysis.security_issues.append(f"Suspicious file: {path}")

    analysis.has_security_issues = len(analysis.security_issues) > 0

    # Try to parse index.theme for the real theme name
    index_theme_path = f"{folder_name}/index.theme"
    if index_theme_path in all_paths or any(p == index_theme_path for p in all_archive_paths):
        content = _extract_file_content(archive_path, index_theme_path)
        if content:
            metadata = _parse_index_theme(content)
            if "name" in metadata:
                analysis.theme_name = metadata["name"]

    found_required = set()

    # Check known components
    for comp_name, comp_info in KNOWN_COMPONENTS.items():
        included = comp_name in items
        valid = True
        validation_error = ""

        # If included and has a required file, check for it
        if included and comp_info.get("required_file"):
            required_file = comp_info["required_file"]
            expected_path = f"{folder_name}/{comp_name}/{required_file}"

            # Check if the required file exists in the archive
            file_found = any(p == expected_path or p.rstrip("/") == expected_path for p in all_archive_paths)

            if not file_found:
                valid = False
                validation_error = f"Missing {required_file}"

        analysis.components.append(
            ThemeComponent(
                name=comp_name + ("/" if comp_name != "index.theme" else ""),
                role=comp_info["role"],
                included=included,
                usable=comp_info["usable"],
                valid=valid,
                validation_error=validation_error,
            )
        )

        if included and comp_name in REQUIRED_COMPONENTS:
            # For required components, also check validity
            if valid:
                found_required.add(comp_name)

    # Check for unknown items
    known_names = set(KNOWN_COMPONENTS.keys())
    # Skip common non-theme files
    skip_items = {
        "LICENSE",
        "README",
        "README.md",
        "metadata.json",
        "options.py",
        "config",
        "libadwaita-1.5",
        "install.sh",
        "gextract.sh",
    }
    skip_items.update(f for f in items if f.endswith(".png") or f.endswith(".sh"))

    for item in items:
        clean_name = item.rstrip("/")
        if clean_name not in known_names and clean_name not in skip_items:
            analysis.components.append(
                ThemeComponent(
                    name=clean_name + "/",
                    role="Unknown",
                    included=True,
                    usable=False,
                    valid=True,
                    validation_error="",
                )
            )

    # Theme is installable only if:
    # 1. All required components are present AND valid
    # 2. No security issues
    analysis.is_installable = REQUIRED_COMPONENTS.issubset(found_required) and not analysis.has_security_issues

    return analysis


def get_theme_install_dir() -> Path:
    """Get the user's theme installation directory."""
    return Path.home() / ".themes"


def install_theme(archive_path: str | Path) -> tuple[bool, str]:
    """
    Install a theme from an archive.

    Returns:
        tuple: (success, message)
    """
    archive_path = Path(archive_path)
    theme_dir = get_theme_install_dir()
    theme_dir.mkdir(parents=True, exist_ok=True)

    try:
        analysis = analyze_archive_full(archive_path)

        if analysis.has_security_issues:
            issues = "\n".join(f"  - {issue}" for issue in analysis.security_issues)
            return False, f"Security issues detected:\n{issues}"

        if not analysis.is_installable:
            return False, "Theme is missing required components (cinnamon/ with cinnamon.css and index.theme)"

        # Extract archive with security filter for tar files
        if archive_path.suffix == ".zip" or archive_path.name.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(theme_dir)
        else:
            with tarfile.open(archive_path, "r:*") as tf:
                # Use data filter for security (Python 3.12+) or manual check
                try:
                    tf.extractall(theme_dir, filter="data")
                except TypeError:
                    # Python < 3.12, extract normally but we already checked for issues
                    tf.extractall(theme_dir)

        # Apply the theme using gsettings
        # Use folder name for installation, not display name
        apply_theme(analysis.folder_name)

        return True, f"Theme '{analysis.theme_name}' installed and applied successfully"
    except Exception as e:
        return False, f"Installation failed: {str(e)}"


def apply_theme(theme_name: str) -> tuple[bool, str]:
    """
    Apply a Cinnamon theme using gsettings.

    Returns:
        tuple: (success, message)
    """
    try:
        # Set the Cinnamon desktop theme
        subprocess.run(
            ["gsettings", "set", "org.cinnamon.theme", "name", theme_name],
            check=True,
            capture_output=True,
        )

        # Set the GTK theme (window controls)
        subprocess.run(
            ["gsettings", "set", "org.cinnamon.desktop.interface", "gtk-theme", theme_name],
            check=True,
            capture_output=True,
        )

        # Set the window border theme
        subprocess.run(
            ["gsettings", "set", "org.cinnamon.desktop.wm.preferences", "theme", theme_name],
            check=True,
            capture_output=True,
        )

        return True, f"Theme '{theme_name}' applied"
    except subprocess.CalledProcessError as e:
        return False, f"Failed to apply theme: {e.stderr.decode() if e.stderr else str(e)}"
    except FileNotFoundError:
        return False, "gsettings command not found. Is Cinnamon desktop installed?"


def revert_to_defaults() -> tuple[bool, str]:
    """
    Revert all themes to their default values.

    Returns:
        tuple: (success, message)
    """
    errors = []

    try:
        # Set cursor theme
        subprocess.run(
            ["gsettings", "set", "org.cinnamon.desktop.interface", "cursor-theme", DEFAULT_THEMES["cursor"]],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        errors.append(f"Cursor theme: {e.stderr.decode() if e.stderr else str(e)}")

    try:
        # Set GTK/Application theme
        subprocess.run(
            ["gsettings", "set", "org.cinnamon.desktop.interface", "gtk-theme", DEFAULT_THEMES["gtk"]],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        errors.append(f"GTK theme: {e.stderr.decode() if e.stderr else str(e)}")

    try:
        # Set icon theme
        subprocess.run(
            ["gsettings", "set", "org.cinnamon.desktop.interface", "icon-theme", DEFAULT_THEMES["icon"]],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        errors.append(f"Icon theme: {e.stderr.decode() if e.stderr else str(e)}")

    try:
        # Set Cinnamon desktop theme
        subprocess.run(
            ["gsettings", "set", "org.cinnamon.theme", "name", DEFAULT_THEMES["cinnamon"]],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        errors.append(f"Desktop theme: {e.stderr.decode() if e.stderr else str(e)}")

    try:
        # Set window border theme
        subprocess.run(
            ["gsettings", "set", "org.cinnamon.desktop.wm.preferences", "theme", DEFAULT_THEMES["gtk"]],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        errors.append(f"Window theme: {e.stderr.decode() if e.stderr else str(e)}")

    if errors:
        return False, "Some themes failed to revert:\n" + "\n".join(errors)

    return True, "All themes reverted to defaults"


def is_valid_archive(file_path: str | Path) -> bool:
    """Check if a file is a valid theme archive."""
    file_path = Path(file_path)
    valid_extensions = {".tar.gz", ".tar.xz", ".tar.bz2", ".tgz", ".zip"}

    name = file_path.name.lower()
    return any(name.endswith(ext) for ext in valid_extensions)
