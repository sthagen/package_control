import sublime
import sublime_plugin

from .. import text
from ..package_disabler import PackageDisabler
from ..show_quick_panel import show_quick_panel


class EnablePackageCommand(sublime_plugin.WindowCommand, PackageDisabler):

    """
    A command that removes a package from Sublime Text's ignored packages list
    """

    def run(self):
        self.disabled_packages = self.get_ignored_packages()
        if not self.disabled_packages:
            sublime.message_dialog(text.format(
                '''
                Package Control

                There are no disabled packages to enable
                '''
            ))
            return
        show_quick_panel(self.window, self.disabled_packages, self.on_done)

    def on_done(self, picked):
        """
        Quick panel user selection handler - enables the selected package

        :param picked:
            An integer of the 0-based package name index from the presented
            list. -1 means the user cancelled.
        """

        if picked == -1:
            return
        package = self.disabled_packages[picked]

        self.reenable_packages(package, 'enable')

        sublime.status_message(text.format(
            '''
            Package %s successfully removed from list of disabled packages -
            restarting Sublime Text may be required
            ''',
            package
        ))
