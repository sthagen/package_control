import functools
import json
import os
import threading

import sublime

from . import events
from .console_write import console_write
from .package_io import package_file_exists, read_package_file
from .settings import (
    preferences_filename,
    pc_settings_filename,
    load_list_setting,
    save_list_setting
)
from .show_error import show_error


class PackageDisabler:
    color_scheme_packages = {}
    """
    A dictionary of packages, containing a color scheme.

    Keys are color scheme names without file extension.
    The values are sets of package names, owning the color scheme.

    {
        'Mariana': {'Color Scheme - Default', 'User'},
    }
    """

    theme_packages = {}
    """
    A dictionary of packages, containing a theme.

    Keys are theme names without file extension.
    The values are sets of package names, owning the theme.

    {
        'Default': {'Theme - Default', 'User'},
    }
    """

    default_themes = {}
    """
    A dictionary of default theme settings.

    Sublime Text 3:

    {
        "theme": "Default.sublime-color-scheme"
    }

    Sublime Text 4

    {
        "theme": "auto"
        "dark_theme": "Default Dark.sublime-color-scheme"
        "light_theme": "Default.sublime-color-scheme"
    }
    """

    global_themes = {}
    """
    A dictionary of stored theme settings.
    """

    default_color_schemes = {}
    """
    A dictionary of default color scheme settings.

    Sublime Text 3:

    {
        "color_scheme": "Mariana.sublime-color-scheme"
    }

    Sublime Text 4

    {
        "color_scheme": "Mariana.sublime-color-scheme"
        "dark_color_scheme": "Mariana.sublime-color-scheme"
        "light_color_scheme": "Breakets.sublime-color-scheme"
    }
    """

    global_color_schemes = {}
    """
    A dictionary of stored color scheme settings.
    """

    view_color_schemes = {}
    """
    A dictionary of view-specific color scheme settings.

    Sublime Text 3:

    {
        <view_id>: {
            "color_scheme": "Mariana.sublime-color-scheme"
        },
        ...
    }

    Sublime Text 4

    {
        <view_id>: {
            "color_scheme": "Mariana.sublime-color-scheme"
            "dark_color_scheme": "Mariana.sublime-color-scheme"
            "light_color_scheme": "Breakets.sublime-color-scheme"
        },
        ...
    }
    """

    view_syntaxes = {}
    """
    A dictionary of view-specifix syntax settings.

    {
        <view_id>: "Packages/Text/Plain Text.tmLanguage"
    }
    """

    lock = threading.Lock()
    restore_id = 0

    @staticmethod
    def get_ignored_packages():
        with PackageDisabler.lock:
            settings = sublime.load_settings(preferences_filename())
            return load_list_setting(settings, 'ignored_packages')

    @staticmethod
    def set_ignored_packages(ignored):
        with PackageDisabler.lock:
            settings = sublime.load_settings(preferences_filename())
            save_list_setting(settings, preferences_filename(), 'ignored_packages', ignored)

    @staticmethod
    def get_version(package):
        """
        Gets the current version of a package

        :param package:
            The name of the package

        :return:
            The string version
        """

        metadata_json = read_package_file(package, 'package-metadata.json')
        if metadata_json:
            try:
                return json.loads(metadata_json).get('version', 'unknown version')
            except (ValueError):
                console_write(
                    '''
                    An error occurred while trying to parse package metadata for %s.
                    ''',
                    package
                )

        return 'unknown version'

    @staticmethod
    def disable_packages(packages, operation='upgrade'):
        """
        Disables one or more packages before installing or upgrading to prevent
        errors where Sublime Text tries to read files that no longer exist, or
        read a half-written file.

        :param packages:
            The string package name, or an array of strings

        :param operation:
            The type of operation that caused the package to be disabled:
             - "upgrade"
             - "remove"
             - "install"
             - "disable"
             - "loader" - deprecated

        :return:
            A set of package names that were disabled
        """

        with PackageDisabler.lock:
            settings = sublime.load_settings(preferences_filename())
            ignored = load_list_setting(settings, 'ignored_packages')

            pc_settings = sublime.load_settings(pc_settings_filename())
            in_process_at_start = load_list_setting(pc_settings, 'in_process_packages')

            if not isinstance(packages, (list, set, tuple)):
                packages = [packages]
            packages = set(packages)

            disabled = packages - (ignored - in_process_at_start)
            ignored |= disabled

            # Clear packages from in-progress when disabling them, otherwise
            # they automatically get re-enabled the next time Sublime Text starts
            if operation == 'disable':
                in_process = in_process_at_start - packages
            else:
                in_process = in_process_at_start | disabled

            # Derermine whether to Backup old color schemes, ayntaxes and theme for later restore.
            # If False, reset to defaults only.
            backup = operation in ('install', 'upgrade')
            if backup:
                # cancel pending settings restore request
                PackageDisabler.restore_id = 0

            PackageDisabler.backup_and_reset_settings(disabled, backup)

            if operation == 'upgrade':
                for package in disabled:
                    version = PackageDisabler.get_version(package)
                    events.add('pre_upgrade', package, version)

            elif operation == 'remove':
                for package in disabled:
                    version = PackageDisabler.get_version(package)
                    events.add(operation, package, version)

            save_list_setting(
                pc_settings,
                pc_settings_filename(),
                'in_process_packages',
                in_process,
                in_process_at_start
            )

            save_list_setting(
                settings,
                preferences_filename(),
                'ignored_packages',
                ignored
            )

            return disabled

    @staticmethod
    def reenable_packages(packages, operation='upgrade'):
        """
        Re-enables packages after they have been installed or upgraded

        :param packages:
            The string package name, or an array of strings

        :param operation:
            The type of operation that caused the package to be re-enabled:
             - "upgrade"
             - "remove"
             - "install"
             - "enable"
             - "loader" - deprecated
        """

        with PackageDisabler.lock:
            settings = sublime.load_settings(preferences_filename())
            ignored = load_list_setting(settings, 'ignored_packages')

            pc_settings = sublime.load_settings(pc_settings_filename())
            in_process = load_list_setting(pc_settings, 'in_process_packages')

            if not isinstance(packages, (list, set, tuple)):
                packages = [packages]
            packages = set(packages) & ignored

            if operation == 'install':
                for package in packages:
                    version = PackageDisabler.get_version(package)
                    events.add(operation, package, version)
                    events.clear(operation, package, future=True)

            elif operation == 'upgrade':
                for package in packages:
                    version = PackageDisabler.get_version(package)
                    events.add('post_upgrade', package, version)
                    events.clear('post_upgrade', package, future=True)
                    events.clear('pre_upgrade', package)

            elif operation == 'remove':
                for package in packages:
                    events.clear('remove', package)

            ignored -= packages
            save_list_setting(settings, preferences_filename(), 'ignored_packages', ignored)

            in_process -= packages
            save_list_setting(pc_settings, pc_settings_filename(), 'in_process_packages', in_process)

            # restore settings after installing missing packages or upgrades
            if operation in ('install', 'upgrade'):
                # By delaying the restore, we give Sublime Text some time to
                # re-enable packages, making errors less likely
                PackageDisabler.restore_id += 1
                sublime.set_timeout(functools.partial(
                    PackageDisabler.restore_settings, PackageDisabler.restore_id), 1000)

    @staticmethod
    def init_default_settings():
        """
        Initializes the default settings from ST's Default/Preferences.sublime-settings.

        Make sure to have correct default values available based on ST version.
        """

        if PackageDisabler.default_themes:
            return

        resource_name = 'Packages/Default/Preferences.sublime-settings'
        settings = sublime.decode_value(sublime.load_resource(resource_name))

        for key in ('color_scheme', 'dark_color_scheme', 'light_color_scheme'):
            value = settings.get(key)
            if value:
                PackageDisabler.default_color_schemes[key] = value

        for key in ('theme', 'dark_theme', 'light_theme'):
            value = settings.get(key)
            if value:
                PackageDisabler.default_themes[key] = value

    @staticmethod
    def backup_and_reset_settings(packages, backup):
        """
        Backup and reset color scheme, syntax or theme contained by specified packages

        :param packages:
            A set of package names which trigger backup and reset of settings.

        :param backup:
            If ``True`` old values are backed up for later restore.
            If ``False`` reset values to defaults only.
        """

        PackageDisabler.init_default_settings()

        settings = sublime.load_settings(preferences_filename())
        cached_settings = {}

        # Backup and reset global theme(s)
        for key, default_file in PackageDisabler.default_themes.items():
            theme_file = settings.get(key)
            if theme_file in (None, '', 'auto', default_file):
                continue
            theme_name, theme_packages = find_theme_packages(theme_file)
            theme_packages &= packages
            if not theme_packages:
                continue
            if backup:
                if theme_name not in PackageDisabler.theme_packages:
                    PackageDisabler.theme_packages[theme_name] = theme_packages
                else:
                    PackageDisabler.theme_packages[theme_name] |= theme_packages
                PackageDisabler.global_themes[key] = theme_file
            settings.set(key, default_file)

        # Backup and reset global color scheme(s)
        #
        # Modern *.sublime-color-schme files may exist in several packages.
        # If one of them gets inaccessible, the merged color scheme breaks.
        # So any related package needs to be monitored. Special treatment is needed
        # for *.tmTheme files, too as they can be overridden by *.sublime-color-schemes.
        for key, default_file in PackageDisabler.default_color_schemes.items():
            scheme_file = settings.get(key)
            cached_settings[key] = scheme_file
            if scheme_file in (None, '', 'auto', default_file):
                continue
            scheme_name, scheme_packages = find_color_scheme_packages(scheme_file)
            scheme_packages &= packages
            if not scheme_packages:
                continue
            if backup:
                if scheme_name not in PackageDisabler.color_scheme_packages:
                    PackageDisabler.color_scheme_packages[scheme_name] = scheme_packages
                else:
                    PackageDisabler.color_scheme_packages[scheme_name] |= scheme_packages
                PackageDisabler.global_color_schemes[key] = scheme_file
            settings.set(key, default_file)

        for window in sublime.windows():
            for view in window.views():
                view_settings = view.settings()

                # Backup and reset view-specific color schemes not already taken care
                # of by resetting the global color scheme above
                for key, default_file in PackageDisabler.default_color_schemes.items():
                    scheme_file = view_settings.get(key)
                    if scheme_file in (None, '', 'auto', default_file, cached_settings[key]):
                        continue
                    scheme_name, scheme_packages = find_color_scheme_packages(scheme_file)
                    scheme_packages &= packages
                    if not scheme_packages:
                        continue
                    if backup:
                        if scheme_name not in PackageDisabler.color_scheme_packages:
                            PackageDisabler.color_scheme_packages[scheme_name] = scheme_packages
                        else:
                            PackageDisabler.color_scheme_packages[scheme_name] |= scheme_packages
                        PackageDisabler.view_color_schemes.setdefault(view.id(), {})[key] = scheme_file
                    # drop view specific color scheme to fallback to global one
                    # and keep it active in case this one can't be restored
                    view_settings.erase(key)

                # Backup and reset assigned syntaxes
                syntax = view_settings.get('syntax')
                if syntax and isinstance(syntax, str) and any(
                    syntax.startswith('Packages/' + package + '/') for package in packages
                ):
                    if backup:
                        PackageDisabler.view_syntaxes[view.id()] = syntax
                    view_settings.set('syntax', 'Packages/Text/Plain text.tmLanguage')

    @staticmethod
    def restore_settings(restore_id):

        if restore_id != PackageDisabler.restore_id:
            return

        with PackageDisabler.lock:
            color_scheme_errors = set()
            syntax_errors = set()

            settings = sublime.load_settings(preferences_filename())
            save_settings = False

            try:
                # restore global theme
                all_missing_theme_packages = set()

                for key, theme_file in PackageDisabler.global_themes.items():
                    theme_name, theme_packages = find_theme_packages(theme_file)
                    missing_theme_packages = PackageDisabler.theme_packages[theme_name] - theme_packages
                    if missing_theme_packages:
                        all_missing_theme_packages |= missing_theme_packages
                    else:
                        settings.set(key, theme_file)
                        save_settings = True

                if all_missing_theme_packages:
                    show_error(
                        '''
                        The following packages no longer participate in your active theme after upgrade.

                           - %s

                        As one of tem may contain the primary theme package,
                        Sublime Text is configured to use the default theme.
                        ''',
                        '\n   - '.join(sorted(all_missing_theme_packages, key=lambda s: s.lower()))
                    )

                # restore global color scheme
                all_missing_scheme_packages = set()

                for key, scheme_file in PackageDisabler.global_color_schemes.items():
                    scheme_name, scheme_packages = find_color_scheme_packages(scheme_file)
                    missing_scheme_packages = PackageDisabler.color_scheme_packages[scheme_name] - scheme_packages
                    if missing_scheme_packages:
                        all_missing_scheme_packages |= missing_scheme_packages
                    else:
                        settings.set(key, scheme_file)
                        save_settings = True

                if all_missing_scheme_packages:
                    show_error(
                        '''
                        The following packages no longer participate in your active color scheme after upgrade.

                           - %s

                        As one of them may contain the primary color scheme,
                        Sublime Text is configured to use the default color scheme.
                        ''',
                        '\n   - '.join(sorted(all_missing_scheme_packages, key=lambda s: s.lower()))
                    )

                # restore viewa-specific color scheme assignments
                for view_id, view_schemes in PackageDisabler.view_color_schemes.items():
                    view = sublime.View(view_id)
                    if not view.is_valid():
                        continue
                    for key, scheme_file in view_schemes.items():
                        if scheme_file in color_scheme_errors:
                            continue
                        scheme_name, scheme_packages = find_color_scheme_packages(scheme_file)
                        missing_scheme_packages = PackageDisabler.color_scheme_packages[scheme_name] - scheme_packages
                        if missing_scheme_packages:
                            console_write('The color scheme "%s" no longer exists' % scheme_file)
                            color_scheme_errors.add(scheme_file)
                            continue
                        view.settings().set(key, scheme_file)

                # restore syntax assignments
                for view_id, syntax in PackageDisabler.view_syntaxes.items():
                    view = sublime.View(view_id)
                    if not view.is_valid() or syntax in syntax_errors:
                        continue
                    if not resource_exists(syntax):
                        console_write('The syntax "%s" no longer exists' % syntax)
                        syntax_errors.add(syntax)
                        continue
                    view.settings().set('syntax', syntax)

            finally:
                if save_settings:
                    sublime.save_settings(preferences_filename())

                PackageDisabler.color_scheme_packages = {}
                PackageDisabler.theme_packages = {}

                PackageDisabler.global_color_schemes = {}
                PackageDisabler.global_themes = {}

                PackageDisabler.view_color_schemes = {}
                PackageDisabler.view_syntaxes = {}

                PackageDisabler.restore_id = 0


def resource_exists(path):
    """
    Checks to see if a file exists

    :param path:
        A unicode string of a resource path, e.g. Packages/Package Name/resource_name.ext

    :return:
        A bool if it exists
    """

    if not path.startswith('Packages/'):
        return False

    parts = path[9:].split('/', 1)
    if len(parts) != 2:
        return False

    package_name, relative_path = parts
    return package_file_exists(package_name, relative_path)


def find_color_scheme_packages(color_scheme):
    """
    Build a set of packages, containing the color_scheme.

    :param color_scheme:
        The color scheme settings value

    :returns:
        A tuple of color scheme name and a set of package names containing it

        ( 'Mariana', { 'Color Scheme - Default', 'User' } )
    """

    packages = set()
    name = os.path.basename(os.path.splitext(color_scheme)[0])

    for ext in ('.sublime-color-scheme', '.tmTheme'):
        for path in sublime.find_resources(name + ext):
            parts = path[9:].split('/', 1)
            if len(parts) == 2:
                packages.add(parts[0])

    return name, packages


def find_theme_packages(theme):
    """
    Build a set of packages, containing the theme.

    :param theme:
        The theme settings value

    :returns:
        A tuple of theme name and a set of package names containing it

        ( 'Default', { 'Theme - Default', 'User' } )
    """

    packages = set()
    file_name = os.path.basename(theme)
    name = os.path.splitext(file_name)[0]

    for path in sublime.find_resources(file_name):
        parts = path[9:].split('/', 1)
        if len(parts) == 2:
            packages.add(parts[0])

    return name, packages
