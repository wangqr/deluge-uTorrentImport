#
# core.py
#
# Copyright (C) 2009 Laharah <laharah22+deluge@gmail.com>
#
# Basic plugin template created by:
# Copyright (C) 2008 Martijn Voncken <mvoncken@gmail.com>
# Copyright (C) 2007-2009 Andrew Resch <andrewresch@gmail.com>
# Copyright (C) 2009 Damien Churchill <damoxc@gmail.com>
#
# Deluge is free software.
#
# You may redistribute it and/or modify it under the terms of the
# GNU General Public License, as published by the Free Software
# Foundation; either version 3 of the License, or (at your option)
# any later version.
#
# deluge is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with deluge.    If not, write to:
# 	The Free Software Foundation, Inc.,
# 	51 Franklin Street, Fifth Floor
# 	Boston, MA  02110-1301, USA.
#
#    In addition, as a special exception, the copyright holders give
#    permission to link the code of portions of this program with the OpenSSL
#    library.
#    You must obey the GNU General Public License in all respects for all of
#    the code used other than OpenSSL. If you modify file(s) with this
#    exception, you may extend this exception to your version of the file(s),
#    but you are not obligated to do so. If you do not wish to do so, delete
#    this exception statement from your version. If you delete this exception
#    statement from all source files in the program, then also delete it here.
#

import os
import re
import base64
from getpass import getuser

from deluge.ui.common import TorrentInfo
from deluge.bencode import bdecode
from deluge.log import LOG as log
from deluge.plugins.pluginbase import CorePluginBase
import deluge.component as component
import deluge.configmanager
from deluge.core.rpcserver import export

from common import Log

log = Log()

DEFAULT_PREFS = {
    "torrent_blacklist": ['.fileguard', 'rec'],
    "wine_drives": {},
    "use_wine_mappings": False,
    "skip_recheck": False,
    "previous_resume_dat_path": ''
}


class Core(CorePluginBase):
    def enable(self):
        self.config = deluge.configmanager.ConfigManager("utorrentimport.conf",
                                                         DEFAULT_PREFS)
        self.torrent_manager = component.get("TorrentManager")

    def disable(self):
        pass

    def update(self):
        pass

    def on_torrent_folder_renamed(self, torrent_id, old, new):
        pass

    def on_torrent_file_renamed(self, torrent_id, index, name):
        pass

    #########
    #  Section: Utilities
    #########

    @export
    def get_default_resume_path(self):
        log.debug('Getting resume.dat path...')
        app_datas = []
        user_home = os.path.expanduser('~')
        if os.getenv('APPDATA'):
            app_datas.append(os.getenv('APPDATA'))
        app_datas.append(os.path.join(user_home, '.wine/drive_c/users', getuser(),
                                      'Application Data'))
        app_datas.append(os.path.join(user_home, 'Library', 'Application Support'))
        app_datas.append('/opt')
        app_datas.append(user_home)

        for app_data in app_datas:
            resume_path = os.path.join(app_data, 'uTorrent', 'resume.dat')
            if not os.path.exists(resume_path) or not os.path.isfile(resume_path):
                log.debug('no resume.dat found at {0}...'.format(app_data))

            else:
                log.debug('resume.dat found at {0}'.format(resume_path))
                return resume_path

        log.debug('no resume.dat could be found')
        return None

    def read_resume_data(self, path):
        try:
            with open(path, 'rb') as f:
                raw = f.read()
        except (IOError, OSError) as e:
            log.error('Could not open {0}. Reason{1}'.format(path, e))
            return None
        return bdecode(raw)

    def find_wine_drives(self):
        drives = os.path.join(os.path.expanduser('~'), '.wine/dosdevices')
        if os.path.isdir(drives):
            log.info('Found WINE drive mappings:')
            for drive in [d for d in os.listdir(drives)
                          if re.match('^[A-Z]:$', d, re.IGNORECASE)]:
                self.config['wine_drives'][drive.lower()] = os.path.relpath(
                    os.path.join(drives, drive))
                log.info("{0} => {1}".format(self.config['wine_drives'][drive.lower()]))
            self.config.save()

    def wine_path_check(self, path):
        mapped = path
        drive = re.match(r'^([A-Z]:)', path, re.IGNORECASE)
        try:
            if self.config['wine_drives'] and drive is not None:
                mapped = self.config['wine_drives'][drive.group(1).lower()] + path[2:]
        except KeyError:
            log.debug('No WINE mapping for drive {0}'.format(drive.group(1)))
        return mapped

    def resolve_path_renames(self, torrent_id, torrent_root, skip_recheck=False):
        """
        resolves issues stemming from utorrent renames not encoded into the torrent
        torrent_id: torrent_id
        torrent_root: what the torrent root should be (according to utorrent)
        """
        torrent = self.torrent_manager[torrent_id]
        files = torrent.get_files()
        if len(files) > 1:
            main_folder = files[0]['path'].split('/')[0]
            if main_folder != torrent_root:
                log.info(u'Renaming {0} => {1}'.format(main_folder,
                                                       torrent_root).encode('utf-8'))
                torrent.rename_folder(main_folder, torrent_root)

        else:
            main_file = files[0]['path']
            if main_file != torrent_root:
                log.info(u'Renaming {0} => {1}'.format(main_file,
                                                       torrent_root).encode('utf-8'))
                torrent.rename_files([(0, torrent_root)])

        if not skip_recheck:
            torrent.force_recheck()
            return

    #########
    #  Section: Public API
    #########

    @export
    def begin_import(self, resume_data=None, use_wine_mappings=False, skip_recheck=False):
        """
        attempts to add utorrent torrents to deluge
        resume_data: path to utorrent resume data
        """
        self.find_wine_drives()
        data = self.read_resume_data(resume_data)
        if not data:
            return None
        added = []
        failed = []
        with log:
            for torrent, info in data.iteritems():
                if torrent in self.config["torrent_blacklist"]:
                    log.debug('skipping {0}'.format(torrent))
                    continue
                torrent = os.path.abspath(os.path.join(os.path.dirname(resume_data),
                                                       torrent))

                try:
                    filedump = base64.encodestring(
                        open(unicode(torrent, 'utf-8'), 'rb').read())
                except IOError:
                    log.error('Could not open torrent {0}! skipping...'.format(torrent))
                    continue

                try:
                    ut_save_path = unicode(info['path'], 'utf-8')
                except UnicodeDecodeError:
                    ut_save_path = unicode(info['path'], 'latin-1')
                except TypeError:
                    pass

                torrent_root = os.path.basename(ut_save_path)
                deluge_storage_path = os.path.dirname(ut_save_path)

                if use_wine_mappings:
                    torrent_root = self.wine_path_check(torrent_root)
                    deluge_storage_path = self.wine_path_check(deluge_storage_path)

                log.info('Adding {0} to deluge.'.format(torrent_root))
                options = {'download_location': deluge_storage_path, 'add_paused': True}
                torrent_id = component.get("Core").add_torrent_file(torrent_root,
                                                                    filedump=filedump,
                                                                    options=options)

                if torrent_id is None:
                    log.info('FAILED: Could not add, may already exsist...'.format(
                        torrent))
                else:
                    log.info('SUCCESS!')
                    self.resolve_path_renames(torrent_id, torrent_root,
                                              skip_recheck=skip_recheck)
                    added.append(torrent_root)

        return added, failed

    @export
    def set_config(self, config):
        """Sets the config dictionary"""
        log.debug('updating config dictionary: {0}'.format(config))
        for key in config.keys():
            self.config[key] = config[key]
        self.config.save()

    @export
    def get_config(self):
        """Returns the config dictionary"""
        log.debug('{0}'.format(self.config.config))
        return self.config.config
