# -*- coding: utf-8 -*-
"""
OnionShare | https://onionshare.org/

Copyright (C) 2015 Micah Lee <micah@micahflee.com>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
import os, sys, subprocess, time, argparse, inspect, shutil, socket, threading, urllib2, httplib
import socks

import strings, helpers, web, hs

class OnionShare(object):
    def __init__(self, debug=False, local_only=False, stay_open=False, transparent_torification=False):
        self.port = None
        self.hs = None
        self.hidserv_dir = None

        # files and dirs to delete on shutdown
        self.cleanup_filenames = []

        # debug mode
        if debug:
            web.debug_mode()

        # do not use tor -- for development
        self.local_only = local_only

        # automatically close when download is finished
        self.stay_open = stay_open

        # traffic automatically goes through Tor
        self.transparent_torification = transparent_torification

    def choose_port(self):
        # let the OS choose a port
        tmpsock = socket.socket()
        tmpsock.bind(("127.0.0.1", 0))
        self.port = tmpsock.getsockname()[1]
        tmpsock.close()

    def start_hidden_service(self, gui=False):
        if not self.port:
            self.choose_port()

        if self.local_only:
            self.onion_host = '127.0.0.1:{0:d}'.format(self.port)
            return

        if not self.hs:
            self.hs = hs.HS(self.transparent_torification)

        self.onion_host = self.hs.start(self.port)

    def wait_for_hs(self):
        if self.local_only:
            return True

        print strings._('wait_for_hs')

        ready = False
        while not ready:
            try:
                sys.stdout.write('{0:s} '.format(strings._('wait_for_hs_trying')))
                sys.stdout.flush()

                if self.transparent_torification:
                    # no need to set the socks5 proxy
                    urllib2.urlopen('http://{0:s}'.format(self.onion_host))
                else:
                    tor_exists = False
                    ports = [9050, 9150]
                    for port in ports:
                        try:
                            s = socks.socksocket()
                            s.setproxy(socks.PROXY_TYPE_SOCKS5, '127.0.0.1', port)
                            s.connect((self.onion_host, 80))
                            s.close()
                            tor_exists = True
                            break
                        except socks.ProxyConnectionError:
                            pass
                    if not tor_exists:
                        raise NoTor(strings._("cant_connect_socksport").format(tor_socks_ports))
                ready = True

                sys.stdout.write('{0:s}\n'.format(strings._('wait_for_hs_yup')))
            except socks.SOCKS5Error:
                sys.stdout.write('{0:s}\n'.format(strings._('wait_for_hs_nope')))
                sys.stdout.flush()
            except urllib2.HTTPError:  # torification error
                sys.stdout.write('{0:s}\n'.format(strings._('wait_for_hs_nope')))
                sys.stdout.flush()
            except httplib.BadStatusLine: # torification (with bridge) error
                sys.stdout.write('{0:s}\n'.format(strings._('wait_for_hs_nope')))
                sys.stdout.flush()
            except KeyboardInterrupt:
                return False
        return True

    def cleanup(self):
        # cleanup files
        for filename in self.cleanup_filenames:
            if os.path.isfile(filename):
                os.remove(filename)
            elif os.path.isdir(filename):
                shutil.rmtree(filename)
        self.cleanup_filenames = []

        # call hs's cleanup
        self.hs.cleanup()


def main(cwd=None):
    strings.load_strings()

    # onionshare CLI in OSX needs to change current working directory (#132)
    if helpers.get_platform() == 'Darwin':
        if cwd:
            os.chdir(cwd)

    # parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--local-only', action='store_true', dest='local_only', help=strings._("help_local_only"))
    parser.add_argument('--stay-open', action='store_true', dest='stay_open', help=strings._("help_stay_open"))
    parser.add_argument('--transparent', action='store_true', dest='transparent_torification', help=strings._("help_transparent_torification"))
    parser.add_argument('--debug', action='store_true', dest='debug', help=strings._("help_debug"))
    parser.add_argument('filename', metavar='filename', nargs='+', help=strings._('help_filename'))
    args = parser.parse_args()

    filenames = args.filename
    for i in range(len(filenames)):
        filenames[i] = os.path.abspath(filenames[i])

    local_only = bool(args.local_only)
    debug = bool(args.debug)
    stay_open = bool(args.stay_open)
    transparent_torification = bool(args.transparent_torification)

    # validation
    valid = True
    for filename in filenames:
        if not os.path.exists(filename):
            print(strings._("not_a_file").format(filename))
            valid = False
    if not valid:
        sys.exit()

    # start the onionshare app
    try:
        app = OnionShare(debug, local_only, stay_open, transparent_torification)
        app.choose_port()
        print strings._("connecting_ctrlport").format(int(app.port))
        app.start_hidden_service()
    except hs.NoTor as e:
        sys.exit(e.args[0])
    except hs.HSDirError as e:
        sys.exit(e.args[0])

    # prepare files to share
    print strings._("preparing_files")
    web.set_file_info(filenames)
    app.cleanup_filenames.append(web.zip_filename)

    # warn about sending large files over Tor
    if web.zip_filesize >= 157286400:  # 150mb
        print ''
        print strings._("large_filesize")
        print ''

    # start onionshare service in new thread
    t = threading.Thread(target=web.start, args=(app.port, app.stay_open, app.transparent_torification))
    t.daemon = True
    t.start()

    try:  # Trap Ctrl-C
        # wait for hs
        ready = app.wait_for_hs()
        if not ready:
            sys.exit()

        print strings._("give_this_url")
        print 'http://{0:s}/{1:s}'.format(app.onion_host, web.slug)
        print ''
        print strings._("ctrlc_to_stop")

        # wait for app to close
        while t.is_alive():
            # t.join() can't catch KeyboardInterrupt in such as Ubuntu
            t.join(0.5)
    except KeyboardInterrupt:
        web.stop(app.port)
    finally:
        # shutdown
        app.cleanup()

if __name__ == '__main__':
    main()
