import gevent
import os
import subprocess

from ajenti.api import *
from ajenti.ui.binder import Binder
from ajenti.util import platform_select

from ajenti.plugins.services.api import ServiceMultiplexor
from ajenti.plugins.supervisor.client import SupervisorServiceManager
from ajenti.plugins.vh.api import MiscComponent, Restartable, SanityCheck
from ajenti.plugins.vh.extensions import BaseExtension

from reconfigure.configs import SupervisorConfig
from reconfigure.items.supervisor import ProgramData


class WebsiteProcess (object):
    def __init__(self, j={}):
        self.name = j.get('name', 'service')
        self.command = j.get('command', '')
        self.directory = j.get('directory', '')
        self.user = j.get('user', '')
        self.environment = j.get('environment', '')

    def save(self):
        return {
            'name': self.name,
            'command': self.command,
            'directory': self.directory,
            'user': self.user,
            'environment': self.environment,
        }


class ProcessTest (SanityCheck):
    def __init__(self, pid):
        SanityCheck.__init__(self)
        self.pid = pid
        self.type = _('Process')
        self.name = pid

    def check(self):
        s = SupervisorServiceManager.get().get_one(self.pid)
        if s:
            self.message = s.status
        return s and s.running


@plugin
class ProcessesExtension (BaseExtension):
    default_config = {
        'processes': [],
    }
    name = _('Processes')

    def init(self):
        self.append(self.ui.inflate('vh:ext-processes'))
        self.binder = Binder(self, self)
        self.find('processes').new_item = lambda c: WebsiteProcess()
        self.refresh()

    def refresh(self):
        self.processes = [WebsiteProcess(x) for x in self.config['processes']]
        self.binder.setup().populate()

    def update(self):
        self.binder.update()
        self.config['processes'] = [x.save() for x in self.processes]


@plugin
class Processes (MiscComponent):
    COMMENT = 'Autogenerated Ajenti V process'

    def create_configuration(self, config):
        self.checks = []

        sup = SupervisorConfig(path=platform_select(
            debian='/etc/supervisor/supervisord.conf',
            centos='/etc/supervisord.conf',
        ))
        sup.load()
        for p in sup.tree.programs:
            if p.comment and p.comment == self.COMMENT:
                sup.tree.programs.remove(p)

        for website in config.websites:
            if website.enabled:
                cfg = website.extension_configs.get(ProcessesExtension.classname) or {}
                for process in cfg.get('processes', []):
                    p = ProgramData()
                    p.comment = self.COMMENT
                    p.name = '%s-%s' % (website.slug, process['name'])
                    p.command = process['command']
                    p.environment = process['environment']
                    p.directory = process['directory'] or website.root
                    p.user = process['user'] or 'www-data'
                    sup.tree.programs.append(p)
                    self.checks.append(ProcessTest(p.name))

        sup.save()

    def apply_configuration(self):
        SupervisorRestartable.get().schedule()

    def get_checks(self):
        return self.checks


@plugin
class SupervisorRestartable (Restartable):
    def restart(self):
        s = ServiceMultiplexor.get().get_one(platform_select(
            debian='supervisor',
            centos='supervisord',
        ))
        if not s.running:
            s.start()
        else:
            subprocess.call(['supervisorctl', 'reload'])

        # Await restart
        retries = 10
        while retries:
            retries -= 1
            if subprocess.call(['supervisorctl', 'status']) == 0:
                break
            gevent.sleep(1)
