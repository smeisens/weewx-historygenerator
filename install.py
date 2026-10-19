# Installer for the historygenerator WeeWX extension
#
# Copyright (c) 2013-2016  Nick Dajda <nick.dajda@gmail.com>
# Modifications (c) 2026  Sigi
#
# Distributed under the terms of the GNU General Public License v3

from weecfg.extension import ExtensionInstaller


def loader():
    return HistoryGeneratorInstaller()


class HistoryGeneratorInstaller(ExtensionInstaller):
    def __init__(self):
        super(HistoryGeneratorInstaller, self).__init__(
            version="1.0.0",
            name='historygenerator',
            description='Colored month/year history tables and $alltime tag for Cheetah skins '
                        '(standalone, based on fuzzy-archer).',
            author="Sigi",
            files=[('bin/user', ['bin/user/historygenerator.py'])],
        )
