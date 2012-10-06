"""
Install server interfaces, for autotest client machine OS provisioning.
"""
import os, xmlrpclib, logging, time, commands, ConfigParser
from autotest.client.shared import error


def remove_hosts_file():
    """
    Remove the ssh known hosts file for a machine.

    Sometimes it is useful to have this done since on a test lab, SSH
    fingerprints of the test machines keep changing all the time due
    to frequent reinstalls.
    """
    known_hosts_file = "%s/.ssh/known_hosts" % os.getenv("HOME")
    if os.path.isfile(known_hosts_file):
        logging.debug("Deleting known hosts file %s", known_hosts_file)
        os.remove(known_hosts_file)


class CobblerInterface(object):
    """
    Implements interfacing with the Cobbler install server.

    @see: https://fedorahosted.org/cobbler/
    """
    def __init__(self, **kwargs):
        """
        Sets class attributes from the keyword arguments passed to constructor.

        @param **kwargs: Dict of keyword arguments passed to constructor.
        """
        self.xmlrpc_url = kwargs['xmlrpc_url']
        self.user = kwargs['user']
        self.password = kwargs['password']
        self.fallback_profile = kwargs['fallback_profile']
        if self.xmlrpc_url:
            self.server = xmlrpclib.Server(self.xmlrpc_url)
            self.token = self.server.login(self.user, self.password)
        self.num_attempts = int(kwargs.get('num_attempts', 2))


    def get_system_handle(self, host):
        """
        Get a system handle, needed to perform operations on the given host

        @param host: Host name

        @return: Tuple (system, system_handle)
        """
        try:
            system = self.server.find_system({"name" : host.hostname})[0]
        except IndexError, detail:
            ### TODO: Method to register this system as brand new
            logging.error("Error finding %s: %s", host.hostname, detail)
            raise ValueError("No system %s registered on install server" %
                             host.hostname)

        system_handle = self.server.get_system_handle(system, self.token)
        return (system, system_handle)


    def _set_host_profile(self, host, profile=''):
        system, system_handle = self.get_system_handle(host)
        system_info = self.server.get_system(system)

        # If no fallback profile is enabled, we don't want to mess
        # with the currently profile set for that machine.
        if profile:
            self.server.modify_system(system_handle, 'profile', profile,
                                      self.token)
            self.server.save_system(system_handle, self.token)

        # Enable netboot for that machine (next time it'll reboot and be
        # reinstalled)
        self.server.modify_system(system_handle, 'netboot_enabled', 'True',
                                  self.token)
        self.server.save_system(system_handle, self.token)
        try:
            # Cobbler only generates the DHCP configuration for netboot enabled
            # machines, so we need to synchronize the dhcpd file after changing
            # the value above
            self.server.sync_dhcp(self.token)
        except xmlrpclib.Fault, err:
            # older Cobbler will not recognize the above command
            if not "unknown remote method" in err.faultString:
                logging.error("DHCP sync failed, error code: %s, error string: %s",
                              err.faultCode, err.faultString)


    def install_host(self, host, profile='', timeout=None, num_attempts=2):
        """
        Install a host object with profile name defined by distro.

        @param host: Autotest host object.
        @param profile: String with cobbler profile name.
        @param timeout: Amount of time to wait for the install.
        @param num_attempts: Maximum number of install attempts.
        """
        if not self.xmlrpc_url:
            return

        installations_attempted = 1

        step_time = 60
        if timeout is None:
            # 1 hour of timeout by default
            timeout = 3600

        system, system_handle = self.get_system_handle(host)
        if not profile:
            profile = self.server.get_system(system).get('profile')
        if not profile:
            e_msg = 'Unable to determine profile for host %s' % host.hostname
            raise error.HostInstallProfileError(e_msg)

        host.record("START", None, "install", host.hostname)
        host.record("GOOD", None, "install.start", host.hostname)
        logging.info("Installing machine %s with profile %s (timeout %s s)",
                     host.hostname, profile, timeout)
        install_start = time.time()
        time_elapsed = 0

        install_successful = False
        while ((not install_successful) and
               (installations_attempted <= self.num_attempts) and
               (time_elapsed < timeout)):

            self._set_host_profile(host, profile)
            self.server.power_system(system_handle,
                                     'reboot', self.token)
            installations_attempted += 1

            while time_elapsed < timeout:

                time.sleep(step_time)

                # Cobbler signals that installation if finished by running
                # a %post script that unsets netboot_enabled. So, if it's
                # still set, installation has not finished. Loop and sleep.
                if not self.server.get_system(system).get('netboot_enabled'):
                    logging.debug('Cobbler got signaled that host %s '
                                  'installation is finished',
                                  host.hostname)
                    break

            # Check if the installed profile matches what we asked for
            installed_profile = self.server.get_system(system).get('profile')
            install_successful = (installed_profile == profile)

            if install_successful:
                logging.debug('Host %s installation successful', host.hostname)
                break
            else:
                logging.info('Host %s installation resulted in different '
                             'profile', host.hostname)

            time_elapsed = time.time() - install_start

        if not install_successful:
            e_msg = 'Host %s install timed out' % host.hostname
            host.record("END FAIL", None, "install", e_msg)
            raise error.HostInstallTimeoutError(e_msg)

        remove_hosts_file()
        host.wait_for_restart()
        host.record("END GOOD", None, "install", host.hostname)
        time_elapsed = time.time() - install_start
        logging.info("Machine %s installed successfuly after %d s (%d min)",
                     host.hostname, time_elapsed, time_elapsed/60)


    def power_host(self, host, state='reboot'):
        """
        Power on/off/reboot a host through cobbler.

        @param host: Autotest host object.
        @param state: Allowed states - one of 'on', 'off', 'reboot'.
        """
        if self.xmlrpc_url:
            system_handle = self.get_system_handle(host)[1]
            self.server.power_system(system_handle, state, self.token)

class DeployConfig():
    def __init__(self, name, cfg_file):
        self.cfg_file = cfg_file
        self.name = name

    def parse_entry(self, entry):
        if self.cfg.has_option(self.name, entry):
            return self.cfg.get(self.name, entry)
        else:
            return self.cfg.get('global', entry)

    def parse(self):
        self.cfg = ConfigParser.ConfigParser()
        self.cfg.read(self.cfg_file)

        self.server	= self.parse_entry('server')
        self.email	= self.parse_entry('email')
        self.ks 	= self.parse_entry('ks')
        self.ks_args 	= self.parse_entry('ks_args')
        self.image 	= self.parse_entry('image')
        self.console 	= self.parse_entry('console')
        self.kargs 	= self.parse_entry('kargs')
        self.extra_kargs= self.parse_entry('extra_kargs')


class ParallelsDeployInterface(object):
    """
    Implements interfacing with the parallels deploy server.

    @see: https://10.29.0.10
    """
    def __init__(self, **kwargs):
        """
        Sets class attributes from the keyword arguments passed to constructor.

        @param **kwargs: Dict of keyword arguments passed to constructor.
        """
        self.xmlrpc_url = kwargs['xmlrpc_url']
        self.user = kwargs['user']
        self.password = kwargs['password']
        self.fallback_profile = kwargs['fallback_profile']
        self.num_attempts = int(kwargs.get('num_attempts', 2))

    def install_host(self, host, profile='', timeout=None, num_attempts=2):
        """
        Install a host object with profile name defined by distro.

        @param host: Autotest host object.
        @param profile: String with cobbler profile name.
        @param timeout: Amount of time to wait for the install.
        @param num_attempts: Maximum number of install attempts.
        """
        if not self.xmlrpc_url:
            return
        try:
            self.dpcfg = DeployConfig(host.hostname, self.xmlrpc_url)
            self.dpcfg.parse()
        except:
            e_msg = 'Unable parse config file %s' % self.xmlrpc_url
            raise error.HostInstallProfileError(e_msg)

        installations_attempted = 0

        step_time = 60
        if timeout is None:
            # 1 hour of timeout by default
            timeout = 3600

        if not profile:
            profile = self.fallback_profile
        if not profile:
            e_msg = 'Unable to determine profile for host %s' % host.hostname
            raise error.HostInstallProfileError(e_msg)
        try:
            old_system_id = host.get_system_id()
        except (error.AutoservSSHTimeout, error.AutoservError):
            old_system_id = 'unknown systen_id before instalation'

        host.record("START", None, "install", host.hostname)
        host.record("GOOD", None, "install.start", host.hostname)
        logging.info("Installing machine %s with profile %s (timeout %s s) old_system_id %s",
                     host.hostname, profile, timeout, old_system_id)
        install_start = time.time()
        time_elapsed = 0
        
        server_opt = '--service %s --email %s ' % (self.dpcfg.server, self.dpcfg.email)
        dpctl_cmd = "dpctl.py list_servers %s --name %s" % (server_opt, host.hostname)
        (ret, output) = commands.getstatusoutput(dpctl_cmd)
        logging.debug('cmd:%s' % dpctl_cmd)
        logging.debug('ret:%d output:%s' % (ret, output))
        if ret != 0:
            e_msg = 'Unable to locate inventory id for %s' % host.hostname
            raise error.HostInstallProfileError(e_msg)
        invno = output

        install_successful = False
        while ((not install_successful) and
               (installations_attempted <= self.num_attempts) and
               (time_elapsed < timeout)):
            
            installations_attempted += 1

            # Explicitly reboot host and put it to known state
            host.hardreset(wait=False)

            attribute="--attribute='kargs=ks=%s %s %s %s %s' " % (
                self.dpcfg.ks, self.dpcfg.ks_args, self.dpcfg.console,
                self.dpcfg.kargs, self.dpcfg.extra_kargs)

            dpctl_cmd = "dpctl.py deploy_image %s -i %s -I %s %s --force --nowait" % (
                server_opt, self.dpcfg.image, invno, attribute)
            (ret, guid) = commands.getstatusoutput(dpctl_cmd)
            logging.debug('cmd:%s' % dpctl_cmd)
            logging.debug('ret:%d output:%s' % (ret, guid))
            if ret == 0:
                # Instalation start successfully
                dpctl_cmd = "dpctl.py trace_activity %s --guid=%s" % (
                    server_opt, guid)
                (ret, output) = commands.getstatusoutput(dpctl_cmd)
                logging.debug('cmd:%s' % dpctl_cmd)
                logging.debug('ret:%d output:%s' % (ret, output))
                if ret == 0:
                    try:
                        system_id = host.get_system_id()
                    except (error.AutoservSSHTimeout, error.AutoservError):
                        system_id = 'unknown systen_id after instalation'
                    if system_id == guid:
                        install_successful = True
                        logging.debug('Host %s installation successful system_id = %s',
                                      host.hostname, system_id)
                        break
                    else:
                        logging.info('Host %s installation resulted in different '
                                     'system_id = %s, expected = %s',
                                     host.hostname, system_id, guid)
            
            
            time_elapsed = time.time() - install_start

        if not install_successful:
            e_msg = 'Host %s install timed out' % host.hostname
            host.record("END FAIL", None, "install", e_msg)
            raise error.HostInstallTimeoutError(e_msg)

        host.record("END GOOD", None, "install", host.hostname)
        time_elapsed = time.time() - install_start
        logging.info("Machine %s installed successfuly after %d s (%d min)",
                     host.hostname, time_elapsed, time_elapsed/60)

